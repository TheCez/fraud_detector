"""Local audit-dossier graph: construction, persistence, and a bounded query API.

`tools` is the public query surface and is deliberately the only way callers should
reach the graph - it reads from SQLite, enforces a limit on every list return, and
hands back plain serializable data rather than NetworkX objects. Both the analysis
agent and the future findings chat agent depend on it, so treat it as a stable
interface.
"""

from app.graph.builder import build_graph
from app.graph.schema import EdgeType, NodeType, entity_node_id, record_node_id
from app.graph.store import load_graph, load_process_graphs, save_graph
from app.graph.subgraphs import ProcessGraph, build_process_graphs
from app.graph.tools import (
    absence_check,
    get_subgraph,
    list_process_graphs,
    neighbors,
    path_between,
    records_for_node,
)

__all__ = [
    "EdgeType",
    "NodeType",
    "ProcessGraph",
    "absence_check",
    "build_graph",
    "build_process_graphs",
    "entity_node_id",
    "get_subgraph",
    "list_process_graphs",
    "load_graph",
    "load_process_graphs",
    "neighbors",
    "path_between",
    "record_node_id",
    "records_for_node",
    "save_graph",
]
