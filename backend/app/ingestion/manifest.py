import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from app.models.schemas import FileClassification, ParseStatus


ACCOUNTING_FOLDERS = {"sachkonten", "kreditoren", "debitoren", "av", "steuercodes"}

MIME_MAP: dict[str, str] = {
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pdf": "application/pdf",
    ".xml": "application/xml",
    ".dtd": "application/xml-dtd",
}


class ManifestEntry(BaseModel):
    file_id: str
    relative_path: str
    original_name: str
    extension: str
    mime_type: str
    size_bytes: int
    sha256: str
    classification: FileClassification
    parse_status: ParseStatus
    parser: str | None = None
    normalized_record_count: int = 0
    excluded_from_analysis: bool = False
    exclusion_reason: str | None = None
    parse_error: str | None = None


class Manifest(BaseModel):
    dossier_id: str
    created_at: str
    entry_count: int
    total_size_bytes: int
    entries: list[ManifestEntry]


def _compute_sha256(file_path: Path) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _generate_file_id(dossier_id: str, relative_path: str) -> str:
    namespace = uuid.uuid5(uuid.NAMESPACE_URL, f"dossier:{dossier_id}")
    return str(uuid.uuid5(namespace, relative_path))


def _detect_mime_type(extension: str) -> str:
    return MIME_MAP.get(extension, "application/octet-stream")


def _classify_file(relative_path: str, extension: str, size_bytes: int) -> tuple[FileClassification, bool, str | None]:
    parts = Path(relative_path).parts
    parent_folders = {p.lower() for p in parts[:-1]}
    filename = Path(relative_path).name.lower()

    # Technical metadata (index.xml, the GDPdU DTD) is a schema input, not analyzable
    # evidence: it is always excluded from analysis, independent of file size. Per
    # agents/PROJECT_SPEC.md, it is never deleted - only kept out of normalization and
    # the graph. The file stays on disk and index.xml is still read directly by
    # gdpdu_txt.parse_gdpdu_folder for column definitions; "excluded from analysis"
    # means "never emitted as normalized records", not "unreadable".
    if filename == "index.xml" or extension == ".dtd":
        return (
            FileClassification.technical_metadata,
            True,
            "technical metadata (GDPdU schema definition) - retained for reproducibility, excluded from analysis",
        )

    excluded = False
    exclusion_reason: str | None = None

    if size_bytes == 0:
        excluded = True
        exclusion_reason = "empty file"

    if extension in (".txt", ".csv") and parent_folders & ACCOUNTING_FOLDERS:
        return FileClassification.evidence, excluded, exclusion_reason

    if extension in (".xlsx", ".docx", ".pdf", ".csv"):
        return FileClassification.supporting, excluded, exclusion_reason

    if extension == ".xml":
        return FileClassification.supporting, excluded, exclusion_reason

    if extension == ".txt":
        return FileClassification.supporting, excluded, exclusion_reason

    return FileClassification.supporting, excluded, exclusion_reason


def build_manifest(extracted_dir: Path, dossier_id: str) -> Manifest:
    entries: list[ManifestEntry] = []

    for file_path in sorted(extracted_dir.rglob("*")):
        if not file_path.is_file():
            continue

        relative_path = str(file_path.relative_to(extracted_dir)).replace("\\", "/")
        extension = file_path.suffix.lower()
        size_bytes = file_path.stat().st_size

        classification, excluded, exclusion_reason = _classify_file(
            relative_path, extension, size_bytes
        )

        entry = ManifestEntry(
            file_id=_generate_file_id(dossier_id, relative_path),
            relative_path=relative_path,
            original_name=file_path.name,
            extension=extension,
            mime_type=_detect_mime_type(extension),
            size_bytes=size_bytes,
            sha256=_compute_sha256(file_path),
            classification=classification,
            parse_status=ParseStatus.pending,
            excluded_from_analysis=excluded,
            exclusion_reason=exclusion_reason,
        )
        entries.append(entry)

    total_size = sum(e.size_bytes for e in entries)

    return Manifest(
        dossier_id=dossier_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        entry_count=len(entries),
        total_size_bytes=total_size,
        entries=entries,
    )


def save_manifest(manifest: Manifest, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        manifest.model_dump_json(indent=2),
        encoding="utf-8",
    )
