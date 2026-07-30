"""Tests for the dossier profile (`app/analysis/profile.py`).

Two kinds of test here, matching the style of test_prefilter.py and
test_graph_engine.py:

- Against the real sample dossier (sample_data/Uebungsdaten_Muster_Verpackungen.zip),
  session-scoped, reusing the shared fixtures in conftest.py - dossier-wide
  counts, a known entity (SHELL_VENDOR/REAL_VENDOR, from test_graph_engine.py),
  determinism, and a loose performance guard.
- Against small synthetic dossiers built directly in SQLite (same technique
  test_graph_analyzer.py uses: hand-built normalized_records rows, no full
  normalization pipeline) - these pin down exact per-entry completeness and
  fill-rate arithmetic that would be tedious to isolate in the real dossier.
"""

from __future__ import annotations

import ast
import json
import re
import time
from pathlib import Path

import pytest

from app.analysis.profile import COMPLETENESS_DIMENSIONS, build_profile
from app.graph.builder import build_graph
from app.graph.schema import EdgeType, entity_node_id
from app.graph.subgraphs import build_process_graphs
from app.persistence import bulk_insert_records, init_normalized_table
from tests.conftest import SAMPLE_DOSSIER_ID, requires_sample_zip

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
# This used to be a denylist of the exact strings a deleted module
# (app/analysis/prefilter.py) once encoded: _REPAIR_KEYWORDS naming German
# repair-related words, _ROUND_THRESHOLDS naming round approval-limit-shaped
# amounts, and _SIGNAL_* constants naming a specific fraud pattern. That
# guards against the mistakes someone already made and removed - it does
# nothing against a *new* mistake of the same shape. `_REPAIR_WORDS =
# ("renovierung", "sanierung")` or a `2_500.0` threshold would pass a
# denylist test clean while being exactly as much a fraud scenario as what
# was removed.
#
# So this parses each module with `ast` and checks the *shape* a fraud
# scenario always takes, rather than its specific past content:
#
#  1. no module-level constant whose name itself suggests domain judgement
#     (KEYWORD/THRESHOLD/SIGNAL/PATTERN/SUSPICIOUS/FRAUD/RISK/EXPECTED/
#     REQUIRED) rather than data format;
#  2. no numeric literal at or above a modest bound, except the section-
#     budget constants entry_brief.py is deliberately allowed to name (see
#     that module's docstring on why those are exposed as constants);
#  3. no new tuple/list/set literal of two or more constants of the same
#     primitive kind (all strings, or all numbers) - a "list of words" is
#     exactly the shape _REPAIR_WORDS took above, and it only takes two
#     words to encode a vocabulary.
#
# An allowlist keyed by *value*, not name, holds every such collection that
# exists today and is legitimate: the completeness dimensions, the document-
# reference column names, the counterparty entity types, the master-data
# record types, and the quantile points. The mechanism is deliberately an
# allowlist rather than a memory of removed mistakes: the point is not to
# remember _REPAIR_WORDS specifically, it is that *any* new collection of
# this shape - named after the old mistake or not - fails until someone
# deliberately adds its exact value here, which is the point where "is this
# domain vocabulary?" has to be answered instead of slipped in silently.
# `__all__` is exempted by name (not by value) since it is Python's own
# export-list idiom, not domain vocabulary. Nothing else is exempted:
# entry_brief.py used to carry a column-name glossary dict, which would have
# been this test's one legitimate dict-shaped exception, but that glossary
# was itself withdrawn (see the T5 review brief's superseding note on
# Finding 2 - a large model already knows what a German GDPdU/GoBD column
# name denotes, so an authored glossary was spending tokens teaching it
# something it knows). With it gone, there is no authored domain vocabulary
# of any kind left in either module - a stronger property than an
# allowlisted exception for one.
# ---------------------------------------------------------------------------

_PROFILE_SOURCE = Path(__file__).resolve().parent.parent / "app" / "analysis" / "profile.py"
_ENTRY_BRIEF_SOURCE = Path(__file__).resolve().parent.parent / "app" / "analysis" / "entry_brief.py"

_FORBIDDEN_NAME_PATTERN = re.compile(
    r"KEYWORD|THRESHOLD|SIGNAL|PATTERN|SUSPICIOUS|FRAUD|RISK|EXPECTED|REQUIRED", re.IGNORECASE
)

# entry_brief.py's per-section character budgets - named module constants by
# design (see that module's docstring), not domain vocabulary.
_ALLOWED_LARGE_NUMERIC_CONSTANT_NAMES = {
    "ENTRY_SECTION_BUDGET",
    "TIMELINE_SECTION_BUDGET",
    "RECORDS_SECTION_BUDGET",
    "PARTIES_SECTION_BUDGET",
    "RELATIONSHIPS_SECTION_BUDGET",
    "NOT_PRESENT_SECTION_BUDGET",
    "CONVENTIONS_SECTION_BUDGET",
    "SUMMARY_BUDGET",
}

# A numeric literal at or above this is treated as potentially threshold-
# shaped. Comfortably above every legitimate small literal these two modules
# use today (list indices, the quantile points, the x100 in percentile
# formatting) and comfortably below the smallest section budget (500).
_MODEST_NUMERIC_BOUND = 200

# The exact value of every constant collection that exists in these two
# modules today and is legitimate - see the block comment above.
_ALLOWED_CONSTANT_COLLECTIONS = {
    ("date", "amount", "counterparty", "document_reference"),  # COMPLETENESS_DIMENSIONS
    ("BELEGNUMMER", "BUCHUNGSNUMMER", "DOKUMENT", "RECHNUNGSNUMMER"),  # _DOCUMENT_REFERENCE_FIELDS
    ("vendor", "customer"),  # _COUNTERPARTY_ENTITY_TYPES
    ("master_data", "master_change"),  # _MASTER_DATA_RECORD_TYPES
    (0.0, 0.25, 0.5, 0.75, 0.9, 0.99, 1.0),  # _QUANTILE_POINTS
}


def _check_module_for_fraud_scenario_shape(source_path: Path) -> list[str]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    violations: list[str] = []

    # Rule 1: module-level constant names suggesting domain judgement.
    for stmt in ast.iter_child_nodes(tree):
        target_name = None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            target_name = stmt.targets[0].id
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            target_name = stmt.target.id
        if target_name and _FORBIDDEN_NAME_PATTERN.search(target_name):
            violations.append(f"module-level constant {target_name!r} has a domain-judgement-shaped name")

    # Nodes that belong to an allowed large-numeric-constant's own value
    # subtree are exempt from rule 2.
    exempt_numeric_node_ids: set[int] = set()
    for stmt in ast.iter_child_nodes(tree):
        name = None
        value = None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            name, value = stmt.targets[0].id, stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
            name, value = stmt.target.id, stmt.value
        if name in _ALLOWED_LARGE_NUMERIC_CONSTANT_NAMES:
            exempt_numeric_node_ids.update(id(node) for node in ast.walk(value))

    # Rule 2: no numeric literal at/above the modest bound outside an
    # allowed named budget constant.
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            if id(node) in exempt_numeric_node_ids:
                continue
            if abs(node.value) >= _MODEST_NUMERIC_BOUND:
                violations.append(
                    f"numeric literal {node.value!r} is at/above the modest bound "
                    f"({_MODEST_NUMERIC_BOUND}) outside an allowed budget constant"
                )

    # Rule 3: no new module-level constant bound to a tuple/list/set literal
    # of 2+ same-kind constants. Scoped to module-level assignments only
    # (not any collection literal anywhere, e.g. inside a function body) -
    # genuine domain vocabulary is always given a reusable name, which is
    # exactly what every historical example (_REPAIR_WORDS, and this file's
    # own allowlisted collections below) does; sweeping in every anonymous
    # literal also catches ordinary structural code - a two-element counter
    # pair like ``[0, 0]``, or a function's own list of output lines - which
    # would make the rule too noisy to keep.
    for stmt in ast.iter_child_nodes(tree):
        target_name, value = None, None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            target_name, value = stmt.targets[0].id, stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
            target_name, value = stmt.target.id, stmt.value
        if value is None or not isinstance(value, (ast.Tuple, ast.List, ast.Set)):
            continue
        elts = value.elts
        if len(elts) < 2 or not all(isinstance(elt, ast.Constant) for elt in elts):
            continue
        values = [elt.value for elt in elts]
        is_all_str = all(isinstance(v, str) for v in values)
        is_all_number = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values)
        if not (is_all_str or is_all_number):
            continue
        if target_name == "__all__":
            continue
        if tuple(values) in _ALLOWED_CONSTANT_COLLECTIONS:
            continue
        violations.append(
            f"module-level constant {target_name!r} is a new "
            f"{type(value).__name__.lower()} literal {tuple(values)!r} with a domain-vocabulary shape"
        )

    return violations


@pytest.mark.parametrize("source_path", [_PROFILE_SOURCE, _ENTRY_BRIEF_SOURCE])
def test_no_encoded_fraud_scenario(source_path: Path):
    violations = _check_module_for_fraud_scenario_shape(source_path)
    assert not violations, f"{source_path.name} has fraud-scenario-shaped code:\n" + "\n".join(violations)


# The guard above is the only mechanical enforcement of this project's central
# rule (`agents/PLAN.md`: no fraud scenario may be encoded in code or in a
# prompt), and it is non-trivial code in its own right - three AST rules and
# two allowlists. An AST walk that silently matched nothing would let every
# future violation through while still reporting a green suite, which is
# exactly the failure mode the guard exists to prevent. So the guard is itself
# tested: each rule must fire on a reintroduced violation of the shape the
# deleted `prefilter.py` used, and clean code must stay clean.
_GUARD_CASES = [
    pytest.param(
        '_REPAIR_KEYWORDS = ("reparatur",)\n',
        "domain-judgement-shaped name",
        id="rule1-name-shaped-constant",
    ),
    pytest.param(
        '_WORDS = ("renovierung", "sanierung", "umbau")\n',
        "domain-vocabulary shape",
        id="rule3-new-keyword-list-the-old-denylist-would-have-missed",
    ),
    pytest.param(
        "_LIMITS = (2_500.0, 7_500.0)\n",
        "at/above the modest bound",
        id="rule2-threshold-the-old-denylist-would-have-missed",
    ),
]


@pytest.mark.parametrize("source,expected_message", _GUARD_CASES)
def test_the_fraud_scenario_guard_fires_on_a_reintroduced_violation(
    tmp_path: Path, source: str, expected_message: str
):
    module_path = tmp_path / "reintroduced.py"
    module_path.write_text(source, encoding="utf-8")
    violations = _check_module_for_fraud_scenario_shape(module_path)
    assert violations, f"guard did not fire on:\n{source}"
    assert any(expected_message in violation for violation in violations), (
        f"guard fired but not for the expected reason ({expected_message!r}): {violations}"
    )


def test_the_fraud_scenario_guard_passes_code_with_no_domain_vocabulary(tmp_path: Path):
    module_path = tmp_path / "clean.py"
    module_path.write_text('MAX_ROWS = 3\nLABEL = "record"\n', encoding="utf-8")
    assert _check_module_for_fraud_scenario_shape(module_path) == []
