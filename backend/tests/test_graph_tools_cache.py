"""Tests for app/graph/tools.py's supplied-graph bypass and bounded cache.

Every function in tools.py used to call load_graph()/load_process_graphs()
unconditionally on entry - on the real sample dossier that is a ~3.4s SQLite
read plus NetworkX rebuild, paid again on every single tool call. This module
proves the fix: a caller that already holds the graph in memory (GraphAnalyzer)
can supply it and skip the load entirely, and a caller that cannot (the future
chat agent, graph API endpoints) gets one transparently from a small per-dossier
cache that never serves a graph older than the version it claims to be.

Reuses the session-scoped sample_graph/sample_process_graphs fixtures from
conftest.py (already built once from the real sample dossier) rather than
rebuilding the extract -> normalize -> build_graph pipeline again - this suite
is already slow (see conftest.py's comment on sample_saved_db_path). Every
test below persists those fixtures onto its own private tmp_path database:
app/graph/tools.py keeps one process-wide cache, and this suite must never
mutate the shared sample_saved_db_path fixture other test modules also read
from read-only.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import app.graph.tools as tools
from app.graph.schema import EdgeType, entity_node_id
from app.graph.store import save_graph
from tests.conftest import SAMPLE_DOSSIER_ID, requires_sample_zip

DOSSIER_ID = SAMPLE_DOSSIER_ID

# Real vendor with 4 has_receipt edges - see test_graph_engine.py, which
# verifies this same fact directly against the graph.
REAL_VENDOR = "209112"


def _private_copy(tmp_path: Path, sample_db_path: Path, name: str) -> Path:
    """A copy of the real sample dossier's registry.db (normalized_records,
    graph tables, everything) that only this test touches - records_for_node
    needs normalized_records to resolve records, which a bare save_graph onto
    an empty file would not have. Safe to save_graph onto again; the shared
    session-scoped sample_db_path/sample_saved_db_path fixtures are never
    written to here."""
    db_path = tmp_path / name
    shutil.copy(sample_db_path, db_path)
    return db_path


@pytest.fixture
def private_db_path(tmp_path: Path, sample_db_path: Path, sample_graph, sample_process_graphs) -> Path:
    """The real sample dossier's graph, persisted onto a private copy of the
    registry db - safe to call save_graph on again without affecting any
    other test module."""
    db_path = _private_copy(tmp_path, sample_db_path, "cache_test.sqlite")
    save_graph(db_path, DOSSIER_ID, sample_graph, sample_process_graphs)
    return db_path


@requires_sample_zip
def test_supplied_graph_never_touches_sqlite(monkeypatch, private_db_path, sample_graph, sample_process_graphs):
    """A tool call with a supplied graph performs no SQLite graph load."""
    vendor_node = entity_node_id("vendor", REAL_VENDOR)

    def _fail(*_args, **_kwargs):
        raise AssertionError("load_graph/load_process_graphs must not run when graph/process_graphs is supplied")

    monkeypatch.setattr(tools, "load_graph", _fail)
    monkeypatch.setattr(tools, "load_process_graphs", _fail)

    neighbor_result = tools.neighbors(DOSSIER_ID, private_db_path, vendor_node, graph=sample_graph)
    assert neighbor_result

    page = tools.list_process_graphs(DOSSIER_ID, private_db_path, limit=5, process_graphs=sample_process_graphs)
    assert len(page) == 5

    payload = tools.get_subgraph(
        DOSSIER_ID,
        private_db_path,
        sample_process_graphs[0].graph_id,
        graph=sample_graph,
        process_graphs=sample_process_graphs,
    )
    assert payload is not None

    absence = tools.absence_check(DOSSIER_ID, private_db_path, vendor_node, "has_receipt", graph=sample_graph)
    assert absence["present"] is True


@requires_sample_zip
def test_repeated_calls_without_supplied_graph_reuse_the_cache(monkeypatch, private_db_path):
    """Repeated tool calls without a supplied graph reuse the cache rather
    than each reloading the graph from SQLite."""
    vendor_node = entity_node_id("vendor", REAL_VENDOR)
    real_load_graph = tools.load_graph
    real_load_process_graphs = tools.load_process_graphs
    load_graph_calls: list[int] = []
    load_process_graphs_calls: list[int] = []

    def _counting_load_graph(db_path, dossier_id):
        load_graph_calls.append(1)
        return real_load_graph(db_path, dossier_id)

    def _counting_load_process_graphs(db_path, dossier_id):
        load_process_graphs_calls.append(1)
        return real_load_process_graphs(db_path, dossier_id)

    monkeypatch.setattr(tools, "load_graph", _counting_load_graph)
    monkeypatch.setattr(tools, "load_process_graphs", _counting_load_process_graphs)

    # A mix of tool calls representative of one candidate-graph traversal,
    # none of them supplying graph/process_graphs.
    assert tools.neighbors(DOSSIER_ID, private_db_path, vendor_node)
    assert tools.absence_check(DOSSIER_ID, private_db_path, vendor_node, "has_receipt")["present"] is True
    assert tools.records_for_node(DOSSIER_ID, private_db_path, vendor_node)
    assert tools.list_process_graphs(DOSSIER_ID, private_db_path, limit=5)

    assert len(load_graph_calls) == 1, "graph was reloaded more than once across repeated tool calls"
    assert len(load_process_graphs_calls) == 1, "process graphs were reloaded more than once"


@requires_sample_zip
def test_rebuild_and_resave_busts_the_cache(tmp_path: Path, sample_db_path: Path, sample_graph, sample_process_graphs):
    """After the graph is rebuilt and re-saved for the same dossier, the next
    tool call sees the new graph, not the cached old one.

    This is the test that matters most per the task brief: a stale-cache bug
    here would show up as an agent reasoning over out-of-date evidence.
    """
    db_path = _private_copy(tmp_path, sample_db_path, "staleness.sqlite")
    save_graph(db_path, DOSSIER_ID, sample_graph, sample_process_graphs)

    vendor_node = entity_node_id("vendor", REAL_VENDOR)
    before = tools.absence_check(DOSSIER_ID, db_path, vendor_node, "has_receipt")
    assert before["present"] is True  # also populates the cache

    # Simulate a re-analysis that rebuilt the graph without this vendor's
    # goods-receipt edges - a real subset of the same sample-dossier graph
    # (not an invented fixture), analogous to the vendor losing its receipts
    # in a corrected export.
    rebuilt_graph = sample_graph.copy()
    receipt_edges = [
        (u, v, k)
        for u, v, k, data in rebuilt_graph.out_edges(vendor_node, keys=True, data=True)
        if data.get("edge_type") == EdgeType.has_receipt.value
    ]
    assert receipt_edges, "test setup assumption broke: vendor no longer has has_receipt edges"
    rebuilt_graph.remove_edges_from(receipt_edges)
    save_graph(db_path, DOSSIER_ID, rebuilt_graph, sample_process_graphs)

    after = tools.absence_check(DOSSIER_ID, db_path, vendor_node, "has_receipt")
    assert after["present"] is False, "stale cache served the pre-rebuild graph"


@requires_sample_zip
def test_two_dossiers_sharing_one_database_do_not_share_cache_entries(
    tmp_path: Path, sample_db_path: Path, sample_graph, sample_process_graphs
):
    """Two different dossiers, even sharing one SQLite file (as the real app
    does - every graph table is dossier-scoped), do not share cache entries."""
    db_path = _private_copy(tmp_path, sample_db_path, "multi_dossier.sqlite")
    vendor_node = entity_node_id("vendor", REAL_VENDOR)
    other_dossier_id = "sample-dossier-other"

    modified_graph = sample_graph.copy()
    receipt_edges = [
        (u, v, k)
        for u, v, k, data in modified_graph.out_edges(vendor_node, keys=True, data=True)
        if data.get("edge_type") == EdgeType.has_receipt.value
    ]
    modified_graph.remove_edges_from(receipt_edges)

    save_graph(db_path, DOSSIER_ID, sample_graph, sample_process_graphs)
    save_graph(db_path, other_dossier_id, modified_graph, sample_process_graphs)

    real_result = tools.absence_check(DOSSIER_ID, db_path, vendor_node, "has_receipt")
    other_result = tools.absence_check(other_dossier_id, db_path, vendor_node, "has_receipt")

    assert real_result["present"] is True
    assert other_result["present"] is False
