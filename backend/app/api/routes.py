import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, UploadFile, File, HTTPException

from app.api.text_encoding import repair_legacy_mojibake
from app.ingestion import (
    Manifest,
    ManifestEntry,
    build_manifest,
    create_workspace,
    extract_zip,
    save_manifest,
)
from app.normalization import normalize_dossier
from app.models import (
    Dossier,
    DossierFile,
    Finding,
    ProcessingStatus,
)
from app.persistence import (
    get_all_dossiers,
    get_dossier,
    init_registry,
    insert_dossier,
    update_dossier_status,
)

router = APIRouter(prefix="/api")

RUNTIME_DIR = Path("runtime")
REGISTRY_DB = RUNTIME_DIR / "registry.db"
DOSSIERS_BASE = RUNTIME_DIR / "dossiers"


def set_runtime_dir(path: Path) -> None:
    global RUNTIME_DIR, REGISTRY_DB, DOSSIERS_BASE
    RUNTIME_DIR = path
    REGISTRY_DB = path / "registry.db"
    DOSSIERS_BASE = path / "dossiers"


def _ensure_registry() -> Path:
    init_registry(REGISTRY_DB)
    return REGISTRY_DB


def _dossier_name_from_filename(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    return stem.replace("_", " ").replace("-", " ").strip()


def _workspace_root(dossier_id: str) -> Path:
    return DOSSIERS_BASE / dossier_id


def _load_manifest(dossier_id: str) -> Manifest | None:
    manifest_path = _workspace_root(dossier_id) / "manifest.json"
    if not manifest_path.exists():
        return None
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    return Manifest.model_validate(data)


def _manifest_entry_to_dossier_file(entry: ManifestEntry) -> DossierFile:
    return DossierFile(
        file_id=entry.file_id,
        relative_path=entry.relative_path,
        original_name=entry.original_name,
        extension=entry.extension,
        mime_type=entry.mime_type,
        size_bytes=entry.size_bytes,
        classification=entry.classification,
        parse_status=entry.parse_status,
        normalized_record_count=entry.normalized_record_count,
    )


def _row_to_dossier(row: dict) -> Dossier:
    return Dossier(
        id=row["id"],
        name=row["name"],
        status=ProcessingStatus(row["status"]),
        file_count=row["file_count"],
        record_count=row["record_count"],
        finding_count=row["finding_count"],
        created_at=row["created_at"],
    )


def _require_dossier(dossier_id: str) -> dict:
    db = _ensure_registry()
    row = get_dossier(db, dossier_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Dossier not found")
    return row


# --- Routes ---


@router.post("/dossiers")
async def upload_dossier(background_tasks: BackgroundTasks, file: UploadFile = File(...)) -> Dossier:
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only ZIP archives are accepted.")

    db = _ensure_registry()
    dossier_id = str(uuid.uuid4())
    name = _dossier_name_from_filename(file.filename)
    created_at = datetime.now(timezone.utc).isoformat()

    workspace = create_workspace(dossier_id, base_dir=DOSSIERS_BASE)

    zip_dest = workspace.original / "upload.zip"
    with open(zip_dest, "wb") as dst:
        shutil.copyfileobj(file.file, dst)

    dossier_row = {
        "id": dossier_id,
        "name": name,
        "status": ProcessingStatus.extracting.value,
        "file_count": 0,
        "record_count": 0,
        "finding_count": 0,
        "created_at": created_at,
    }
    insert_dossier(db, dossier_row)

    result = await extract_zip(zip_dest, workspace.extracted)

    if not result.success:
        update_dossier_status(db, dossier_id, ProcessingStatus.error.value)
        raise HTTPException(status_code=422, detail=f"Extraction failed: {result.error}")

    update_dossier_status(db, dossier_id, ProcessingStatus.building_inventory.value)

    manifest = build_manifest(workspace.extracted, dossier_id)
    save_manifest(manifest, workspace.root / "manifest.json")

    update_dossier_status(
        db,
        dossier_id,
        ProcessingStatus.normalizing.value,
        file_count=manifest.entry_count,
    )

    manifest = normalize_dossier(
        extracted_dir=workspace.extracted,
        workspace_root=workspace.root,
        manifest=manifest,
        dossier_id=dossier_id,
    )

    from app.persistence import get_record_count
    record_count = get_record_count(REGISTRY_DB, dossier_id)

    # Analysis can call cloud services and is deliberately decoupled from upload.
    update_dossier_status(
        db, dossier_id, ProcessingStatus.analyzing.value, record_count=record_count
    )
    background_tasks.add_task(_run_dossier_analysis, dossier_id, workspace.root, REGISTRY_DB)

    row = get_dossier(db, dossier_id)
    return _row_to_dossier(row)


def _run_dossier_analysis(dossier_id: str, workspace_root: Path, db_path: Path) -> None:
    from app.analysis.runner import run_analysis

    run_analysis(dossier_id, workspace_root, db_path)


@router.post("/dossiers/{dossier_id}/analysis")
async def rerun_dossier_analysis(dossier_id: str, background_tasks: BackgroundTasks) -> dict:
    _require_dossier(dossier_id)
    workspace_root = _workspace_root(dossier_id)
    normalized_jsonl = workspace_root / "normalized" / "all_records.jsonl"
    if not normalized_jsonl.exists():
        raise HTTPException(status_code=409, detail="Dossier normalization is not available.")

    update_dossier_status(_ensure_registry(), dossier_id, ProcessingStatus.analyzing.value)
    background_tasks.add_task(_run_dossier_analysis, dossier_id, workspace_root, REGISTRY_DB)
    return {"status": ProcessingStatus.analyzing.value}


@router.get("/dossiers")
async def list_dossiers() -> list[Dossier]:
    db = _ensure_registry()
    rows = get_all_dossiers(db)
    return [_row_to_dossier(r) for r in rows]


@router.get("/dossiers/{dossier_id}")
async def get_dossier_route(dossier_id: str) -> Dossier:
    row = _require_dossier(dossier_id)
    return _row_to_dossier(row)


@router.get("/dossiers/{dossier_id}/status")
async def get_dossier_status(dossier_id: str) -> dict:
    row = _require_dossier(dossier_id)
    return {"status": row["status"]}


@router.get("/dossiers/{dossier_id}/files")
async def get_dossier_files(dossier_id: str) -> list[DossierFile]:
    _require_dossier(dossier_id)
    manifest = _load_manifest(dossier_id)
    if manifest is None:
        return []
    return [_manifest_entry_to_dossier_file(e) for e in manifest.entries]


@router.get("/dossiers/{dossier_id}/files/{file_id}/preview")
async def get_file_preview(
    dossier_id: str, file_id: str, limit: int = 100, offset: int = 0
) -> dict:
    _require_dossier(dossier_id)

    from app.persistence import get_records_by_file

    records = get_records_by_file(REGISTRY_DB, dossier_id, file_id, limit=limit, offset=offset)

    if not records:
        return {"type": "empty", "content": None}

    first = json.loads(records[0]["data_json"]) if records[0].get("data_json") else {}
    record_type = records[0].get("record_type", "")

    if record_type == "document_text":
        blocks = []
        for r in records:
            data = json.loads(r["data_json"]) if r.get("data_json") else {}
            blocks.append({
                "text": data.get("text_content", ""),
                "page": data.get("source", {}).get("page") if isinstance(data.get("source"), dict) else None,
                "paragraph": data.get("source", {}).get("paragraph") if isinstance(data.get("source"), dict) else None,
            })
        return repair_legacy_mojibake({"type": "text", "content": {"blocks": blocks}})

    # Tabular data
    if not first.get("data"):
        # Try parsing data_json directly
        headers: list[str] = []
        rows: list[list[str]] = []
        for r in records:
            data = json.loads(r["data_json"]) if r.get("data_json") else {}
            row_data = data.get("data", {})
            if not headers and row_data:
                headers = list(row_data.keys())
            rows.append([str(row_data.get(h, "")) for h in headers])
        return repair_legacy_mojibake(
            {"type": "table", "content": {"headers": headers, "rows": rows, "total": len(rows)}}
        )

    headers = list(first.get("data", {}).keys())
    rows = []
    for r in records:
        data = json.loads(r["data_json"]) if r.get("data_json") else {}
        row_data = data.get("data", {})
        rows.append([str(row_data.get(h, "")) for h in headers])
    return repair_legacy_mojibake(
        {"type": "table", "content": {"headers": headers, "rows": rows, "total": len(rows)}}
    )


@router.get("/dossiers/{dossier_id}/findings")
async def get_findings_route(dossier_id: str) -> list[Finding]:
    _require_dossier(dossier_id)
    from app.persistence import get_findings as db_get_findings

    findings_data = db_get_findings(REGISTRY_DB, dossier_id)
    return [Finding(**repair_legacy_mojibake(f)) for f in findings_data]


@router.get("/dossiers/{dossier_id}/findings/{finding_id}")
async def get_finding_route(dossier_id: str, finding_id: str) -> Finding:
    _require_dossier(dossier_id)
    from app.persistence import get_finding as db_get_finding

    finding_data = db_get_finding(REGISTRY_DB, dossier_id, finding_id)
    if finding_data is None:
        raise HTTPException(status_code=404, detail="Finding not found")
    return Finding(**repair_legacy_mojibake(finding_data))
