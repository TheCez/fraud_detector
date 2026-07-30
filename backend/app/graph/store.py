"""Persist a built graph to SQLite so it outlives the analysis run.

Round-tripping (``save_graph`` then ``load_graph``) must be lossless, including
each edge's ``record_ids`` - a later PR's chat agent and the UI read graphs back
without ever rebuilding them.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import networkx as nx

from app.graph.subgraphs import ProcessGraph
from app.persistence.database import (
    bulk_insert_graph_edges,
    bulk_insert_graph_nodes,
    bulk_insert_process_graphs,
    bump_graph_version,
    clear_graph_tables,
    get_graph_edges,
    get_graph_nodes,
    get_graph_version,
    get_process_graphs,
    init_graph_tables,
)


def save_graph(
    db_path: Path,
    dossier_id: str,
    graph: nx.MultiDiGraph,
    process_graphs: list[ProcessGraph],
) -> None:
    node_rows = [
        {
            "node_id": node_id,
            "node_type": attrs.get("node_type"),
            "data_json": json.dumps(attrs, ensure_ascii=False),
        }
        for node_id, attrs in graph.nodes(data=True)
    ]
    edge_rows = [
        {
            "edge_id": attrs["edge_id"],
            "source": source,
            "target": target,
            "edge_type": attrs["edge_type"],
            "record_ids_json": json.dumps(list(attrs["record_ids"]), ensure_ascii=False),
        }
        for source, target, attrs in graph.edges(data=True)
    ]
    process_graph_rows = [
        {
            "graph_id": pg.graph_id,
            "data_json": json.dumps(
                {
                    "graph_id": pg.graph_id,
                    "record_ids": list(pg.record_ids),
                    "entity_node_ids": list(pg.entity_node_ids),
                    "source_node_ids": list(pg.source_node_ids),
                    "sink_node_ids": list(pg.sink_node_ids),
                    "record_count": pg.record_count,
                    "capped": pg.capped,
                    "had_cycle": pg.had_cycle,
                },
                ensure_ascii=False,
            ),
            "record_count": pg.record_count,
        }
        for pg in process_graphs
    ]

    # One connection, one transaction for table creation, clearing this
    # dossier's previous rows, all three bulk inserts, and the version bump -
    # previously each step opened its own connection and committed separately.
    # Clearing first (rather than relying on INSERT OR REPLACE alone) is what
    # makes a rebuild with fewer or different nodes/edges than before actually
    # replace the persisted graph instead of leaving old rows orphaned
    # alongside the new ones. Bumping the version in the same transaction is
    # what lets app/graph/tools.py's cache trust it: a reader can never
    # observe a new version without also seeing the graph data it corresponds
    # to.
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    try:
        init_graph_tables(db_path, con=con)
        clear_graph_tables(db_path, dossier_id, con=con)
        bulk_insert_graph_nodes(db_path, dossier_id, node_rows, con=con)
        bulk_insert_graph_edges(db_path, dossier_id, edge_rows, con=con)
        bulk_insert_process_graphs(db_path, dossier_id, process_graph_rows, con=con)
        bump_graph_version(db_path, dossier_id, con=con)
        con.commit()
    finally:
        con.close()


def load_graph(db_path: Path, dossier_id: str) -> nx.MultiDiGraph:
    graph = nx.MultiDiGraph()

    for row in get_graph_nodes(db_path, dossier_id):
        attrs = json.loads(row["data_json"])
        graph.add_node(row["node_id"], **attrs)

    for row in get_graph_edges(db_path, dossier_id):
        record_ids = tuple(json.loads(row["record_ids_json"]))
        graph.add_edge(
            row["source"],
            row["target"],
            key=row["edge_id"],
            edge_id=row["edge_id"],
            edge_type=row["edge_type"],
            record_ids=record_ids,
        )

    return graph


def load_process_graphs(db_path: Path, dossier_id: str) -> list[ProcessGraph]:
    process_graphs = []
    for row in get_process_graphs(db_path, dossier_id):
        data = json.loads(row["data_json"])
        process_graphs.append(
            ProcessGraph(
                graph_id=data["graph_id"],
                record_ids=tuple(data["record_ids"]),
                entity_node_ids=tuple(data["entity_node_ids"]),
                source_node_ids=tuple(data["source_node_ids"]),
                sink_node_ids=tuple(data["sink_node_ids"]),
                record_count=data["record_count"],
                capped=data["capped"],
                had_cycle=data["had_cycle"],
            )
        )
    return process_graphs


__all__ = ["save_graph", "load_graph", "load_process_graphs", "get_graph_version"]
