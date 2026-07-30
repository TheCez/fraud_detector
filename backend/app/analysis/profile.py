"""One-pass dossier profile: counts and relationships learned directly from
the graph and records in front of it.

Every field this module produces is a measured fact about this dossier - a
record count, a first/last date, an edge-type count including a *zero* count,
a fill rate. None of it is a judgement about whether a fact is suspicious.
See ``entry_brief.py``'s module docstring and the T5 task brief for the
data-quality-vs-fraud-judgement line this module and its consumer must stay
on the right side of: "this vendor has 0 has_receipt edges" is something this
module may say; "which means it is fraudulent" is not something anything in
this codebase says, here or anywhere else.

Built in exactly two passes over persisted data - one over
``iter_records_by_dossier``, one over the built graph's edges - plus cheap
in-memory aggregation over the (small, already-loaded) list of process
graphs and per-record facts collected during the first pass. Never calls
``graph.subgraph()`` here: see ``app/graph/subgraphs.py``'s module docstring
for why doing that once per process graph was a measured performance
disaster on this codebase's ~110k-edge real dossier. (``entry_brief.py``
calls it exactly once per rendered entry, which is a different, cheap thing -
see its docstring.)
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import networkx as nx

from app.graph.schema import EdgeType, NodeType
from app.graph.store import load_graph, load_process_graphs
from app.graph.subgraphs import ProcessGraph
from app.persistence.database import iter_records_by_dossier

# Record types that carry a master-data change with a separate changer/approver
# column pair. A structural fact about which files these are - used only to
# count how many such records name an entity, never to inspect who the
# changer/approver were.
_MASTER_DATA_RECORD_TYPES = ("master_data", "master_change")

# Small fixed cap on how many master-data record ids one EntityProfile carries
# verbatim (see EntityProfile.master_data_record_ids) - so a pathological
# entity with many master-data records cannot blow up a brief. The full count
# is unaffected by this cap - it stays in master_data_reference_count.
MASTER_DATA_RECORD_ID_BUDGET = 5

# Entity types that represent an external business counterparty rather than an
# internal reference. See app/normalization/models.py's EntityRef docstring:
# "vendor", "customer", "account", "user", "asset", "document", "cost_center"
# are the entity types normalization extracts: account/user/asset/cost_center
# are internal references, vendor/customer are the only two that represent a
# party outside the reporting entity.
_COUNTERPARTY_ENTITY_TYPES = ("vendor", "customer")

# The same document-identifier columns app/graph/builder.py joins process
# graphs on (BELEGNUMMER, BUCHUNGSNUMMER, DOKUMENT, RECHNUNGSNUMMER). Reused
# here only to answer "does this record cite any document reference at all" -
# a provenance/completeness fact, not a join and not a business rule about
# which record types must carry one.
_DOCUMENT_REFERENCE_FIELDS = ("BELEGNUMMER", "BUCHUNGSNUMMER", "DOKUMENT", "RECHNUNGSNUMMER")

# The four identity dimensions a ledger entry needs before anyone could form a
# view of it at all - a date, an amount, a named counterparty, a document
# reference. Order is fixed so every rendering of these facts is deterministic.
COMPLETENESS_DIMENSIONS = ("date", "amount", "counterparty", "document_reference")

_QUANTILE_POINTS = (0.0, 0.25, 0.5, 0.75, 0.9, 0.99, 1.0)


def _get_ci(data: dict[str, Any], field_name: str) -> Any:
    """Case-insensitive dict lookup - column names come from the source export
    verbatim and casing is not guaranteed to match our constants' casing."""
    if field_name in data:
        return data[field_name]
    upper = field_name.upper()
    for key, value in data.items():
        if key.upper() == upper:
            return value
    return None


def _non_empty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


@dataclass
class EntityProfile:
    """Dossier-wide counts for one entity node - never what happened in any
    one entry, only what is true of this entity across the whole dossier."""

    entity_node_id: str
    record_count: int
    first_date: str | None
    last_date: str | None
    total_amount: float
    mean_amount: float | None
    edge_type_counts: dict[str, int]
    master_data_reference_count: int
    # The record ids (sorted, capped at MASTER_DATA_RECORD_ID_BUDGET) of the
    # master_data/master_change records that name this entity - lets a brief
    # render each one in full rather than only the count above, which never
    # applies this cap.
    master_data_record_ids: tuple[str, ...]
    # Every other entity node (account, user, asset, vendor, customer - any
    # entity type) this entity co-occurs on at least one shared record with.
    # Not the same thing as a vendor/customer "counterparty" (see
    # _COUNTERPARTY_ENTITY_TYPES / COMPLETENESS_DIMENSIONS' "counterparty"
    # dimension above) - this counts every co-occurring entity node, internal
    # references included.
    co_occurring_entity_count: int


@dataclass
class ShapeProfile:
    """Stats for one entry shape - the sorted tuple of record types a process
    graph carries."""

    shape: tuple[str, ...]
    entry_count: int
    record_types: tuple[str, ...]
    edge_type_counts: dict[str, int]
    completeness_counts: dict[str, int]


@dataclass
class EntryCompleteness:
    """Per-entry identity facts, aggregated across every record in the entry
    (not per record - see the module docstring on why per-record evaluation
    would misreport a coherent entry as full of holes) plus the per-field
    fill rate across the entry's own records."""

    graph_id: str
    has_date: bool
    date_record_ids: tuple[str, ...]
    has_amount: bool
    amount_record_ids: tuple[str, ...]
    has_counterparty: bool
    counterparty_record_ids: tuple[str, ...]
    has_document_reference: bool
    document_reference_record_ids: tuple[str, ...]
    field_fill_rates: dict[str, tuple[int, int]]


@dataclass
class DossierProfile:
    dossier_id: str
    record_count: int
    total_entries: int
    record_type_counts: dict[str, int]
    amount_quantiles: dict[str, float]
    periods: tuple[str, ...]
    entities: dict[str, EntityProfile]
    shapes: dict[tuple[str, ...], ShapeProfile]
    entry_shape: dict[str, tuple[str, ...]]
    entry_edge_types: dict[str, tuple[str, ...]]
    entry_completeness: dict[str, EntryCompleteness]


def _quantiles(sorted_values: list[float]) -> dict[str, float]:
    if not sorted_values:
        return {}
    n = len(sorted_values)
    result: dict[str, float] = {}
    for point in _QUANTILE_POINTS:
        index = min(n - 1, int(round(point * (n - 1))))
        result[f"p{int(round(point * 100))}"] = sorted_values[index]
    return result


def build_profile(
    dossier_id: str,
    db_path: Path,
    *,
    graph: nx.MultiDiGraph | None = None,
    process_graphs: list[ProcessGraph] | None = None,
) -> DossierProfile:
    """Build the dossier profile in one pass over records and one over edges.

    Signature mirrors ``app.analysis.prefilter.select_candidate_graphs``:
    accepts an already-built graph and process-graph list so a caller that
    just built them (``runner.py``) never re-reads them from SQLite; falls
    back to loading both when omitted.
    """
    if graph is None:
        graph = load_graph(db_path, dossier_id)
    if process_graphs is None:
        process_graphs = load_process_graphs(db_path, dossier_id)

    record_type_counts: Counter[str] = Counter()
    record_meta: dict[str, tuple[str, str | None, float | None]] = {}
    record_counterparty_types: dict[str, frozenset[str]] = {}
    record_has_document_ref: dict[str, bool] = {}
    amounts: list[float] = []
    periods: set[str] = set()
    record_count = 0

    # Record -> owning process graph. Built before pass 1 (it needs only the
    # already-loaded process_graphs, nothing pass 1 discovers) so pass 1 can
    # accumulate each entry's field-fill-rate counters as records stream,
    # instead of retaining every one of the dossier's ~32.8k records' full
    # field dict for the whole build just to compute this afterward. Every
    # record node belongs to exactly one process graph - subgraphs.py's
    # join_graph adds every record node even when it joins to nothing, so
    # this mapping is total.
    record_to_graph: dict[str, str] = {}
    for process_graph in process_graphs:
        for rid in process_graph.record_ids:
            record_to_graph[rid] = process_graph.graph_id

    # graph_id -> column -> [filled_count, applicable_count]. Accumulated in
    # place as pass 1 streams so no record's field dict outlives its own
    # iteration.
    entry_field_counts: dict[str, dict[str, list[int]]] = {}

    # --- Pass 1: one streamed read of every normalized record. ---
    for row in iter_records_by_dossier(db_path, dossier_id):
        record_count += 1
        record_id = row["record_id"]
        record_type = row["record_type"]
        record_type_counts[record_type] += 1
        record_meta[record_id] = (record_type, row["date"], row["amount"])
        if row["amount"] is not None:
            amounts.append(row["amount"])

        parsed = json.loads(row["data_json"])
        period = parsed.get("period")
        if period:
            periods.add(period)

        data = parsed.get("data") or {}
        entities = parsed.get("entities") or []
        record_counterparty_types[record_id] = frozenset(
            entity_type
            for entity_type in (entity.get("entity_type") for entity in entities)
            if entity_type in _COUNTERPARTY_ENTITY_TYPES
        )
        record_has_document_ref[record_id] = any(
            _non_empty(_get_ci(data, field_name)) for field_name in _DOCUMENT_REFERENCE_FIELDS
        )

        gid = record_to_graph.get(record_id)
        if gid is not None:
            column_counts = entry_field_counts.setdefault(gid, {})
            for column, value in data.items():
                counter = column_counts.setdefault(column, [0, 0])
                counter[1] += 1
                if _non_empty(value):
                    counter[0] += 1

    amounts.sort()
    amount_quantiles = _quantiles(amounts)

    # Each process graph's shape (the sorted tuple of its member records'
    # types) - needs record_meta, which pass 1 just populated, so this stays
    # after it.
    graph_shape: dict[str, tuple[str, ...]] = {}
    for process_graph in process_graphs:
        shape = tuple(
            sorted(record_meta[rid][0] for rid in process_graph.record_ids if rid in record_meta)
        )
        graph_shape[process_graph.graph_id] = shape

    # --- Pass 2: one walk of every edge in the built graph. ---
    entity_record_ids: dict[str, set[str]] = {}
    entity_edge_counts: dict[str, Counter[str]] = {}
    record_entities: dict[str, set[str]] = {}
    graph_edge_presence: dict[str, set[str]] = {}

    for source, target, data in graph.edges(data=True):
        edge_type = data.get("edge_type")
        record_ids = data.get("record_ids") or ()

        graphs_touched: set[str] = set()
        for rid in record_ids:
            gid = record_to_graph.get(rid)
            if gid is not None:
                graphs_touched.add(gid)
        for gid in graphs_touched:
            graph_edge_presence.setdefault(gid, set()).add(edge_type)

        for node_id in (source, target):
            if graph.nodes[node_id].get("node_type") != NodeType.entity.value:
                continue
            entity_edge_counts.setdefault(node_id, Counter())[edge_type] += 1
            bucket = entity_record_ids.setdefault(node_id, set())
            for rid in record_ids:
                bucket.add(rid)
                record_entities.setdefault(rid, set()).add(node_id)

    # --- Finalize per-entity profiles from what the edge pass collected. ---
    entities: dict[str, EntityProfile] = {}
    for node_id, attrs in graph.nodes(data=True):
        if attrs.get("node_type") != NodeType.entity.value:
            continue
        rec_ids = entity_record_ids.get(node_id, set())
        dates = [record_meta[rid][1] for rid in rec_ids if rid in record_meta and record_meta[rid][1]]
        entity_amounts = [
            record_meta[rid][2] for rid in rec_ids if rid in record_meta and record_meta[rid][2] is not None
        ]
        master_record_ids = sorted(
            rid for rid in rec_ids if rid in record_meta and record_meta[rid][0] in _MASTER_DATA_RECORD_TYPES
        )
        master_count = len(master_record_ids)
        counterparties: set[str] = set()
        for rid in rec_ids:
            counterparties |= record_entities.get(rid, set())
        counterparties.discard(node_id)

        counts = entity_edge_counts.get(node_id, Counter())
        full_counts = {edge_type.value: counts.get(edge_type.value, 0) for edge_type in EdgeType}

        entities[node_id] = EntityProfile(
            entity_node_id=node_id,
            record_count=len(rec_ids),
            first_date=min(dates) if dates else None,
            last_date=max(dates) if dates else None,
            total_amount=sum(entity_amounts),
            mean_amount=(sum(entity_amounts) / len(entity_amounts)) if entity_amounts else None,
            edge_type_counts=full_counts,
            master_data_reference_count=master_count,
            master_data_record_ids=tuple(master_record_ids[:MASTER_DATA_RECORD_ID_BUDGET]),
            co_occurring_entity_count=len(counterparties),
        )

    # --- Per-entry completeness: aggregate across each entry's own records. ---
    entry_completeness: dict[str, EntryCompleteness] = {}
    for process_graph in process_graphs:
        record_ids = process_graph.record_ids

        date_record_ids = tuple(rid for rid in record_ids if record_meta.get(rid, (None, None, None))[1])
        amount_record_ids = tuple(
            rid for rid in record_ids if record_meta.get(rid, (None, None, None))[2] is not None
        )
        counterparty_record_ids = tuple(rid for rid in record_ids if record_counterparty_types.get(rid))
        document_reference_record_ids = tuple(rid for rid in record_ids if record_has_document_ref.get(rid))

        column_counts = entry_field_counts.get(process_graph.graph_id, {})
        field_fill_rates: dict[str, tuple[int, int]] = {
            column: (filled, applicable)
            for column, (filled, applicable) in sorted(column_counts.items())
        }

        entry_completeness[process_graph.graph_id] = EntryCompleteness(
            graph_id=process_graph.graph_id,
            has_date=bool(date_record_ids),
            date_record_ids=date_record_ids,
            has_amount=bool(amount_record_ids),
            amount_record_ids=amount_record_ids,
            has_counterparty=bool(counterparty_record_ids),
            counterparty_record_ids=counterparty_record_ids,
            has_document_reference=bool(document_reference_record_ids),
            document_reference_record_ids=document_reference_record_ids,
            field_fill_rates=field_fill_rates,
        )

    # --- Per-shape aggregation: entry count, edge-type coverage, completeness coverage. ---
    shape_to_graph_ids: dict[tuple[str, ...], list[str]] = {}
    for graph_id, shape in graph_shape.items():
        shape_to_graph_ids.setdefault(shape, []).append(graph_id)

    shapes: dict[tuple[str, ...], ShapeProfile] = {}
    for shape, graph_ids in shape_to_graph_ids.items():
        edge_counter: Counter[str] = Counter()
        completeness_counter: Counter[str] = Counter()
        for graph_id in graph_ids:
            edge_counter.update(graph_edge_presence.get(graph_id, ()))
            completeness = entry_completeness.get(graph_id)
            if completeness is None:
                continue
            for dimension in COMPLETENESS_DIMENSIONS:
                if getattr(completeness, f"has_{dimension}"):
                    completeness_counter[dimension] += 1

        shapes[shape] = ShapeProfile(
            shape=shape,
            entry_count=len(graph_ids),
            record_types=tuple(sorted(set(shape))),
            edge_type_counts=dict(edge_counter),
            completeness_counts={
                dimension: completeness_counter.get(dimension, 0) for dimension in COMPLETENESS_DIMENSIONS
            },
        )

    entry_edge_types = {
        graph_id: tuple(sorted(edge_types)) for graph_id, edge_types in graph_edge_presence.items()
    }

    return DossierProfile(
        dossier_id=dossier_id,
        record_count=record_count,
        total_entries=len(process_graphs),
        record_type_counts=dict(record_type_counts),
        amount_quantiles=amount_quantiles,
        periods=tuple(sorted(periods)),
        entities=entities,
        shapes=shapes,
        entry_shape=graph_shape,
        entry_edge_types=entry_edge_types,
        entry_completeness=entry_completeness,
    )


__all__ = [
    "COMPLETENESS_DIMENSIONS",
    "MASTER_DATA_RECORD_ID_BUDGET",
    "DossierProfile",
    "EntityProfile",
    "EntryCompleteness",
    "ShapeProfile",
    "build_profile",
]
