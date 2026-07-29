import io
import json
from pathlib import Path

from app.analysis.graph import CogneeCloudGraph
from app.core.settings import AgentSettings


def _settings() -> AgentSettings:
    return AgentSettings(
        cognee_api_key="test-key",
        cognee_service_url="https://tenant.example",
        openai_api_key="openai-key",
        openai_model="gpt-5.4",
        agent_enabled=True,
    )


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return io.BytesIO(json.dumps(self.payload).encode())

    def __exit__(self, *_):
        return False


def test_ingest_uses_remote_remember_endpoint(monkeypatch, tmp_path: Path):
    captured = {}
    monkeypatch.setattr(
        "app.analysis.graph.urlopen",
        lambda request, timeout: captured.update(request=request, timeout=timeout) or _Response({"ok": True}),
    )
    source = tmp_path / "all_records.jsonl"
    source.write_text(
        '{"record_id":"record-1","record_type":"invoice","source":{"relative_path":"invoices.csv"},"data":{}}\n',
        encoding="utf-8",
    )

    result = CogneeCloudGraph(_settings()).ingest("dossier-1", source)

    request = captured["request"]
    assert request.full_url == "https://tenant.example/api/v1/remember"
    assert request.get_header("X-api-key") == "test-key"
    assert b'name="datasetName"' in request.data
    assert b"fraud-dossier-dossier-1" in request.data
    assert b'filename="normalized-dossier.csv"' in request.data
    assert b'record-1,invoice' in request.data
    assert result.dataset_name == "fraud-dossier-dossier-1"


def test_normalized_cloud_payload_excludes_provenance_and_empty_fields(tmp_path: Path):
    source = tmp_path / "all_records.jsonl"
    source.write_text(
        json.dumps({
            "record_id": "record-1", "dossier_id": "dossier-1", "record_type": "invoice",
            "source": {"relative_path": "private.csv", "row_number": 4},
            "entities": [{"entity_type": "vendor", "entity_id": "209101", "label": None}],
            "relationships": {"paid_to": "209101"},
            "data": {"invoice_number": "INV-1", "unused": None},
            "date": "2025-10-14", "period": None, "amount": 100.0, "currency": "EUR",
            "text_content": None,
        }) + "\n",
        encoding="utf-8",
    )

    payload = CogneeCloudGraph._normalized_cloud_payload(source).decode()

    assert 'private.csv' not in payload
    assert 'dossier-1' not in payload
    assert '"unused"' not in payload
    assert 'entity_type"": ""vendor' in payload
    assert 'entity_id"": ""209101' in payload


def test_normalized_cloud_payload_keeps_only_business_tabular_and_text_records(tmp_path: Path):
    source = tmp_path / "all_records.jsonl"
    source.write_text(
        "\n".join(
            (
                json.dumps({"record_id": "csv", "record_type": "invoice", "source": {"relative_path": "invoices.csv"}}),
                json.dumps({"record_id": "xlsx", "record_type": "permission", "source": {"relative_path": "roles.xlsx"}}),
                json.dumps({"record_id": "txt", "record_type": "vendor_posting", "source": {"relative_path": "vendor.txt"}}),
            )
        ) + "\n",
        encoding="utf-8",
    )

    payload = CogneeCloudGraph._normalized_cloud_payload(source).decode()

    assert '\r\ncsv,invoice,' in payload
    assert '\r\nxlsx,permission,' in payload
    assert '\r\ntxt,vendor_posting,' in payload


def test_recall_uses_remote_dataset_scoped_api(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.analysis.graph.urlopen",
        lambda request, timeout: captured.update(request=request, timeout=timeout)
        or _Response([{"record_id": "dbeb1eb1-40eb-4b45-bdb6-95cb3f934790"}]),
    )

    record_ids = CogneeCloudGraph(_settings()).recall_record_ids("dossier-1", "Find anomalies")

    request = captured["request"]
    assert request.full_url == "https://tenant.example/api/v1/recall"
    assert json.loads(request.data) == {
        "query": "Find anomalies",
        "datasets": ["fraud-dossier-dossier-1"],
        "searchType": "GRAPH_COMPLETION",
        "topK": 50,
        "includeReferences": True,
        "scope": "graph",
    }
    assert record_ids == ["dbeb1eb1-40eb-4b45-bdb6-95cb3f934790"]


def test_forget_deletes_temporary_dataset(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.analysis.graph.urlopen",
        lambda request, timeout: captured.update(request=request, timeout=timeout) or _Response({"ok": True}),
    )

    CogneeCloudGraph(_settings()).forget_dataset("fraud-dossier-dossier-1")

    request = captured["request"]
    assert request.full_url == "https://tenant.example/api/v1/forget"
    assert json.loads(request.data) == {"dataset": "fraud-dossier-dossier-1"}
