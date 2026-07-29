"""Tests for the graph-traversing analyzer (`app/analysis/graph_analyzer.py`).

Never makes a live model call - the tool-calling and proposer models are
stand-ins exposing only `.invoke(messages)`, exercised through the real
LangGraph workflow so the step-budget and cap enforcement are genuinely
verified rather than assumed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from app.analysis.errors import GraphUnavailableError
from app.analysis.graph_analyzer import (
    GraphAnalyzer,
    ProposedFinding,
    ProposedFindingBatch,
    _build_traversal_graph,
)
from app.analysis.prefilter import Candidate
from app.core.settings import AgentSettings
from app.evidence import EvidenceRecordStore
from app.graph.subgraphs import ProcessGraph
from app.persistence import bulk_insert_records, init_normalized_table


def _settings(**overrides) -> AgentSettings:
    defaults = dict(openai_api_key="test-key", openai_model="gpt-test", agent_enabled=True, model_call_cap=100)
    defaults.update(overrides)
    return AgentSettings(**defaults)


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


def _fake_process_graph(graph_id: str, record_ids: tuple[str, ...]) -> ProcessGraph:
    return ProcessGraph(
        graph_id=graph_id,
        record_ids=record_ids,
        entity_node_ids=(),
        source_node_ids=(),
        sink_node_ids=(),
        record_count=len(record_ids),
        capped=False,
        had_cycle=False,
    )


# ---------------------------------------------------------------------------
# Trust boundary: evidence store + validate_and_build_findings
# ---------------------------------------------------------------------------


def test_evidence_store_rejects_a_record_from_another_dossier(tmp_path: Path):
    db_path = tmp_path / "registry.db"
    init_normalized_table(db_path)
    bulk_insert_records(db_path, [_record("record-a", "dossier-a"), _record("record-b", "dossier-b")])

    records = EvidenceRecordStore("dossier-a", db_path).resolve(["record-a", "record-b"])

    assert [record["record_id"] for record in records] == ["record-a"]


def test_rebuilds_evidence_from_local_record_not_model_text_and_sets_graph_id(tmp_path: Path):
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

    findings = GraphAnalyzer._validate_and_build_findings(
        "dossier-a", EvidenceRecordStore("dossier-a", db_path), "PG-1", [proposal]
    )

    assert len(findings) == 1
    assert findings[0].status == "needs_review"
    assert findings[0].graph_id == "PG-1"
    assert findings[0].evidence[0].record_id == "record-a"
    assert "KREDITOR" in findings[0].evidence[0].excerpt


def test_discards_proposal_citing_unresolvable_record_id(tmp_path: Path):
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

    findings = GraphAnalyzer._validate_and_build_findings(
        "dossier-a", EvidenceRecordStore("dossier-a", db_path), "PG-1", [proposal]
    )

    assert findings == []


def test_discards_proposal_citing_a_foreign_dossier_record_id(tmp_path: Path):
    db_path = tmp_path / "registry.db"
    init_normalized_table(db_path)
    bulk_insert_records(db_path, [_record("record-a", "dossier-b")])
    proposal = ProposedFinding(
        title="Potential unsupported activity",
        severity="medium",
        category="review",
        explanation="This should not be persisted.",
        reasoning="The record belongs to a different dossier.",
        confidence="low",
        record_ids=["record-a"],
    )

    findings = GraphAnalyzer._validate_and_build_findings(
        "dossier-a", EvidenceRecordStore("dossier-a", db_path), "PG-1", [proposal]
    )

    assert findings == []


def test_discards_the_whole_proposal_even_if_only_one_of_several_ids_is_bad(tmp_path: Path):
    db_path = tmp_path / "registry.db"
    init_normalized_table(db_path)
    bulk_insert_records(db_path, [_record("record-a", "dossier-a")])
    proposal = ProposedFinding(
        title="Mixed valid and invalid citation",
        severity="medium",
        category="review",
        explanation="One id is real, one is not.",
        reasoning="Whole-proposal rejection must not partially accept this.",
        confidence="low",
        record_ids=["record-a", "record-does-not-exist"],
    )

    findings = GraphAnalyzer._validate_and_build_findings(
        "dossier-a", EvidenceRecordStore("dossier-a", db_path), "PG-1", [proposal]
    )

    assert findings == []


# ---------------------------------------------------------------------------
# Step budget: enforced by the traversal graph's own routing, not the model
# ---------------------------------------------------------------------------


class _AlwaysToolCallModel:
    """Requests one more tool call every turn, forever, unless stopped."""

    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        return AIMessage(
            content="",
            tool_calls=[{"name": "absence_check", "args": {"node_id": "vendor:1", "expected_edge_type": "has_receipt"}, "id": f"call-{self.calls}"}],
        )


class _RecordingProposerModel:
    def __init__(self, batch: ProposedFindingBatch) -> None:
        self.batch = batch
        self.invocations = 0

    def invoke(self, messages):
        self.invocations += 1
        return self.batch


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0

    def invoke(self, args):
        self.calls += 1
        return {"echo": args}


def test_step_budget_bounds_tool_calls_regardless_of_how_many_the_model_requests():
    tool_model = _AlwaysToolCallModel()
    proposer_model = _RecordingProposerModel(ProposedFindingBatch(findings=[]))
    tools_by_name = {"absence_check": _FakeTool("absence_check")}
    step_budget = 3

    workflow = _build_traversal_graph(tool_model, proposer_model, tools_by_name, step_budget)
    result = workflow.invoke(
        {"messages": [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}], "tool_calls_used": 0},
        config={"recursion_limit": step_budget * 3 + 10},
    )

    assert result["tool_calls_used"] == step_budget
    assert tools_by_name["absence_check"].calls == step_budget
    # The model is asked at most once more than the budget to learn the budget was hit.
    assert tool_model.calls <= step_budget + 1
    assert proposer_model.invocations == 1


def test_traversal_finalizes_immediately_when_the_model_makes_no_tool_calls():
    class _NoToolCallsModel:
        def invoke(self, messages):
            return AIMessage(content="nothing to investigate further")

    proposer_model = _RecordingProposerModel(
        ProposedFindingBatch(
            findings=[
                ProposedFinding(
                    title="Round-amount invoice pattern",
                    severity="medium",
                    category="review",
                    explanation="Explanation text.",
                    reasoning="Reasoning text.",
                    confidence="low",
                    record_ids=["record-a"],
                )
            ]
        )
    )

    workflow = _build_traversal_graph(_NoToolCallsModel(), proposer_model, {}, step_budget=6)
    result = workflow.invoke(
        {"messages": [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}], "tool_calls_used": 0},
        config={"recursion_limit": 30},
    )

    assert result["tool_calls_used"] == 0
    assert len(result["proposals"]) == 1
    assert proposer_model.invocations == 1


# ---------------------------------------------------------------------------
# GraphAnalyzer.analyze: configuration, cap enforcement and recording
# ---------------------------------------------------------------------------


def test_analyze_raises_graph_unavailable_error_when_agent_enabled_without_credentials(tmp_path: Path):
    db_path = tmp_path / "registry.db"
    init_normalized_table(db_path)
    settings = _settings(openai_api_key=None)

    with pytest.raises(GraphUnavailableError):
        GraphAnalyzer(settings).analyze("dossier-a", db_path)


def test_model_call_cap_truncates_candidates_and_records_the_cap_hit(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "registry.db"
    init_normalized_table(db_path)

    fake_candidates = [
        Candidate(
            graph=_fake_process_graph(f"PG-{i}", (f"rec-{i}",)),
            reasons=("test reason",),
            signals=("missing_receipt",),
        )
        for i in range(5)
    ]
    monkeypatch.setattr(
        "app.analysis.graph_analyzer.select_candidate_graphs", lambda dossier_id, db_path: fake_candidates
    )
    monkeypatch.setattr("app.analysis.graph_analyzer.load_process_graphs", lambda db_path, dossier_id: fake_candidates)

    proposer_model = _RecordingProposerModel(ProposedFindingBatch(findings=[]))

    class _NoToolCallsModel:
        def __init__(self) -> None:
            self.invocations = 0

        def invoke(self, messages):
            self.invocations += 1
            return AIMessage(content="no tool calls")

    tool_model = _NoToolCallsModel()
    monkeypatch.setattr(GraphAnalyzer, "_build_models", lambda self, tools: (tool_model, proposer_model))

    settings = _settings(model_call_cap=2)
    analyzer = GraphAnalyzer(settings)
    findings = analyzer.analyze("dossier-a", db_path)

    assert findings == []
    assert analyzer.candidate_graphs == 5
    assert analyzer.analyzed_graphs == 2
    assert analyzer.model_call_cap_hit is True
    assert "2" in analyzer.cap_message and "5" in analyzer.cap_message
    # Proof the cap actually stopped work, not just that the stats say so.
    assert proposer_model.invocations == 2
    assert tool_model.invocations == 2


def test_no_cap_hit_when_candidates_are_within_the_cap(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "registry.db"
    init_normalized_table(db_path)

    fake_candidates = [
        Candidate(
            graph=_fake_process_graph("PG-0", ("rec-0",)),
            reasons=("test reason",),
            signals=("missing_receipt",),
        )
    ]
    monkeypatch.setattr(
        "app.analysis.graph_analyzer.select_candidate_graphs", lambda dossier_id, db_path: fake_candidates
    )
    monkeypatch.setattr("app.analysis.graph_analyzer.load_process_graphs", lambda db_path, dossier_id: fake_candidates)

    class _NoToolCallsModel:
        def invoke(self, messages):
            return AIMessage(content="no tool calls")

    proposer_model = _RecordingProposerModel(ProposedFindingBatch(findings=[]))
    monkeypatch.setattr(GraphAnalyzer, "_build_models", lambda self, tools: (_NoToolCallsModel(), proposer_model))

    settings = _settings(model_call_cap=100)
    analyzer = GraphAnalyzer(settings)
    analyzer.analyze("dossier-a", db_path)

    assert analyzer.model_call_cap_hit is False
    assert analyzer.cap_message is None
    assert analyzer.analyzed_graphs == 1
