"""Tests for the analysis pipeline (`app/analysis/pipeline.py`).

Never makes a live model call - the analyst model is a stand-in exposing only
`.invoke(messages)`, exercised through the real `AnalysisPipeline.analyze()`
so cap enforcement, concurrency and failure isolation are genuinely verified
rather than assumed. Follows the fixture style of `test_profile.py`: small
synthetic dossiers built directly in SQLite plus the real graph engine
(`build_graph`/`build_process_graphs`), never a hand-rolled fake graph -
`entry_brief.render_entry_brief` needs a real graph to render from.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.analysis.analyst import ProposedFinding, ProposedFindingBatch
from app.analysis.errors import GraphUnavailableError
from app.analysis.pipeline import (
    AnalysisPipeline,
    _build_analyst_model,
    _build_findings_from_proposals,
    _entry_amount_scores,
    rank_entries_for_analysis,
)
from app.analysis.profile import DossierProfile, EntryCompleteness, ShapeProfile, build_profile
from app.core.settings import AgentSettings
from app.evidence import EvidenceRecordStore
from app.graph.builder import build_graph
from app.graph.subgraphs import ProcessGraph, build_process_graphs
from app.persistence import bulk_insert_records, init_normalized_table
from tests.fraud_scenario_guard import check_module_for_fraud_scenario_shape

_PIPELINE_SOURCE = Path(__file__).resolve().parent.parent / "app" / "analysis" / "pipeline.py"

_RECORD_ID_RE = re.compile(r"^Record (\S+) \(", re.MULTILINE)


def _record_id_from_brief(brief: str) -> str:
    match = _RECORD_ID_RE.search(brief)
    assert match, f"no 'Record <id> (' line found in brief:\n{brief[:500]}"
    return match.group(1)


def _settings(**overrides) -> AgentSettings:
    defaults = dict(
        openai_api_key="test-key",
        analyst_model="gpt-test",
        gate_model="gpt-test",
        verifier_model="gpt-test",
        agent_enabled=True,
        model_call_cap=100,
        max_workers=4,
    )
    defaults.update(overrides)
    return AgentSettings(**defaults)


def _row(
    record_id: str,
    dossier_id: str,
    record_type: str,
    *,
    date: str | None = None,
    amount: float | None = None,
    currency: str | None = "EUR",
    data: dict | None = None,
    entities: list[dict] | None = None,
    relationships: dict | None = None,
    file_id: str = "file-1",
    relative_path: str = "Test/File.txt",
    row_number: int = 1,
) -> dict:
    normalized = {
        "record_id": record_id,
        "dossier_id": dossier_id,
        "record_type": record_type,
        "source": {"file_id": file_id, "relative_path": relative_path, "row_number": row_number},
        "entities": entities or [],
        "relationships": relationships or {},
        "data": data or {},
        "date": date,
        "period": date[:7] if date else None,
        "amount": amount,
        "currency": currency,
        "text_content": None,
    }
    return {
        "record_id": record_id,
        "dossier_id": dossier_id,
        "file_id": file_id,
        "record_type": record_type,
        "date": date,
        "amount": amount,
        "currency": currency,
        "data_json": json.dumps(normalized, ensure_ascii=False),
    }


def _build_dossier(dossier_id: str, db_path: Path, rows: list[dict]):
    init_normalized_table(db_path)
    bulk_insert_records(db_path, rows)
    graph = build_graph(dossier_id, db_path)
    process_graphs = build_process_graphs(dossier_id, graph)
    return graph, process_graphs


def _independent_entries(dossier_id: str, count: int) -> list[dict]:
    """``count`` single-record vendor_posting entries, each with a distinct
    vendor and document reference so each becomes its own process graph -
    same technique test_profile.py's completeness_dossier fixture uses."""
    return [
        _row(
            f"record-{i}",
            dossier_id,
            "vendor_posting",
            date="2025-01-10",
            amount=1000.0 + i,
            data={"BELEGNUMMER": f"DOC-{i}", "BUCHUNGSBETRAG": 1000.0 + i},
            entities=[{"entity_type": "vendor", "entity_id": f"vendor-{i}"}],
        )
        for i in range(count)
    ]


class _NoFindingsModel:
    def __init__(self) -> None:
        self.invocations = 0

    def invoke(self, messages):
        self.invocations += 1
        return ProposedFindingBatch(findings=[])


class _RecordCitingModel:
    """Cites whichever record_id this entry's own rendered brief carries, so
    each of several concurrently analysed entries gets a distinct, correct
    finding rather than every thread proposing the same canned one."""

    def __init__(self) -> None:
        self.invocations = 0

    def invoke(self, messages):
        self.invocations += 1
        record_id = _record_id_from_brief(messages[1]["content"])
        return ProposedFindingBatch(
            findings=[
                ProposedFinding(
                    title=f"Finding for {record_id}",
                    severity="medium",
                    category="review",
                    explanation="Explanation text long enough to pass validation.",
                    reasoning="Reasoning text long enough to pass validation.",
                    confidence="low",
                    record_ids=[record_id],
                )
            ]
        )


# ---------------------------------------------------------------------------
# Trust boundary: evidence store + _build_findings_from_proposals
# ---------------------------------------------------------------------------


def _minimal_record(record_id: str, dossier_id: str) -> dict:
    normalized = {
        "record_id": record_id,
        "source": {"file_id": "file-1", "relative_path": "Kreditoren/Buchungen.txt", "row_number": 4},
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
    bulk_insert_records(db_path, [_minimal_record("record-a", "dossier-a"), _minimal_record("record-b", "dossier-b")])

    records = EvidenceRecordStore("dossier-a", db_path).resolve(["record-a", "record-b"])

    assert [record["record_id"] for record in records] == ["record-a"]


def test_rebuilds_evidence_from_local_record_not_model_text_and_sets_graph_id(tmp_path: Path):
    db_path = tmp_path / "registry.db"
    init_normalized_table(db_path)
    bulk_insert_records(db_path, [_minimal_record("record-a", "dossier-a")])
    proposal = ProposedFinding(
        title="Potential payment splitting pattern",
        severity="high",
        category="control_override",
        explanation="Several payments need review.",
        reasoning="The source record is near the approval threshold.",
        confidence="medium",
        record_ids=["record-a"],
    )

    findings = _build_findings_from_proposals(
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

    findings = _build_findings_from_proposals(
        "dossier-a", EvidenceRecordStore("dossier-a", db_path), "PG-1", [proposal]
    )

    assert findings == []


def test_discards_proposal_citing_a_foreign_dossier_record_id(tmp_path: Path):
    db_path = tmp_path / "registry.db"
    init_normalized_table(db_path)
    bulk_insert_records(db_path, [_minimal_record("record-a", "dossier-b")])
    proposal = ProposedFinding(
        title="Potential unsupported activity",
        severity="medium",
        category="review",
        explanation="This should not be persisted.",
        reasoning="The record belongs to a different dossier.",
        confidence="low",
        record_ids=["record-a"],
    )

    findings = _build_findings_from_proposals(
        "dossier-a", EvidenceRecordStore("dossier-a", db_path), "PG-1", [proposal]
    )

    assert findings == []


def test_discards_the_whole_proposal_even_if_only_one_of_several_ids_is_bad(tmp_path: Path):
    db_path = tmp_path / "registry.db"
    init_normalized_table(db_path)
    bulk_insert_records(db_path, [_minimal_record("record-a", "dossier-a")])
    proposal = ProposedFinding(
        title="Mixed valid and invalid citation",
        severity="medium",
        category="review",
        explanation="One id is real, one is not.",
        reasoning="Whole-proposal rejection must not partially accept this.",
        confidence="low",
        record_ids=["record-a", "record-does-not-exist"],
    )

    findings = _build_findings_from_proposals(
        "dossier-a", EvidenceRecordStore("dossier-a", db_path), "PG-1", [proposal]
    )

    assert findings == []


# ---------------------------------------------------------------------------
# Model construction: structured output, no tools bound
# ---------------------------------------------------------------------------


def test_build_analyst_model_binds_structured_output_and_no_tools(monkeypatch):
    import langchain_openai

    captured: dict = {}

    class _FakeChatOpenAI:
        def __init__(self, **kwargs) -> None:
            captured["kwargs"] = kwargs
            self.bind_tools_called = False

        def bind_tools(self, tools):  # pragma: no cover - must never be called
            self.bind_tools_called = True
            return self

        def with_structured_output(self, schema):
            captured["schema"] = schema
            return self

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", _FakeChatOpenAI)

    model = _build_analyst_model(_settings(analyst_model="gpt-test-model"))

    assert captured["kwargs"]["model"] == "gpt-test-model"
    assert captured["schema"] is ProposedFindingBatch
    assert getattr(model, "bind_tools_called", False) is False


# ---------------------------------------------------------------------------
# AnalysisPipeline.analyze: configuration, cap enforcement and recording
# ---------------------------------------------------------------------------


def test_analyze_raises_graph_unavailable_error_when_agent_enabled_without_credentials(tmp_path: Path):
    db_path = tmp_path / "registry.db"
    init_normalized_table(db_path)
    settings = _settings(openai_api_key=None)

    with pytest.raises(GraphUnavailableError):
        AnalysisPipeline(settings).analyze("dossier-a", db_path)


def test_model_call_cap_truncates_entries_and_records_the_cap_hit(tmp_path: Path, monkeypatch):
    dossier_id = "dossier-cap-hit"
    db_path = tmp_path / "registry.db"
    graph, process_graphs = _build_dossier(dossier_id, db_path, _independent_entries(dossier_id, 5))

    stub_model = _NoFindingsModel()
    monkeypatch.setattr("app.analysis.pipeline._build_analyst_model", lambda settings: stub_model)

    settings = _settings(model_call_cap=2, max_workers=1)
    pipeline = AnalysisPipeline(settings, graph=graph, process_graphs=process_graphs)
    findings = pipeline.analyze(dossier_id, db_path)

    assert findings == []
    assert pipeline.total_entries == 5
    assert pipeline.analyzed_entries == 2
    assert pipeline.model_call_cap_hit is True
    assert "2" in pipeline.cap_message and "5" in pipeline.cap_message
    # Proof the cap actually stopped work, not just that the stats say so.
    assert stub_model.invocations == 2


def test_no_cap_hit_when_entries_are_within_the_cap(tmp_path: Path, monkeypatch):
    dossier_id = "dossier-within-cap"
    db_path = tmp_path / "registry.db"
    graph, process_graphs = _build_dossier(dossier_id, db_path, _independent_entries(dossier_id, 3))

    monkeypatch.setattr("app.analysis.pipeline._build_analyst_model", lambda settings: _NoFindingsModel())

    settings = _settings(model_call_cap=100, max_workers=1)
    pipeline = AnalysisPipeline(settings, graph=graph, process_graphs=process_graphs)
    pipeline.analyze(dossier_id, db_path)

    assert pipeline.model_call_cap_hit is False
    assert pipeline.cap_message is None
    assert pipeline.analyzed_entries == 3


# ---------------------------------------------------------------------------
# Concurrency: determinism, per-entry isolation, systemic-failure escalation
# ---------------------------------------------------------------------------


def test_concurrent_and_sequential_analysis_produce_identical_findings(tmp_path: Path, monkeypatch):
    dossier_id = "dossier-concurrency"
    db_path = tmp_path / "registry.db"
    graph, process_graphs = _build_dossier(dossier_id, db_path, _independent_entries(dossier_id, 8))

    monkeypatch.setattr("app.analysis.pipeline._build_analyst_model", lambda settings: _RecordCitingModel())

    sequential = AnalysisPipeline(
        _settings(model_call_cap=100, max_workers=1), graph=graph, process_graphs=process_graphs
    ).analyze(dossier_id, db_path)
    concurrent = AnalysisPipeline(
        _settings(model_call_cap=100, max_workers=8), graph=graph, process_graphs=process_graphs
    ).analyze(dossier_id, db_path)

    assert len(sequential) == 8
    assert [f.model_dump(mode="json") for f in sequential] == [f.model_dump(mode="json") for f in concurrent]
    # Proves the final ordering comes from the deliberate sort, not from
    # however the threads happened to finish.
    assert [f.finding_id for f in concurrent] == sorted(f.finding_id for f in concurrent)


def test_a_single_entry_failure_is_isolated_and_does_not_abort_the_run(tmp_path: Path, monkeypatch):
    dossier_id = "dossier-isolated-failure"
    db_path = tmp_path / "registry.db"
    graph, process_graphs = _build_dossier(dossier_id, db_path, _independent_entries(dossier_id, 3))

    class _FailsOnOneEntryModel:
        def invoke(self, messages):
            record_id = _record_id_from_brief(messages[1]["content"])
            if record_id == "record-1":
                raise RuntimeError("simulated per-entry failure")
            return ProposedFindingBatch(
                findings=[
                    ProposedFinding(
                        title=f"Finding for {record_id}",
                        severity="medium",
                        category="review",
                        explanation="Explanation text long enough to pass validation.",
                        reasoning="Reasoning text long enough to pass validation.",
                        confidence="low",
                        record_ids=[record_id],
                    )
                ]
            )

    monkeypatch.setattr("app.analysis.pipeline._build_analyst_model", lambda settings: _FailsOnOneEntryModel())

    settings = _settings(model_call_cap=100, max_workers=1)
    pipeline = AnalysisPipeline(settings, graph=graph, process_graphs=process_graphs)
    findings = pipeline.analyze(dossier_id, db_path)

    assert {evidence.record_id for finding in findings for evidence in finding.evidence} == {"record-0", "record-2"}


def test_every_entry_failing_raises_graph_unavailable_error(tmp_path: Path, monkeypatch):
    dossier_id = "dossier-all-fail"
    db_path = tmp_path / "registry.db"
    graph, process_graphs = _build_dossier(dossier_id, db_path, _independent_entries(dossier_id, 3))

    class _AlwaysFailsModel:
        def invoke(self, messages):
            raise RuntimeError("simulated systemic failure")

    monkeypatch.setattr("app.analysis.pipeline._build_analyst_model", lambda settings: _AlwaysFailsModel())

    settings = _settings(model_call_cap=100, max_workers=1)
    with pytest.raises(GraphUnavailableError):
        AnalysisPipeline(settings, graph=graph, process_graphs=process_graphs).analyze(dossier_id, db_path)


def test_an_authentication_error_on_one_entry_raises_even_with_other_successes(tmp_path: Path, monkeypatch):
    """An authentication error is a configuration problem, not a per-entry one
    - it must still abort the run with GraphUnavailableError even though other
    entries in the same batch happened to succeed first."""
    import httpx
    from openai import AuthenticationError

    dossier_id = "dossier-auth-error"
    db_path = tmp_path / "registry.db"
    graph, process_graphs = _build_dossier(dossier_id, db_path, _independent_entries(dossier_id, 3))

    def _auth_error() -> AuthenticationError:
        request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        response = httpx.Response(401, request=request)
        return AuthenticationError("invalid api key", response=response, body=None)

    class _AuthFailsOnOneEntryModel:
        def invoke(self, messages):
            record_id = _record_id_from_brief(messages[1]["content"])
            if record_id == "record-1":
                raise _auth_error()
            return ProposedFindingBatch(findings=[])

    monkeypatch.setattr("app.analysis.pipeline._build_analyst_model", lambda settings: _AuthFailsOnOneEntryModel())

    settings = _settings(model_call_cap=100, max_workers=1)
    with pytest.raises(GraphUnavailableError):
        AnalysisPipeline(settings, graph=graph, process_graphs=process_graphs).analyze(dossier_id, db_path)


# ---------------------------------------------------------------------------
# _entry_amount_scores: one real pass over records
# ---------------------------------------------------------------------------


def test_entry_amount_scores_reads_the_amount_carried_by_each_entrys_own_record(tmp_path: Path):
    dossier_id = "dossier-amount-scores"
    db_path = tmp_path / "registry.db"
    _graph, process_graphs = _build_dossier(dossier_id, db_path, _independent_entries(dossier_id, 3))

    scores = _entry_amount_scores(db_path, dossier_id, process_graphs)

    for process_graph in process_graphs:
        (record_id,) = process_graph.record_ids
        expected_amount = 1000.0 + int(record_id.split("-")[1])
        assert scores[process_graph.graph_id] == pytest.approx(expected_amount)


# ---------------------------------------------------------------------------
# rank_entries_for_analysis: never excludes, deterministic, direction of each
# of the three signals - hand-built DossierProfile/ProcessGraph objects, no
# real graph needed, so each signal is pinned down in isolation.
# ---------------------------------------------------------------------------


def _fake_process_graph(graph_id: str, record_ids: tuple[str, ...] = ("r1",)) -> ProcessGraph:
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


def _full_completeness(graph_id: str, record_ids: tuple[str, ...] = ("r1",)) -> EntryCompleteness:
    return EntryCompleteness(
        graph_id=graph_id,
        has_date=True,
        date_record_ids=record_ids,
        has_amount=True,
        amount_record_ids=record_ids,
        has_counterparty=True,
        counterparty_record_ids=record_ids,
        has_document_reference=True,
        document_reference_record_ids=record_ids,
        field_fill_rates={},
    )


def _fake_profile(*, entry_shape, shapes, entry_completeness, entry_edge_types, amount_quantiles) -> DossierProfile:
    return DossierProfile(
        dossier_id="fake-dossier",
        record_count=0,
        total_entries=len(entry_shape),
        record_type_counts={},
        amount_quantiles=amount_quantiles,
        periods=(),
        entities={},
        shapes=shapes,
        entry_shape=entry_shape,
        entry_edge_types=entry_edge_types,
        entry_completeness=entry_completeness,
    )


def test_rank_entries_returns_every_entry_exactly_once_and_never_excludes(tmp_path: Path):
    dossier_id = "dossier-never-excludes"
    db_path = tmp_path / "registry.db"
    _graph, process_graphs = _build_dossier(dossier_id, db_path, _independent_entries(dossier_id, 6))
    profile = build_profile(dossier_id, db_path, graph=_graph, process_graphs=process_graphs)
    amount_scores = _entry_amount_scores(db_path, dossier_id, process_graphs)

    ordered = rank_entries_for_analysis(profile, process_graphs, amount_scores)

    assert sorted(ordered) == sorted(pg.graph_id for pg in process_graphs)


def test_rank_entries_is_deterministic(tmp_path: Path):
    dossier_id = "dossier-rank-determinism"
    db_path = tmp_path / "registry.db"
    graph, process_graphs = _build_dossier(dossier_id, db_path, _independent_entries(dossier_id, 6))
    profile = build_profile(dossier_id, db_path, graph=graph, process_graphs=process_graphs)
    amount_scores = _entry_amount_scores(db_path, dossier_id, process_graphs)

    first = rank_entries_for_analysis(profile, process_graphs, amount_scores)
    second = rank_entries_for_analysis(profile, process_graphs, amount_scores)

    assert first == second


def test_higher_amount_entry_ranks_before_lower_amount_entry_of_identical_shape_and_completeness():
    shape = ("vendor_posting",)
    shape_profile = ShapeProfile(
        shape=shape, entry_count=2, record_types=("vendor_posting",), edge_type_counts={}, completeness_counts={}
    )
    process_graphs = [_fake_process_graph("PG-high"), _fake_process_graph("PG-low")]
    profile = _fake_profile(
        entry_shape={"PG-high": shape, "PG-low": shape},
        shapes={shape: shape_profile},
        entry_completeness={"PG-high": _full_completeness("PG-high"), "PG-low": _full_completeness("PG-low")},
        entry_edge_types={"PG-high": (), "PG-low": ()},
        amount_quantiles={"p0": 100.0, "p50": 500.0, "p100": 900.0},
    )

    ordered = rank_entries_for_analysis(profile, process_graphs, {"PG-high": 950.0, "PG-low": 50.0})

    assert ordered.index("PG-high") < ordered.index("PG-low")


def test_rarer_shape_ranks_before_common_shape_all_else_equal():
    common_shape = ("vendor_posting",)
    rare_shape = ("asset_record",)
    common_profile = ShapeProfile(
        shape=common_shape, entry_count=100, record_types=("vendor_posting",), edge_type_counts={}, completeness_counts={}
    )
    rare_profile = ShapeProfile(
        shape=rare_shape, entry_count=1, record_types=("asset_record",), edge_type_counts={}, completeness_counts={}
    )
    process_graphs = [_fake_process_graph("PG-common"), _fake_process_graph("PG-rare")]
    profile = _fake_profile(
        entry_shape={"PG-common": common_shape, "PG-rare": rare_shape},
        shapes={common_shape: common_profile, rare_shape: rare_profile},
        entry_completeness={"PG-common": _full_completeness("PG-common"), "PG-rare": _full_completeness("PG-rare")},
        entry_edge_types={"PG-common": (), "PG-rare": ()},
        amount_quantiles={},
    )

    ordered = rank_entries_for_analysis(profile, process_graphs, {})

    assert ordered.index("PG-rare") < ordered.index("PG-common")


def test_entry_missing_more_identity_dimensions_ranks_before_a_fully_identified_peer():
    shape = ("vendor_posting",)
    shape_profile = ShapeProfile(
        shape=shape, entry_count=2, record_types=("vendor_posting",), edge_type_counts={}, completeness_counts={}
    )
    process_graphs = [_fake_process_graph("PG-incomplete"), _fake_process_graph("PG-complete")]
    incomplete = EntryCompleteness(
        graph_id="PG-incomplete",
        has_date=False,
        date_record_ids=(),
        has_amount=False,
        amount_record_ids=(),
        has_counterparty=True,
        counterparty_record_ids=("r1",),
        has_document_reference=True,
        document_reference_record_ids=("r1",),
        field_fill_rates={},
    )
    profile = _fake_profile(
        entry_shape={"PG-incomplete": shape, "PG-complete": shape},
        shapes={shape: shape_profile},
        entry_completeness={"PG-incomplete": incomplete, "PG-complete": _full_completeness("PG-complete")},
        entry_edge_types={"PG-incomplete": (), "PG-complete": ()},
        amount_quantiles={},
    )

    ordered = rank_entries_for_analysis(profile, process_graphs, {})

    assert ordered.index("PG-incomplete") < ordered.index("PG-complete")


# ---------------------------------------------------------------------------
# No encoded fraud scenario
# ---------------------------------------------------------------------------


def test_pipeline_module_has_no_encoded_fraud_scenario():
    violations = check_module_for_fraud_scenario_shape(_PIPELINE_SOURCE)
    assert not violations, "pipeline.py has fraud-scenario-shaped code:\n" + "\n".join(violations)
