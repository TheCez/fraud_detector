import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport

from app.api import routes
from app.api.routes import set_runtime_dir
from app.main import app
from app.persistence import (
    bulk_insert_records,
    init_normalized_table,
    init_registry,
    insert_dossier,
)

SAMPLE_ZIP = Path(__file__).resolve().parent.parent.parent / "sample_data" / "Uebungsdaten_Muster_Verpackungen.zip"


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path):
    set_runtime_dir(tmp_path)
    yield
    set_runtime_dir(Path("runtime"))


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_health(client: AsyncClient):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_upload_rejects_non_zip(client: AsyncClient):
    r = await client.post(
        "/api/dossiers",
        files={"file": ("test.txt", b"hello", "text/plain")},
    )
    assert r.status_code == 400
    assert "ZIP" in r.json()["detail"]


async def test_upload_rejects_invalid_zip(client: AsyncClient):
    r = await client.post(
        "/api/dossiers",
        files={"file": ("test.zip", b"not a zip at all", "application/zip")},
    )
    assert r.status_code == 422
    assert "Extraction failed" in r.json()["detail"]


@pytest.mark.skipif(not SAMPLE_ZIP.exists(), reason="sample ZIP not available")
async def test_upload_real_zip(client: AsyncClient, tmp_path):
    with open(SAMPLE_ZIP, "rb") as f:
        r = await client.post(
            "/api/dossiers",
            files={"file": (SAMPLE_ZIP.name, f, "application/zip")},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "analyzing"
    assert data["file_count"] > 0
    assert "id" in data
    assert data["name"] == "Uebungsdaten Muster Verpackungen"

    dossier_id = data["id"]
    workspace_root = tmp_path / "dossiers" / dossier_id
    assert workspace_root.exists()
    assert (workspace_root / "original" / "upload.zip").exists()
    assert (workspace_root / "manifest.json").exists()


@pytest.mark.skipif(not SAMPLE_ZIP.exists(), reason="sample ZIP not available")
async def test_get_dossier_after_upload(client: AsyncClient):
    with open(SAMPLE_ZIP, "rb") as f:
        upload_resp = await client.post(
            "/api/dossiers",
            files={"file": (SAMPLE_ZIP.name, f, "application/zip")},
        )
    dossier_id = upload_resp.json()["id"]

    r = await client.get(f"/api/dossiers/{dossier_id}")
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == dossier_id
    assert data["status"] == "complete"
    assert data["file_count"] > 0


@pytest.mark.skipif(not SAMPLE_ZIP.exists(), reason="sample ZIP not available")
async def test_get_files_after_upload(client: AsyncClient):
    with open(SAMPLE_ZIP, "rb") as f:
        upload_resp = await client.post(
            "/api/dossiers",
            files={"file": (SAMPLE_ZIP.name, f, "application/zip")},
        )
    dossier_id = upload_resp.json()["id"]

    r = await client.get(f"/api/dossiers/{dossier_id}/files")
    assert r.status_code == 200
    files = r.json()
    assert len(files) > 0
    first = files[0]
    assert "file_id" in first
    assert "relative_path" in first
    assert "mime_type" in first
    assert "classification" in first


@pytest.mark.skipif(not SAMPLE_ZIP.exists(), reason="sample ZIP not available")
async def test_source_preview_preserves_german_utf8_headers(client: AsyncClient):
    with open(SAMPLE_ZIP, "rb") as f:
        upload_resp = await client.post(
            "/api/dossiers",
            files={"file": (SAMPLE_ZIP.name, f, "application/zip")},
        )
    dossier_id = upload_resp.json()["id"]

    files_resp = await client.get(f"/api/dossiers/{dossier_id}/files")
    source_file = next(
        file
        for file in files_resp.json()
        if file["relative_path"].endswith("Kreditoren/Lieferantenbuchungen.txt")
    )

    preview_resp = await client.get(
        f"/api/dossiers/{dossier_id}/files/{source_file['file_id']}/preview"
    )
    assert preview_resp.status_code == 200
    headers = preview_resp.json()["content"]["headers"]
    assert "BUCHUNGSWÄHRUNG" in headers
    assert all("Ã" not in header for header in headers)


async def test_source_preview_repairs_legacy_mojibake(client: AsyncClient):
    dossier_id = "legacy-encoding"
    db_path = routes.REGISTRY_DB
    init_registry(db_path)
    init_normalized_table(db_path)
    insert_dossier(
        db_path,
        {
            "id": dossier_id,
            "name": "legacy",
            "status": "complete",
            "file_count": 1,
            "record_count": 1,
            "finding_count": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    bulk_insert_records(
        db_path,
        [
            {
                "record_id": "legacy-record",
                "dossier_id": dossier_id,
                "file_id": "legacy-file",
                "record_type": "vendor_posting",
                "date": None,
                "amount": None,
                "currency": None,
                "data_json": json.dumps(
                    {"data": {"BUCHUNGSWÃ\u0084HRUNG": "EUR"}},
                    ensure_ascii=False,
                ),
                "data_json": json.dumps(
                    {"data": {"BUCHUNGSW" + chr(0xC3) + chr(0x84) + "HRUNG": "EUR"}},
                    ensure_ascii=False,
                ),
            }
        ],
    )

    preview_resp = await client.get(
        f"/api/dossiers/{dossier_id}/files/legacy-file/preview"
    )
    assert preview_resp.status_code == 200
    assert preview_resp.json()["content"]["headers"] == ["BUCHUNGSWÄHRUNG"]


@pytest.mark.skipif(not SAMPLE_ZIP.exists(), reason="sample ZIP not available")
async def test_get_status_after_upload(client: AsyncClient):
    with open(SAMPLE_ZIP, "rb") as f:
        upload_resp = await client.post(
            "/api/dossiers",
            files={"file": (SAMPLE_ZIP.name, f, "application/zip")},
        )
    dossier_id = upload_resp.json()["id"]

    r = await client.get(f"/api/dossiers/{dossier_id}/status")
    assert r.status_code == 200
    assert r.json()["status"] == "complete"


@pytest.mark.skipif(not SAMPLE_ZIP.exists(), reason="sample ZIP not available")
async def test_findings_require_existing_dossier(client: AsyncClient):
    with open(SAMPLE_ZIP, "rb") as f:
        upload_resp = await client.post(
            "/api/dossiers",
            files={"file": (SAMPLE_ZIP.name, f, "application/zip")},
        )
    dossier_id = upload_resp.json()["id"]

    r = await client.get(f"/api/dossiers/{dossier_id}/findings")
    assert r.status_code == 200
    findings = r.json()
    assert len(findings) == 4
    assert findings[0]["finding_id"] == "F1"


async def test_get_nonexistent_dossier_404(client: AsyncClient):
    r = await client.get("/api/dossiers/nonexistent-id")
    assert r.status_code == 404


async def test_get_nonexistent_dossier_status_404(client: AsyncClient):
    r = await client.get("/api/dossiers/nonexistent-id/status")
    assert r.status_code == 404


async def test_get_nonexistent_dossier_files_404(client: AsyncClient):
    r = await client.get("/api/dossiers/nonexistent-id/files")
    assert r.status_code == 404


async def test_get_nonexistent_dossier_findings_404(client: AsyncClient):
    r = await client.get("/api/dossiers/nonexistent-id/findings")
    assert r.status_code == 404


@pytest.mark.skipif(not SAMPLE_ZIP.exists(), reason="sample ZIP not available")
async def test_get_finding_detail(client: AsyncClient):
    with open(SAMPLE_ZIP, "rb") as f:
        upload_resp = await client.post(
            "/api/dossiers",
            files={"file": (SAMPLE_ZIP.name, f, "application/zip")},
        )
    dossier_id = upload_resp.json()["id"]

    r = await client.get(f"/api/dossiers/{dossier_id}/findings/F1")
    assert r.status_code == 200
    finding = r.json()
    assert "209101" in finding["title"] or "shell" in finding["title"].lower()
    assert finding["severity"] == "critical"
    assert finding["amount_at_risk"] is not None
    assert len(finding["evidence"]) >= 3


@pytest.mark.skipif(not SAMPLE_ZIP.exists(), reason="sample ZIP not available")
async def test_get_finding_not_found(client: AsyncClient):
    with open(SAMPLE_ZIP, "rb") as f:
        upload_resp = await client.post(
            "/api/dossiers",
            files={"file": (SAMPLE_ZIP.name, f, "application/zip")},
        )
    dossier_id = upload_resp.json()["id"]

    r = await client.get(f"/api/dossiers/{dossier_id}/findings/INVALID")
    assert r.status_code == 404


@pytest.mark.skipif(not SAMPLE_ZIP.exists(), reason="sample ZIP not available")
async def test_list_dossiers(client: AsyncClient):
    with open(SAMPLE_ZIP, "rb") as f:
        await client.post(
            "/api/dossiers",
            files={"file": (SAMPLE_ZIP.name, f, "application/zip")},
        )

    r = await client.get("/api/dossiers")
    assert r.status_code == 200
    dossiers = r.json()
    assert len(dossiers) == 1
    assert dossiers[0]["status"] == "complete"
