"""Tests for the local graph engine (`app/graph/`).

Driven by the real sample dossier (sample_data/Uebungsdaten_Muster_Verpackungen.zip),
end to end: extract -> build manifest -> normalize -> build graph. Follows the style
of test_gdpdu_normalization.py and test_xlsx_parser.py.

Identifiers used below are known-good per the task brief and were independently
verified against the real sample data (Kreditoren/Lieferantenbuchungen.txt,
Sachkonten/Sachkontobuchungen.txt, Begleitdokumente/Wareneingangsliste_2025.csv,
AV/Anlagen.txt) - the sealed ground-truth file is never read.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app.graph.builder import build_graph
from app.graph.schema import EdgeType, NodeType, entity_node_id, record_node_id
from app.graph.store import load_graph, load_process_graphs, save_graph
from app.graph.subgraphs import build_process_graphs
from app.graph.tools import (
    absence_check,
    get_subgraph,
    list_process_graphs,
    neighbors,
    path_between,
    records_for_node,
)
from app.ingestion.manifest import build_manifest
from app.normalization.orchestrator import normalize_dossier

SAMPLE_ZIP = (
    Path(__file__).resolve().parent.parent.parent
    / "sample_data"
    / "Uebungsdaten_Muster_Verpackungen.zip"
)

requires_sample_zip = pytest.mark.skipif(
    not SAMPLE_ZIP.exists(), reason="sample ZIP not available"
)

DOSSIER_ID = "graph-engine-sample-dossier"

# Shell vendor (no goods receipt) - see task brief.
SHELL_VENDOR = "209101"
# Legitimate counterpart vendor (has goods receipts).
REAL_VENDOR = "209112"
# Split-payment vendor whose GL leg uses a decomposed composite SACHKONTONUMMER.
SPLIT_VENDOR = "200007"
SPLIT_ACCOUNT = "330000"
SPLIT_PAYMENT_AMOUNTS = {9780.0, 9820.0, 9750.0, 9690.0}

REPAIR_ASSETS = (
    "040000-000191",
    "040000-000192",
    "040000-000194",
    "040000-000196",
    "060000-000193",
    "060000-000195",
)


@pytest.fixture(scope="module")
def extracted_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    target = tmp_path_factory.mktemp("graph_extract")
    with zipfile.ZipFile(SAMPLE_ZIP) as zf:
        zf.extractall(target)
    (root,) = [p for p in target.iterdir() if p.is_dir()]
    return root


@pytest.fixture(scope="module")
def db_path(tmp_path_factory: pytest.TempPathFactory, extracted_dir: Path) -> Path:
    """Run the real manifest + normalization pipeline, returning the SQLite path
    normalize_dossier populates (workspace_root.parent.parent / "registry.db")."""
    manifest = build_manifest(extracted_dir, DOSSIER_ID)
    workspace_root = tmp_path_factory.mktemp("graph_workspace") / "dossiers" / DOSSIER_ID
    workspace_root.mkdir(parents=True)
    normalize_dossier(extracted_dir, workspace_root, manifest, DOSSIER_ID)
    return workspace_root.parent.parent / "registry.db"


@pytest.fixture(scope="module")
def graph(db_path: Path):
    return build_graph(DOSSIER_ID, db_path)


@pytest.fixture(scope="module")
def process_graphs(graph):
    return build_process_graphs(DOSSIER_ID, graph)


@pytest.fixture(scope="module")
def saved_db_path(db_path: Path, graph, process_graphs) -> Path:
    """db_path with the graph persisted - tools.py reads back from storage, it
    never takes an in-memory graph directly."""
    save_graph(db_path, DOSSIER_ID, graph, process_graphs)
    return db_path


# ---------------------------------------------------------------------------
# Required assertion 1: shell vendor connects to all 10 postings
# ---------------------------------------------------------------------------


@requires_sample_zip
def test_shell_vendor_node_connects_to_all_postings(graph):
    vendor_node = entity_node_id("vendor", SHELL_VENDOR)
    assert graph.has_node(vendor_node)

    connected_records = {
        u for u, _v, d in graph.in_edges(vendor_node, data=True) if d["edge_type"] == EdgeType.paid_to.value
    }
    assert len(connected_records) == 10


# ---------------------------------------------------------------------------
# Required assertion 2: has_receipt absence/presence works both directions
# ---------------------------------------------------------------------------


@requires_sample_zip
def test_shell_vendor_has_no_receipt_while_real_vendor_does(graph):
    shell_node = entity_node_id("vendor", SHELL_VENDOR)
    real_node = entity_node_id("vendor", REAL_VENDOR)

    shell_receipts = [v for _u, v, d in graph.out_edges(shell_node, data=True) if d["edge_type"] == EdgeType.has_receipt.value]
    real_receipts = [v for _u, v, d in graph.out_edges(real_node, data=True) if d["edge_type"] == EdgeType.has_receipt.value]

    assert shell_receipts == []
    assert len(real_receipts) == 4


@requires_sample_zip
def test_absence_check_matches_direct_graph_inspection(saved_db_path: Path, graph):
    """Guards against the absence_check tool and the graph disagreeing with each
    other - a test that only checks one side would prove nothing."""
    shell_result = absence_check(DOSSIER_ID, saved_db_path, entity_node_id("vendor", SHELL_VENDOR), "has_receipt")
    real_result = absence_check(DOSSIER_ID, saved_db_path, entity_node_id("vendor", REAL_VENDOR), "has_receipt")

    assert shell_result["present"] is False
    assert real_result["present"] is True
    assert len(real_result["matching_edges"]) == 4


# ---------------------------------------------------------------------------
# Required assertion 3: repair-named assets connect to their postings
# ---------------------------------------------------------------------------


@requires_sample_zip
def test_repair_assets_connect_to_asset_postings(graph):
    for asset_id in REPAIR_ASSETS:
        asset_node = entity_node_id("asset", asset_id)
        assert graph.has_node(asset_node), f"missing asset node for {asset_id}"
        connected = list(graph.in_edges(asset_node)) + list(graph.out_edges(asset_node))
        assert connected, f"asset {asset_id} has no connected records"


@requires_sample_zip
def test_bare_asset_group_codes_do_not_dangle(graph):
    """021000/040000/060000/062000 are GL group codes on depreciation rows, not
    real assets - they must not appear as asset nodes."""
    for bare_code in ("021000", "040000", "060000", "062000"):
        assert not graph.has_node(entity_node_id("asset", bare_code))
        # They should still be reachable as account nodes (from ANLAGENGRUPPE).
        assert graph.has_node(entity_node_id("account", bare_code))


# ---------------------------------------------------------------------------
# Required assertion 4: decomposed composite account joins the split payments
# ---------------------------------------------------------------------------


@requires_sample_zip
def test_split_vendor_payments_join_via_decomposed_composite_account(graph):
    account_node = entity_node_id("account", SPLIT_ACCOUNT)
    vendor_node = entity_node_id("vendor", SPLIT_VENDOR)
    assert graph.has_node(account_node)
    assert graph.has_node(vendor_node)

    account_records = {u for u, _v in graph.in_edges(account_node)}
    vendor_records = {u for u, _v in graph.in_edges(vendor_node)}
    shared_records = account_records & vendor_records

    matched_amounts = {
        graph.nodes[rid]["amount"]
        for rid in shared_records
        if graph.nodes[rid].get("date") == "2025-10-14"
    }
    assert SPLIT_PAYMENT_AMOUNTS <= matched_amounts


# ---------------------------------------------------------------------------
# Required assertion 5: hub-node guard
# ---------------------------------------------------------------------------


@requires_sample_zip
def test_no_process_subgraph_holds_more_than_a_modest_fraction_of_records(graph, process_graphs):
    total_records = sum(1 for _n, d in graph.nodes(data=True) if d["node_type"] == NodeType.record.value)
    assert total_records > 0

    largest = max(pg.record_count for pg in process_graphs)
    fraction = largest / total_records

    # The design goal is clusters of a handful of records (one business document);
    # 5% would already indicate a hub has glued unrelated transactions together.
    assert fraction < 0.05, (
        f"largest process subgraph has {largest} of {total_records} records "
        f"({fraction:.1%}) - a hub node has likely collapsed multiple transactions"
    )


@requires_sample_zip
def test_every_record_belongs_to_exactly_one_process_graph(graph, process_graphs):
    total_records = {n for n, d in graph.nodes(data=True) if d["node_type"] == NodeType.record.value}
    covered = [rid for pg in process_graphs for rid in pg.record_ids]
    assert len(covered) == len(set(covered)), "a record appears in more than one process graph"
    assert set(record_node_id(rid) for rid in covered) == total_records


# ---------------------------------------------------------------------------
# Required assertion 6: determinism across builds
# ---------------------------------------------------------------------------


@requires_sample_zip
def test_building_twice_yields_identical_ids(db_path: Path):
    graph_a = build_graph(DOSSIER_ID, db_path)
    graph_b = build_graph(DOSSIER_ID, db_path)

    assert sorted(graph_a.nodes()) == sorted(graph_b.nodes())
    edge_ids_a = sorted(d["edge_id"] for _u, _v, d in graph_a.edges(data=True))
    edge_ids_b = sorted(d["edge_id"] for _u, _v, d in graph_b.edges(data=True))
    assert edge_ids_a == edge_ids_b

    process_graphs_a = sorted(pg.graph_id for pg in build_process_graphs(DOSSIER_ID, graph_a))
    process_graphs_b = sorted(pg.graph_id for pg in build_process_graphs(DOSSIER_ID, graph_b))
    assert process_graphs_a == process_graphs_b


# ---------------------------------------------------------------------------
# Required assertion 7: save/load round-trips losslessly
# ---------------------------------------------------------------------------


@requires_sample_zip
def test_save_then_load_roundtrips_losslessly(tmp_path: Path, graph, process_graphs):
    roundtrip_db = tmp_path / "roundtrip.sqlite"
    save_graph(roundtrip_db, DOSSIER_ID, graph, process_graphs)

    loaded_graph = load_graph(roundtrip_db, DOSSIER_ID)
    loaded_process_graphs = load_process_graphs(roundtrip_db, DOSSIER_ID)

    assert sorted(loaded_graph.nodes()) == sorted(graph.nodes())
    assert loaded_graph.number_of_edges() == graph.number_of_edges()

    original_edges = {d["edge_id"]: tuple(sorted(d["record_ids"])) for _u, _v, d in graph.edges(data=True)}
    loaded_edges = {d["edge_id"]: tuple(sorted(d["record_ids"])) for _u, _v, d in loaded_graph.edges(data=True)}
    assert original_edges == loaded_edges

    assert sorted(pg.graph_id for pg in loaded_process_graphs) == sorted(pg.graph_id for pg in process_graphs)


@requires_sample_zip
def test_save_is_idempotent_no_duplicate_rows(tmp_path: Path, graph, process_graphs):
    """Re-running a build (and save) must not duplicate rows."""
    roundtrip_db = tmp_path / "idempotent.sqlite"
    save_graph(roundtrip_db, DOSSIER_ID, graph, process_graphs)
    save_graph(roundtrip_db, DOSSIER_ID, graph, process_graphs)

    loaded_graph = load_graph(roundtrip_db, DOSSIER_ID)
    assert loaded_graph.number_of_nodes() == graph.number_of_nodes()
    assert loaded_graph.number_of_edges() == graph.number_of_edges()


# ---------------------------------------------------------------------------
# Required assertion 8: every edge is provenance-backed
# ---------------------------------------------------------------------------


@requires_sample_zip
def test_every_edge_has_at_least_one_backing_record_id(graph):
    for _u, _v, data in graph.edges(data=True):
        assert data.get("record_ids"), f"edge {data.get('edge_id')} has no backing record_ids"


# ---------------------------------------------------------------------------
# tools.py - bounded, dossier-scoped query API
# ---------------------------------------------------------------------------


@requires_sample_zip
def test_list_process_graphs_is_bounded_and_dossier_scoped(saved_db_path: Path):
    page = list_process_graphs(DOSSIER_ID, saved_db_path, limit=5)
    assert len(page) == 5

    unbounded_attempt = list_process_graphs(DOSSIER_ID, saved_db_path, limit=10_000)
    assert len(unbounded_attempt) <= 200  # hard cap, never the whole dossier

    other_dossier = list_process_graphs("no-such-dossier", saved_db_path, limit=5)
    assert other_dossier == []


@requires_sample_zip
def test_get_subgraph_returns_serializable_nodes_and_edges(saved_db_path: Path, process_graphs):
    target = process_graphs[0]
    payload = get_subgraph(DOSSIER_ID, saved_db_path, target.graph_id)

    assert payload is not None
    assert payload["graph_id"] == target.graph_id
    assert len(payload["nodes"]) >= target.record_count
    for edge in payload["edges"]:
        assert isinstance(edge["record_ids"], list)

    assert get_subgraph(DOSSIER_ID, saved_db_path, "does-not-exist") is None


@requires_sample_zip
def test_neighbors_is_bounded_and_filters_by_edge_type(saved_db_path: Path):
    vendor_node = entity_node_id("vendor", SHELL_VENDOR)
    all_neighbors = neighbors(DOSSIER_ID, saved_db_path, vendor_node, limit=3)
    assert len(all_neighbors) <= 3

    paid_to_only = neighbors(DOSSIER_ID, saved_db_path, vendor_node, edge_type="paid_to", limit=100)
    assert all(n["edge"]["edge_type"] == "paid_to" for n in paid_to_only)
    assert len(paid_to_only) == 10


@requires_sample_zip
def test_records_for_node_resolves_real_normalized_records(saved_db_path: Path):
    vendor_node = entity_node_id("vendor", REAL_VENDOR)
    records = records_for_node(DOSSIER_ID, saved_db_path, vendor_node, limit=5)
    assert 0 < len(records) <= 5
    for record in records:
        assert record["record_type"]


@requires_sample_zip
def test_path_between_is_bounded_by_max_len(saved_db_path: Path):
    vendor_node = entity_node_id("vendor", SHELL_VENDOR)
    account_node = entity_node_id("account", "673000")  # posting expense account

    result = path_between(DOSSIER_ID, saved_db_path, vendor_node, account_node, max_len=6)
    assert result is not None
    assert result["length"] <= 6

    impossible = path_between(DOSSIER_ID, saved_db_path, vendor_node, account_node, max_len=1)
    assert impossible is None


@requires_sample_zip
def test_absence_check_on_unknown_node(saved_db_path: Path):
    result = absence_check(DOSSIER_ID, saved_db_path, "vendor:does-not-exist", "has_receipt")
    assert result["present"] is False
    assert "does not exist" in result["explanation"]
