"""Bounded, dossier-scoped query API over the persisted local graph.

This is the seam two future consumers depend on unchanged: an LLM agent that
walks process graphs one by one, and a chat agent/UI that renders them. Every
function here is dossier-scoped, returns plain serializable data (never a
NetworkX object), and enforces an explicit bound - a default limit on every list
return, a maximum path length, no unbounded traversal. An LLM must never be able
to pull an entire dossier into a prompt through this API.

Every function takes optional ``graph``/``process_graphs`` keyword arguments.
A caller that already holds the loaded graph in memory (``GraphAnalyzer``,
which receives it from ``runner.py`` right after building it) supplies it and
the function never touches SQLite at all. A caller with no graph to hand in -
the future findings chat agent, the graph API endpoints - gets one transparently
from ``_GraphCache`` below: a small, bounded, thread-safe cache keyed by
(dossier_id, db_path) and validated against ``get_graph_version``, so repeated
tool calls across one investigation load the graph once instead of once per
call, without ever serving a graph older than the version it claims to be.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path

import networkx as nx

from app.graph.store import get_graph_version, load_graph, load_process_graphs
from app.graph.subgraphs import ProcessGraph
from app.persistence.database import get_record_by_id

# Small and bounded on purpose: a per-dossier graph is ~42k nodes/~110k edges
# on the real sample dossier, so caching more than a handful at once would
# leak memory across dossiers rather than just amortizing one investigation's
# repeated tool calls.
_CACHE_MAX_ENTRIES = 4


class _GraphCacheEntry:
    __slots__ = ("version", "graph", "process_graphs")

    def __init__(self, version: int, graph: nx.MultiDiGraph, process_graphs: list[ProcessGraph]) -> None:
        self.version = version
        self.graph = graph
        self.process_graphs = process_graphs


class _GraphCache:
    """Bounded LRU cache of (graph, process_graphs) per (dossier_id, db_path).

    Validity is checked against ``get_graph_version`` on every access, not
    trusted between accesses - cheap (an indexed single-row lookup) next to a
    multi-second graph rebuild, and it is what guarantees a re-analysis that
    rewrites the persisted graph is never served stale from here: version and
    graph data always commit together (see ``save_graph``), so a version
    mismatch is the only signal this cache needs.
    """

    def __init__(self, max_entries: int = _CACHE_MAX_ENTRIES) -> None:
        self._max_entries = max_entries
        self._lock = threading.Lock()
        self._entries: "OrderedDict[tuple[str, str], _GraphCacheEntry]" = OrderedDict()

    def get(self, dossier_id: str, db_path: Path) -> tuple[nx.MultiDiGraph, list[ProcessGraph]]:
        key = (dossier_id, str(db_path))
        current_version = get_graph_version(db_path, dossier_id)

        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry.version == current_version:
                self._entries.move_to_end(key)
                return entry.graph, entry.process_graphs

        graph = load_graph(db_path, dossier_id)
        process_graphs = load_process_graphs(db_path, dossier_id)

        with self._lock:
            self._entries[key] = _GraphCacheEntry(current_version, graph, process_graphs)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

        return graph, process_graphs

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_CACHE = _GraphCache()


def _cached_graph(dossier_id: str, db_path: Path) -> nx.MultiDiGraph:
    graph, _process_graphs = _CACHE.get(dossier_id, db_path)
    return graph


def _cached_process_graphs(dossier_id: str, db_path: Path) -> list[ProcessGraph]:
    _graph, process_graphs = _CACHE.get(dossier_id, db_path)
    return process_graphs


_HARD_MAX_LIMIT = 200
_DEFAULT_LIST_LIMIT = 50
_DEFAULT_NEIGHBOR_LIMIT = 20
_DEFAULT_RECORDS_LIMIT = 20
_DEFAULT_MAX_PATH_LEN = 6
_HARD_MAX_PATH_LEN = 12


def _bounded_limit(limit: int | None, default: int) -> int:
    if limit is None:
        limit = default
    return max(1, min(limit, _HARD_MAX_LIMIT))


def list_process_graphs(
    dossier_id: str,
    db_path: Path,
    *,
    limit: int = _DEFAULT_LIST_LIMIT,
    offset: int = 0,
    process_graphs: list[ProcessGraph] | None = None,
) -> list[dict]:
    """Summaries only - graph_id, record_count, and flags. Use get_subgraph for
    the full node/edge payload of one graph.

    Supply ``process_graphs`` when the caller already has it in memory to skip
    the load entirely; otherwise it comes from the bounded per-dossier cache."""
    bounded_limit = _bounded_limit(limit, _DEFAULT_LIST_LIMIT)
    if process_graphs is None:
        process_graphs = _cached_process_graphs(dossier_id, db_path)
    page = process_graphs[offset : offset + bounded_limit]
    return [
        {
            "graph_id": pg.graph_id,
            "record_count": pg.record_count,
            "entity_count": len(pg.entity_node_ids),
            "source_node_ids": list(pg.source_node_ids),
            "sink_node_ids": list(pg.sink_node_ids),
            "capped": pg.capped,
            "had_cycle": pg.had_cycle,
        }
        for pg in page
    ]


def get_subgraph(
    dossier_id: str,
    db_path: Path,
    graph_id: str,
    *,
    graph: nx.MultiDiGraph | None = None,
    process_graphs: list[ProcessGraph] | None = None,
) -> dict | None:
    """Full node/edge payload for one process graph, serializable to JSON.

    Supply ``graph``/``process_graphs`` when the caller already has them in
    memory to skip the load entirely; otherwise they come from the bounded
    per-dossier cache."""
    if process_graphs is None:
        process_graphs = _cached_process_graphs(dossier_id, db_path)
    target = next((pg for pg in process_graphs if pg.graph_id == graph_id), None)
    if target is None:
        return None

    if graph is None:
        graph = _cached_graph(dossier_id, db_path)
    record_node_ids = {f"record:{rid}" for rid in target.record_ids}
    node_ids = record_node_ids | set(target.entity_node_ids)

    nodes = [_node_payload(graph, n) for n in sorted(node_ids) if graph.has_node(n)]
    edges = [_edge_payload(u, v, data) for u, v, data in graph.subgraph(node_ids).edges(data=True)]

    return {
        "graph_id": target.graph_id,
        "record_count": target.record_count,
        "source_node_ids": list(target.source_node_ids),
        "sink_node_ids": list(target.sink_node_ids),
        "capped": target.capped,
        "had_cycle": target.had_cycle,
        "nodes": nodes,
        "edges": edges,
    }


def neighbors(
    dossier_id: str,
    db_path: Path,
    node_id: str,
    *,
    edge_type: str | None = None,
    limit: int = _DEFAULT_NEIGHBOR_LIMIT,
    graph: nx.MultiDiGraph | None = None,
) -> list[dict]:
    """Bounded one-hop neighbors of node_id, in either direction.

    Supply ``graph`` when the caller already has it in memory to skip the
    load entirely; otherwise it comes from the bounded per-dossier cache."""
    bounded_limit = _bounded_limit(limit, _DEFAULT_NEIGHBOR_LIMIT)
    if graph is None:
        graph = _cached_graph(dossier_id, db_path)
    if not graph.has_node(node_id):
        return []

    results = []
    for _u, v, data in graph.out_edges(node_id, data=True):
        if edge_type is not None and data.get("edge_type") != edge_type:
            continue
        results.append(
            {"node": _node_payload(graph, v), "edge": _edge_payload(node_id, v, data), "direction": "out"}
        )
    for u, _v, data in graph.in_edges(node_id, data=True):
        if edge_type is not None and data.get("edge_type") != edge_type:
            continue
        results.append(
            {"node": _node_payload(graph, u), "edge": _edge_payload(u, node_id, data), "direction": "in"}
        )

    results.sort(key=lambda r: (r["direction"], r["edge"]["edge_type"], r["node"]["node_id"]))
    return results[:bounded_limit]


def records_for_node(
    dossier_id: str,
    db_path: Path,
    node_id: str,
    *,
    limit: int = _DEFAULT_RECORDS_LIMIT,
    graph: nx.MultiDiGraph | None = None,
) -> list[dict]:
    """Normalized records backing node_id - itself if it's a record node, plus
    every record any incident edge cites as justification.

    Supply ``graph`` when the caller already has it in memory to skip the
    load entirely; otherwise it comes from the bounded per-dossier cache."""
    bounded_limit = _bounded_limit(limit, _DEFAULT_RECORDS_LIMIT)
    if graph is None:
        graph = _cached_graph(dossier_id, db_path)
    if not graph.has_node(node_id):
        return []

    record_ids: list[str] = []
    seen: set[str] = set()

    def _add(rid: str) -> None:
        if rid not in seen:
            seen.add(rid)
            record_ids.append(rid)

    if node_id.startswith("record:"):
        _add(node_id.split(":", 1)[1])

    for _u, _v, data in graph.out_edges(node_id, data=True):
        for rid in data.get("record_ids", ()):
            _add(rid)
    for _u, _v, data in graph.in_edges(node_id, data=True):
        for rid in data.get("record_ids", ()):
            _add(rid)

    records = []
    for rid in record_ids[:bounded_limit]:
        row = get_record_by_id(db_path, dossier_id, rid)
        if row is not None:
            records.append(
                {
                    "record_id": row["record_id"],
                    "record_type": row["record_type"],
                    "date": row["date"],
                    "amount": row["amount"],
                    "currency": row["currency"],
                    "file_id": row["file_id"],
                }
            )
    return records


def path_between(
    dossier_id: str,
    db_path: Path,
    source_node_id: str,
    target_node_id: str,
    *,
    max_len: int = _DEFAULT_MAX_PATH_LEN,
    graph: nx.MultiDiGraph | None = None,
) -> dict | None:
    """Shortest path between two nodes, bounded to max_len hops.

    Traverses the undirected view so record/entity edge direction never blocks
    finding a path that clearly exists in the underlying graph.

    Supply ``graph`` when the caller already has it in memory to skip the
    load entirely; otherwise it comes from the bounded per-dossier cache.
    """
    bounded_max_len = max(1, min(max_len, _HARD_MAX_PATH_LEN))
    if graph is None:
        graph = _cached_graph(dossier_id, db_path)
    if not graph.has_node(source_node_id) or not graph.has_node(target_node_id):
        return None

    undirected = graph.to_undirected(as_view=True)
    try:
        node_path = nx.shortest_path(undirected, source_node_id, target_node_id)
    except nx.NetworkXNoPath:
        return None

    if len(node_path) - 1 > bounded_max_len:
        return None

    edges = []
    for a, b in zip(node_path, node_path[1:]):
        edge_data = graph.get_edge_data(a, b) or graph.get_edge_data(b, a) or {}
        if edge_data:
            _key, data = next(iter(edge_data.items()))
            edges.append(_edge_payload(a, b, data))

    return {
        "nodes": [_node_payload(graph, n) for n in node_path],
        "edges": edges,
        "length": len(node_path) - 1,
    }


def absence_check(
    dossier_id: str,
    db_path: Path,
    node_id: str,
    expected_edge_type: str,
    *,
    graph: nx.MultiDiGraph | None = None,
) -> dict:
    """Answer "does node_id have an edge of expected_edge_type" directly, with
    enough detail for a caller to explain the conclusion either way (e.g. "this
    vendor has no goods receipt").

    Supply ``graph`` when the caller already has it in memory to skip the
    load entirely; otherwise it comes from the bounded per-dossier cache."""
    if graph is None:
        graph = _cached_graph(dossier_id, db_path)
    if not graph.has_node(node_id):
        return {
            "node_id": node_id,
            "expected_edge_type": expected_edge_type,
            "present": False,
            "matching_edges": [],
            "explanation": f"Node {node_id} does not exist in this dossier's graph.",
        }

    matches = []
    for _u, v, data in graph.out_edges(node_id, data=True):
        if data.get("edge_type") == expected_edge_type:
            matches.append(_edge_payload(node_id, v, data))
    for u, _v, data in graph.in_edges(node_id, data=True):
        if data.get("edge_type") == expected_edge_type:
            matches.append(_edge_payload(u, node_id, data))

    present = bool(matches)
    if present:
        explanation = (
            f"{node_id} has {len(matches)} '{expected_edge_type}' edge(s), backed by "
            f"{sum(len(m['record_ids']) for m in matches)} record reference(s)."
        )
    else:
        explanation = f"{node_id} has no '{expected_edge_type}' edge in this dossier's graph."

    return {
        "node_id": node_id,
        "expected_edge_type": expected_edge_type,
        "present": present,
        "matching_edges": matches[:_DEFAULT_LIST_LIMIT],
        "explanation": explanation,
    }


def _node_payload(graph: nx.MultiDiGraph, node_id: str) -> dict:
    attrs = dict(graph.nodes[node_id])
    attrs["node_id"] = node_id
    return attrs


def _edge_payload(source: str, target: str, data: dict) -> dict:
    return {
        "edge_id": data.get("edge_id"),
        "source": source,
        "target": target,
        "edge_type": data.get("edge_type"),
        "record_ids": list(data.get("record_ids", ())),
    }
