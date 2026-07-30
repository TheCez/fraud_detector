"""Tests for the dossier profile (`app/analysis/profile.py`).

Two kinds of test here, matching the style of test_graph_engine.py:

- Against the real sample dossier (sample_data/Uebungsdaten_Muster_Verpackungen.zip),
  session-scoped, reusing the shared fixtures in conftest.py - dossier-wide
  counts, a known entity (SHELL_VENDOR/REAL_VENDOR, from test_graph_engine.py),
  determinism, and a loose performance guard.
- Against small synthetic dossiers built directly in SQLite (hand-built
  normalized_records rows, no full normalization pipeline) - these pin down
  exact per-entry completeness and fill-rate arithmetic that would be tedious
  to isolate in the real dossier.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from app.analysis.profile import COMPLETENESS_DIMENSIONS, build_profile
from app.graph.builder import build_graph
from app.graph.schema import EdgeType, entity_node_id
from app.graph.subgraphs import build_process_graphs
from app.persistence import bulk_insert_records, init_normalized_table
from tests.conftest import SAMPLE_DOSSIER_ID, requires_sample_zip
from tests.fraud_scenario_guard import check_module_for_fraud_scenario_shape

DOSSIER_ID = SAMPLE_DOSSIER_ID

SHELL_VENDOR = "209101"  # no goods receipt anywhere - see test_graph_engine.py
REAL_VENDOR = "209112"  # has goods receipts - see test_graph_engine.py


# ---------------------------------------------------------------------------
# Real sample dossier
# ---------------------------------------------------------------------------


@pytest.fixture
def profile(sample_profile):
    """Thin alias onto the shared session-scoped fixture in conftest.py - see
    its docstring for why it lives there instead of here."""
    return sample_profile


@requires_sample_zip
def test_dossier_wide_counts_are_internally_consistent(profile, sample_process_graphs):
    assert profile.record_count > 0
    assert sum(profile.record_type_counts.values()) == profile.record_count
    assert profile.total_entries == len(sample_process_graphs)
    assert profile.amount_quantiles  # some records carry an amount
    assert profile.amount_quantiles["p0"] <= profile.amount_quantiles["p50"] <= profile.amount_quantiles["p100"]


@requires_sample_zip
def test_shapes_partition_every_entry_exactly_once(profile, sample_process_graphs):
    assert sum(shape.entry_count for shape in profile.shapes.values()) == len(sample_process_graphs)
    assert set(profile.entry_shape.keys()) == {pg.graph_id for pg in sample_process_graphs}


@requires_sample_zip
def test_shell_vendor_has_zero_has_receipt_and_real_vendor_does_not(profile):
    shell = profile.entities[entity_node_id("vendor", SHELL_VENDOR)]
    real = profile.entities[entity_node_id("vendor", REAL_VENDOR)]

    # The absent relationship is the useful part - it must be a present zero,
    # not a missing key.
    assert shell.edge_type_counts["has_receipt"] == 0
    assert real.edge_type_counts["has_receipt"] > 0

    # Every entity carries a count for every known edge type, seen or not.
    assert set(shell.edge_type_counts) == {edge_type.value for edge_type in EdgeType}
    assert set(real.edge_type_counts) == {edge_type.value for edge_type in EdgeType}


@requires_sample_zip
def test_shell_vendor_record_count_matches_direct_graph_inspection(profile, sample_graph):
    from app.graph.schema import EdgeType as ET

    vendor_node = entity_node_id("vendor", SHELL_VENDOR)
    direct_record_ids = {
        rid for u, v, d in sample_graph.in_edges(vendor_node, data=True) if d["edge_type"] == ET.paid_to.value for rid in d["record_ids"]
    }
    assert profile.entities[vendor_node].record_count >= len(direct_record_ids)


@requires_sample_zip
def test_profile_is_deterministic(sample_saved_db_path: Path, sample_graph, sample_process_graphs):
    first = build_profile(
        SAMPLE_DOSSIER_ID, sample_saved_db_path, graph=sample_graph, process_graphs=sample_process_graphs
    )
    second = build_profile(
        SAMPLE_DOSSIER_ID, sample_saved_db_path, graph=sample_graph, process_graphs=sample_process_graphs
    )
    assert first == second


@requires_sample_zip
def test_building_the_profile_is_a_bounded_number_of_passes_not_per_graph_work(
    sample_saved_db_path: Path, sample_graph, sample_process_graphs
):
    """Loose performance guard, not a precise benchmark: building the profile
    is documented as one pass over records and one over edges, never a
    per-process-graph subgraph() call (see subgraphs.py's docstring on why
    that was a measured disaster at this dossier's scale - ~4,900 process
    graphs). A per-graph-cost bug would blow well past this generous ceiling
    on a slow CI box; a real single/double pass comfortably will not.
    """
    started = time.monotonic()
    build_profile(SAMPLE_DOSSIER_ID, sample_saved_db_path, graph=sample_graph, process_graphs=sample_process_graphs)
    elapsed = time.monotonic() - started
    assert elapsed < 20.0, f"build_profile took {elapsed:.1f}s - investigate for accidental per-graph work"


# ---------------------------------------------------------------------------
# Synthetic dossiers - exact completeness/fill-rate arithmetic
# ---------------------------------------------------------------------------


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


def _build(dossier_id: str, db_path: Path, rows: list[dict]):
    init_normalized_table(db_path)
    bulk_insert_records(db_path, rows)
    graph = build_graph(dossier_id, db_path)
    process_graphs = build_process_graphs(dossier_id, graph)
    return graph, process_graphs


def _graph_id_for(process_graphs, record_id: str) -> str:
    return next(pg.graph_id for pg in process_graphs if record_id in pg.record_ids)


@pytest.fixture
def completeness_dossier(tmp_path: Path):
    """Three single-record master_data entries of the same shape:

    - M1: fully identified, has an approver (an approved_by edge).
    - M2: fully identified, no approver.
    - M3: identified by nothing at all - no date, amount, counterparty, or
      document reference - and no approver either.

    Plus a fourth, document-joined pair (J1 + M4) forming a different, richer
    shape (journal_entry + master_data) so there is a real nearest-superset
    shape for the bare-master_data shape to be compared against.
    """
    dossier_id = "completeness-dossier"
    db_path = tmp_path / "registry.db"

    rows = [
        _row(
            "M1",
            dossier_id,
            "master_data",
            date="2024-01-05",
            amount=100.0,
            data={"GEAENDERT_VON": "u1", "GENEHMIGT_VON": "u2", "BELEGNUMMER": "M1-DOC"},
            entities=[
                {"entity_type": "vendor", "entity_id": "400001"},
                {"entity_type": "user", "entity_id": "u2"},
            ],
            relationships={"approved_by": "u2"},
        ),
        _row(
            "M2",
            dossier_id,
            "master_data",
            date="2024-02-05",
            amount=200.0,
            data={"GEAENDERT_VON": "u1", "BELEGNUMMER": "M2-DOC"},
            entities=[{"entity_type": "vendor", "entity_id": "400002"}],
        ),
        _row(
            "M3",
            dossier_id,
            "master_data",
            date=None,
            amount=None,
            currency=None,
            data={"GEAENDERT_VON": "u1"},
            entities=[],
        ),
        _row(
            "J1",
            dossier_id,
            "journal_entry",
            date="2024-03-01",
            amount=500.0,
            data={"BELEGNUMMER": "DJ-1", "SACHKONTONUMMER": "100000", "BUCHUNGSBETRAG": 500.0},
            entities=[{"entity_type": "account", "entity_id": "100000"}],
        ),
        _row(
            "M4",
            dossier_id,
            "master_data",
            date="2024-03-01",
            amount=None,
            data={"BELEGNUMMER": "DJ-1", "GEAENDERT_VON": None},
            entities=[],
        ),
    ]

    graph, process_graphs = _build(dossier_id, db_path, rows)
    profile = build_profile(dossier_id, db_path, graph=graph, process_graphs=process_graphs)
    return dossier_id, db_path, graph, process_graphs, profile


def test_completeness_is_true_when_any_record_in_the_entry_supplies_it(completeness_dossier):
    _dossier_id, _db_path, _graph, process_graphs, profile = completeness_dossier
    m1_graph_id = _graph_id_for(process_graphs, "M1")
    completeness = profile.entry_completeness[m1_graph_id]

    assert completeness.has_date and completeness.date_record_ids == ("M1",)
    assert completeness.has_amount
    assert completeness.has_counterparty
    assert completeness.has_document_reference


def test_completeness_is_false_only_when_no_record_in_the_entry_supplies_it(completeness_dossier):
    _dossier_id, _db_path, _graph, process_graphs, profile = completeness_dossier
    m3_graph_id = _graph_id_for(process_graphs, "M3")
    completeness = profile.entry_completeness[m3_graph_id]

    assert not completeness.has_date
    assert not completeness.has_amount
    assert not completeness.has_counterparty
    assert not completeness.has_document_reference
    for dimension in COMPLETENESS_DIMENSIONS:
        assert getattr(completeness, f"{dimension}_record_ids") == ()


def test_field_fill_rate_counts_only_records_that_could_carry_the_column(completeness_dossier):
    _dossier_id, _db_path, _graph, process_graphs, profile = completeness_dossier
    j1_graph_id = _graph_id_for(process_graphs, "J1")
    m4_graph_id = _graph_id_for(process_graphs, "M4")
    assert j1_graph_id == m4_graph_id, "J1 and M4 should document-join into one entry via shared BELEGNUMMER"

    fill_rates = profile.entry_completeness[j1_graph_id].field_fill_rates
    assert fill_rates["BELEGNUMMER"] == (2, 2)  # both records carry and fill it
    assert fill_rates["SACHKONTONUMMER"] == (1, 1)  # only J1 could carry it
    assert fill_rates["GEAENDERT_VON"] == (0, 1)  # only M4 could carry it, and it is empty


def test_shape_edge_type_and_completeness_coverage_is_per_shape_not_per_record(completeness_dossier):
    _dossier_id, _db_path, _graph, process_graphs, profile = completeness_dossier
    m1_graph_id = _graph_id_for(process_graphs, "M1")
    shape = profile.entry_shape[m1_graph_id]
    assert shape == ("master_data",)

    shape_profile = profile.shapes[shape]
    assert shape_profile.entry_count == 3  # M1, M2, M3
    assert shape_profile.edge_type_counts.get("approved_by", 0) == 1  # only M1
    assert shape_profile.completeness_counts == {
        "date": 2,  # M1, M2
        "amount": 2,
        "counterparty": 2,
        "document_reference": 2,
    }


def test_nearest_superset_shape_exists_for_the_bare_master_data_shape(completeness_dossier):
    _dossier_id, _db_path, _graph, process_graphs, profile = completeness_dossier
    richer_shape = tuple(sorted(("journal_entry", "master_data")))
    assert richer_shape in profile.shapes
    assert profile.shapes[richer_shape].entry_count == 1


def test_profile_determinism_holds_on_a_synthetic_dossier_too(completeness_dossier):
    dossier_id, db_path, graph, process_graphs, first = completeness_dossier
    second = build_profile(dossier_id, db_path, graph=graph, process_graphs=process_graphs)
    assert first == second


# ---------------------------------------------------------------------------
# No encoded fraud scenario
#
# The AST guard itself - the *shape* a fraud scenario always takes, rather
# than a denylist of specific past content - now lives in
# `tests/fraud_scenario_guard.py`, shared with test_analyst.py and
# test_pipeline.py; see that module's docstring for the three rules and the
# value-keyed allowlist. Its own self-tests live in
# `tests/test_fraud_scenario_guard.py`.
# ---------------------------------------------------------------------------

_PROFILE_SOURCE = Path(__file__).resolve().parent.parent / "app" / "analysis" / "profile.py"
_ENTRY_BRIEF_SOURCE = Path(__file__).resolve().parent.parent / "app" / "analysis" / "entry_brief.py"


@pytest.mark.parametrize("source_path", [_PROFILE_SOURCE, _ENTRY_BRIEF_SOURCE])
def test_no_encoded_fraud_scenario(source_path: Path):
    violations = check_module_for_fraud_scenario_shape(source_path)
    assert not violations, f"{source_path.name} has fraud-scenario-shaped code:\n" + "\n".join(violations)
