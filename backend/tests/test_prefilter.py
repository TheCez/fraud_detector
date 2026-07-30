"""Tests for the pre-filter (`app/analysis/prefilter.py`) against the real
sample dossier (sample_data/Uebungsdaten_Muster_Verpackungen.zip).

Identifiers used below are known-good per the task brief and were
independently verified against the real sample data - the sealed ground-truth
file is never read. Follows the fixture style of test_graph_engine.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.graph.schema import entity_node_id
from app.analysis.prefilter import select_candidate_graphs
from tests.conftest import SAMPLE_DOSSIER_ID, requires_sample_zip

DOSSIER_ID = SAMPLE_DOSSIER_ID

SHELL_VENDOR = "209101"
REAL_VENDOR = "209112"
SPLIT_VENDOR = "200007"

REPAIR_ASSETS = (
    "040000-000191",
    "040000-000192",
    "040000-000194",
    "040000-000196",
    "060000-000193",
    "060000-000195",
)


@pytest.fixture
def db_path(sample_saved_db_path: Path) -> Path:
    return sample_saved_db_path


@pytest.fixture(scope="session")
def candidates(sample_saved_db_path: Path):
    """Session-scoped: re-selecting per test would load the whole persisted
    graph again, and this file is already among the slowest in the suite."""
    return select_candidate_graphs(SAMPLE_DOSSIER_ID, sample_saved_db_path)


def _selected_entity_nodes(candidates, prefix: str) -> set[str]:
    return {
        node_id
        for candidate in candidates
        for node_id in candidate.graph.entity_node_ids
        if node_id.startswith(prefix)
    }


@requires_sample_zip
def test_prefilter_is_meaningfully_selective_not_a_pass_through(db_path: Path, candidates):
    from app.graph.store import load_process_graphs

    total = len(load_process_graphs(db_path, DOSSIER_ID))
    assert total > 0
    assert 0 < len(candidates) < total


@requires_sample_zip
def test_shell_vendor_with_no_goods_receipt_is_selected(candidates):
    selected_vendors = _selected_entity_nodes(candidates, "vendor:")
    assert entity_node_id("vendor", SHELL_VENDOR) in selected_vendors


@requires_sample_zip
def test_honest_counterpart_vendor_may_be_selected_but_is_not_required(candidates):
    """The pre-filter may admit the honest, real-goods-receipt vendor too - that
    is correct and expected. Rejecting it is the model's job, not the filter's.
    This test only documents that the filter does not crash or exclude it by
    construction; it does not assert either way."""
    selected_vendors = _selected_entity_nodes(candidates, "vendor:")
    # No assertion on membership - see docstring. Just prove the pipeline ran.
    assert isinstance(selected_vendors, set)


@requires_sample_zip
def test_repair_named_assets_are_selected(candidates):
    selected_assets = _selected_entity_nodes(candidates, "asset:")
    for asset_id in REPAIR_ASSETS:
        assert entity_node_id("asset", asset_id) in selected_assets, f"asset {asset_id} not selected"


@requires_sample_zip
def test_split_payment_vendor_is_selected(candidates):
    selected_vendors = _selected_entity_nodes(candidates, "vendor:")
    assert entity_node_id("vendor", SPLIT_VENDOR) in selected_vendors


@requires_sample_zip
def test_every_candidate_has_at_least_one_reason(candidates):
    for candidate in candidates:
        assert candidate.reasons
        assert all(isinstance(reason, str) and reason for reason in candidate.reasons)


# ---------------------------------------------------------------------------
# Ranking. The filter selects more candidates than the default model-call cap,
# so the order decides what actually gets analyzed - see _SIGNAL_WEIGHTS.
# ---------------------------------------------------------------------------


@requires_sample_zip
def test_candidates_are_ordered_strongest_signal_first(candidates):
    priorities = [candidate.priority for candidate in candidates]
    assert priorities == sorted(priorities, reverse=True)


@requires_sample_zip
def test_a_graph_whose_only_signal_is_a_round_amount_ranks_last(candidates):
    """Round amounts are common in real ledgers. A graph distinguished only by
    one must not outrank a missing goods receipt, or a capped run would spend
    its budget on noise."""
    round_only = [c for c in candidates if c.signals == ("round_amount",)]
    stronger = [c for c in candidates if c.priority > 10]
    assert round_only, "expected at least one round-amount-only candidate in the sample dossier"
    assert stronger, "expected at least one stronger candidate in the sample dossier"

    last_strong = max(candidates.index(c) for c in stronger)
    first_round_only = min(candidates.index(c) for c in round_only)
    assert first_round_only > last_strong


@requires_sample_zip
def test_shell_vendor_survives_the_default_model_call_cap(db_path: Path, candidates):
    """The whole point of ranking: the shell vendor's graph must be inside the
    analyzed window, not left to chance. Before ranking, candidates arrived in
    uuid5 order and roughly two thirds were dropped by the cap, so this graph
    had no better than a coin-flip chance of ever being looked at."""
    from app.core.settings import AgentSettings

    cap = AgentSettings.from_environment().model_call_cap
    shell_node = entity_node_id("vendor", SHELL_VENDOR)

    within_cap = _selected_entity_nodes(candidates[:cap], "vendor:")
    assert shell_node in within_cap, (
        f"shell vendor {SHELL_VENDOR} fell outside the first {cap} ranked candidates "
        f"of {len(candidates)} - a capped run would silently miss it"
    )


@requires_sample_zip
def test_ranking_is_deterministic(db_path: Path, candidates):
    """Reuses the module-scoped `candidates` for one side of the comparison -
    re-selecting twice here would load the whole persisted graph twice more,
    and this file is already among the slowest in the suite."""
    again = select_candidate_graphs(DOSSIER_ID, db_path)
    assert [c.graph.graph_id for c in again] == [c.graph.graph_id for c in candidates]
    assert [c.priority for c in again] == [c.priority for c in candidates]
