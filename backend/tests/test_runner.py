"""Tests for `app/analysis/runner.py`'s degraded-state handling and the local
graph build/persist step that now runs regardless of analyzer mode."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.analysis.errors import GraphUnavailableError
from app.analysis.runner import run_analysis
from app.core.settings import AgentSettings
from app.persistence import (
    bulk_insert_records,
    get_dossier,
    get_findings,
    init_normalized_table,
    init_registry,
    insert_dossier,
)


def _seed_dossier(db_path: Path, dossier_id: str) -> None:
    init_registry(db_path)
    insert_dossier(
        db_path,
        {
            "id": dossier_id,
            "name": "Test dossier",
            "status": "analyzing",
            "file_count": 1,
            "record_count": 1,
            "finding_count": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    init_normalized_table(db_path)
    normalized = {
        "record_id": "record-1",
        "source": {"file_id": "file-1", "relative_path": "Kreditoren/Buchungen.txt", "row_number": 2},
        "entities": [{"entity_type": "vendor", "entity_id": "200007", "label": None}],
        "relationships": {"paid_to": "200007"},
        "data": {"BUCHUNGSBETRAG": "9800,00"},
    }
    bulk_insert_records(
        db_path,
        [
            {
                "record_id": "record-1",
                "dossier_id": dossier_id,
                "file_id": "file-1",
                "record_type": "vendor_posting",
                "date": "2025-10-14",
                "amount": 9800.0,
                "currency": "EUR",
                "data_json": json.dumps(normalized),
            }
        ],
    )


def _latest_analysis_run(db_path: Path, dossier_id: str) -> dict:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT * FROM analysis_runs WHERE dossier_id = ? ORDER BY created_at DESC LIMIT 1",
            (dossier_id,),
        ).fetchone()
        assert row is not None
        return dict(row)
    finally:
        con.close()


def test_graph_is_built_and_persisted_in_demo_mode(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("FRAUD_AGENT_ENABLED", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    db_path = tmp_path / "registry.db"
    dossier_id = "dossier-demo"
    _seed_dossier(db_path, dossier_id)

    run_analysis(dossier_id, tmp_path / "workspace", db_path)

    con = sqlite3.connect(db_path)
    try:
        (count,) = con.execute(
            "SELECT COUNT(*) FROM process_graphs WHERE dossier_id = ?", (dossier_id,)
        ).fetchone()
    finally:
        con.close()
    assert count > 0

    row = get_dossier(db_path, dossier_id)
    assert row["status"] == "complete"


def test_agent_enabled_without_credentials_marks_analysis_incomplete(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FRAUD_AGENT_ENABLED", "true")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    db_path = tmp_path / "registry.db"
    dossier_id = "dossier-no-creds"
    _seed_dossier(db_path, dossier_id)

    run_analysis(dossier_id, tmp_path / "workspace", db_path)

    row = get_dossier(db_path, dossier_id)
    assert row["status"] == "analysis_incomplete"
    run_row = _latest_analysis_run(db_path, dossier_id)
    assert run_row["status"] == "unavailable"
    assert get_findings(db_path, dossier_id) == []


def test_model_failure_leaves_dossier_incomplete_with_no_partial_findings(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FRAUD_AGENT_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    db_path = tmp_path / "registry.db"
    dossier_id = "dossier-model-failure"
    _seed_dossier(db_path, dossier_id)

    def _boom(self, dossier_id, db_path):
        raise RuntimeError("simulated model failure")

    monkeypatch.setattr("app.analysis.runner.AnalysisPipeline.analyze", _boom)

    run_analysis(dossier_id, tmp_path / "workspace", db_path)

    row = get_dossier(db_path, dossier_id)
    assert row["status"] == "analysis_incomplete"
    assert get_findings(db_path, dossier_id) == []
    run_row = _latest_analysis_run(db_path, dossier_id)
    assert run_row["status"] == "error"


def test_model_call_cap_hit_completes_the_run_but_records_the_message(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FRAUD_AGENT_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    db_path = tmp_path / "registry.db"
    dossier_id = "dossier-cap-hit"
    _seed_dossier(db_path, dossier_id)

    class _CappedPipeline:
        def __init__(self, settings, *, graph=None, process_graphs=None) -> None:
            self.model_call_cap_hit = True
            self.cap_message = "Model-call cap (2) reached: 5 of 5 ledger entries..."

        def analyze(self, dossier_id, db_path):
            return []

    monkeypatch.setattr("app.analysis.runner.AnalysisPipeline", _CappedPipeline)

    run_analysis(dossier_id, tmp_path / "workspace", db_path)

    row = get_dossier(db_path, dossier_id)
    assert row["status"] == "complete"
    run_row = _latest_analysis_run(db_path, dossier_id)
    assert run_row["status"] == "complete"
    assert "cap" in run_row["error"].lower()
