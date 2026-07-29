import json
from pathlib import Path

from app.analysis.agent_analyzer import AgentAnalyzer, ProposedFinding
from app.analysis.graph import EvidenceRecordStore
from app.persistence import bulk_insert_records, init_normalized_table


def _record(record_id: str, dossier_id: str) -> dict:
    normalized = {
        "record_id": record_id,
        "source": {
            "file_id": "file-1",
            "relative_path": "Kreditoren/Buchungen.txt",
            "row_number": 4,
        },
        "data": {"BETRAG": "9800,00", "KREDITOR": "200007"},
    }
    return {
        "record_id": record_id,
        "dossier_id": dossier_id,
        "file_id": "file-1",
        "record_type": "vendor_posting",
        "date": "2025-10-14",
        "amount": 9800.0,
        "currency": "EUR",
        "data_json": json.dumps(normalized),
    }


def test_evidence_store_rejects_a_record_from_another_dossier(tmp_path: Path):
    db_path = tmp_path / "registry.db"
    init_normalized_table(db_path)
    bulk_insert_records(db_path, [_record("record-a", "dossier-a"), _record("record-b", "dossier-b")])

    records = EvidenceRecordStore("dossier-a", db_path).resolve(["record-a", "record-b"])

    assert [record["record_id"] for record in records] == ["record-a"]


def test_agent_rebuilds_evidence_from_local_record_not_model_text(tmp_path: Path):
    db_path = tmp_path / "registry.db"
    init_normalized_table(db_path)
    bulk_insert_records(db_path, [_record("record-a", "dossier-a")])
    proposal = ProposedFinding(
        title="Potential payment splitting pattern",
        severity="high",
        category="control_override",
        explanation="Several payments need review.",
        reasoning="The source record is near the approval threshold.",
        confidence="medium",
        record_ids=["record-a"],
    )

    findings = AgentAnalyzer._validate_and_build_findings(
        "dossier-a", EvidenceRecordStore("dossier-a", db_path), [proposal]
    )

    assert len(findings) == 1
    assert findings[0].status == "needs_review"
    assert findings[0].evidence[0].record_id == "record-a"
    assert "KREDITOR" in findings[0].evidence[0].excerpt


def test_agent_discards_proposal_with_unknown_evidence(tmp_path: Path):
    db_path = tmp_path / "registry.db"
    init_normalized_table(db_path)
    proposal = ProposedFinding(
        title="Potential unsupported activity",
        severity="medium",
        category="review",
        explanation="This should not be persisted.",
        reasoning="The record does not exist.",
        confidence="low",
        record_ids=["missing-record"],
    )

    findings = AgentAnalyzer._validate_and_build_findings(
        "dossier-a", EvidenceRecordStore("dossier-a", db_path), [proposal]
    )

    assert findings == []
