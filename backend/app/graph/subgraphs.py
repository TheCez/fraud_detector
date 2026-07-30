"""Process-graph enumeration - the transaction clusters an agent walks one by one.

A process graph is *not* a raw weakly-connected component over the whole graph:
high-degree entity nodes (an account referenced by thousands of GL rows, a user who
posted half the journal) would glue nearly everything into one giant component.
Instead, records are clustered by the document_join edges `builder.py` already
computed (one business document = one cluster), and only then is each cluster's
directly-referenced entity nodes attached. See `builder.py`'s module docstring for
the fan-out cap that keeps document ids themselves from becoming hubs.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

import networkx as nx

from app.graph.schema import EdgeType, NodeType

logger = logging.getLogger(__name__)

_NAMESPACE = uuid.NAMESPACE_URL

# A process graph with more member records than this is flagged (never silently
# truncated) - see the hub-node guard test in test_graph_engine.py.
_DEFAULT_MAX_RECORDS_PER_SUBGRAPH = 200


@dataclass(frozen=True)
class ProcessGraph:
    graph_id: str
    record_ids: tuple[str, ...]
    entity_node_ids: tuple[str, ...]
    source_node_ids: tuple[str, ...]
    sink_node_ids: tuple[str, ...]
    record_count: int
    capped: bool
    had_cycle: bool


def _make_graph_id(dossier_id: str, record_ids: tuple[str, ...]) -> str:
    """Deterministic id derived from the sorted member record ids - same pattern
    `agent_analyzer.py` uses for finding ids (uuid5 over a sorted, joined key)."""
    key = dossier_id + "|" + "|".join(record_ids)
    return f"PG-{uuid.uuid5(_NAMESPACE, key).hex[:16]}"


def _induced_out_adjacency(
    graph: nx.MultiDiGraph, induced_nodes: set[str]
) -> dict[str, list[tuple[str, str]]]:
    """Out-edges of ``induced_nodes`` whose target is also in ``induced_nodes``.

    Built directly from the full graph rather than via ``graph.subgraph()``:
    a lazy ``SubGraph`` view's per-call overhead (iterated through networkx's
    backend-dispatch machinery) dominated this module's runtime when called
    once per process graph on a large dossier - profiled at ~9s of a ~20s
    ``build_process_graphs`` run on the real sample dossier, almost all of it
    inside ``is_directed_acyclic_graph``/``topological_sort`` on the view
    rather than in this module's own logic. A process graph's induced node
    set is small, and every node in it already has its real out-edges walked
    directly (not via a view) a few lines above this function's call site -
    the same direct-access pattern reused here.
    """
    adjacency: dict[str, list[tuple[str, str]]] = {node: [] for node in induced_nodes}
    for node in induced_nodes:
        for _source, target, key in graph.out_edges(node, keys=True):
            if target in induced_nodes:
                adjacency[node].append((target, key))
    return adjacency


def _break_cycles_deterministically(
    induced_nodes: set[str], out_adjacency: dict[str, list[tuple[str, str]]]
) -> tuple[dict[str, int], dict[str, int], bool]:
    """In/out degree of the induced subgraph after breaking cycles, plus
    whether it had one.

    Cycles can appear: e.g. vendor entity -(has_receipt)-> goods_receipt record
    -(document_join)-> vendor invoice record -(paid_to)-> vendor entity. Rather
    than crash or silently claim acyclicity, this drops DFS back-edges in a
    fixed, sorted visitation order - a standard, deterministic feedback-arc-set
    approximation. The full edge set (including whatever gets dropped here) is
    untouched in the persisted graph; this exists only to compute in/out degree
    honestly for source/sink node selection.

    Iterative rather than recursive - a per-node stack frame here would put a
    hard, silent cap on how long a chain of document-joined records can be
    before this raises ``RecursionError``.
    """
    out_degree = dict.fromkeys(induced_nodes, 0)
    in_degree = dict.fromkeys(induced_nodes, 0)
    visited: set[str] = set()
    in_stack: set[str] = set()
    dropped = 0

    for start in sorted(induced_nodes):
        if start in visited:
            continue
        visited.add(start)
        in_stack.add(start)
        stack = [(start, iter(sorted(out_adjacency[start])))]

        while stack:
            node, edges = stack[-1]
            pushed = False
            for target, _key in edges:
                if target in in_stack:
                    dropped += 1
                    continue
                out_degree[node] += 1
                in_degree[target] += 1
                if target not in visited:
                    visited.add(target)
                    in_stack.add(target)
                    stack.append((target, iter(sorted(out_adjacency[target]))))
                    pushed = True
                    break
            if not pushed:
                stack.pop()
                in_stack.discard(node)

    return in_degree, out_degree, dropped > 0


def build_process_graphs(
    dossier_id: str,
    graph: nx.MultiDiGraph,
    *,
    max_records_per_subgraph: int = _DEFAULT_MAX_RECORDS_PER_SUBGRAPH,
) -> list[ProcessGraph]:
    """Enumerate process graphs (transaction clusters) for one built dossier graph."""
    record_nodes = [n for n, d in graph.nodes(data=True) if d.get("node_type") == NodeType.record.value]

    join_graph = nx.Graph()
    join_graph.add_nodes_from(record_nodes)
    join_graph.add_edges_from(
        (u, v)
        for u, v, d in graph.edges(data=True)
        if d.get("edge_type") == EdgeType.document_join.value
    )

    process_graphs: list[ProcessGraph] = []
    cycle_count = 0

    for component in nx.connected_components(join_graph):
        record_node_set = set(component)
        record_ids = tuple(sorted(n.split(":", 1)[1] for n in record_node_set))

        entity_node_ids: set[str] = set()
        for rnode in record_node_set:
            for _u, v, d in graph.out_edges(rnode, data=True):
                if graph.nodes[v].get("node_type") == NodeType.entity.value:
                    entity_node_ids.add(v)
            for u, _v, d in graph.in_edges(rnode, data=True):
                if graph.nodes[u].get("node_type") == NodeType.entity.value:
                    entity_node_ids.add(u)

        induced_nodes = record_node_set | entity_node_ids
        out_adjacency = _induced_out_adjacency(graph, induced_nodes)

        in_degree, out_degree, had_cycle = _break_cycles_deterministically(induced_nodes, out_adjacency)
        if had_cycle:
            cycle_count += 1
            logger.debug(
                "dossier %s: cycle detected in process graph over records %s - "
                "broke it deterministically for source/sink computation only",
                dossier_id,
                record_ids,
            )

        source_node_ids = tuple(sorted(n for n in induced_nodes if in_degree[n] == 0))
        sink_node_ids = tuple(sorted(n for n in induced_nodes if out_degree[n] == 0))

        capped = len(record_ids) > max_records_per_subgraph
        if capped:
            logger.warning(
                "dossier %s: process graph %s has %d member records, over the "
                "%d-record cap - reporting in full (never silently truncated) but "
                "flagged as capped",
                dossier_id,
                _make_graph_id(dossier_id, record_ids),
                len(record_ids),
                max_records_per_subgraph,
            )

        process_graphs.append(
            ProcessGraph(
                graph_id=_make_graph_id(dossier_id, record_ids),
                record_ids=record_ids,
                entity_node_ids=tuple(sorted(entity_node_ids)),
                source_node_ids=source_node_ids,
                sink_node_ids=sink_node_ids,
                record_count=len(record_ids),
                capped=capped,
                had_cycle=had_cycle,
            )
        )

    if cycle_count:
        logger.warning(
            "dossier %s: %d of %d process graphs contained a cycle - each was broken "
            "deterministically for source/sink computation only (see debug log for "
            "the specific record ids)",
            dossier_id,
            cycle_count,
            len(process_graphs),
        )

    process_graphs.sort(key=lambda pg: pg.graph_id)
    return process_graphs
