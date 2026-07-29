"""Deterministic, in-memory graph construction for one dossier.

No LLM, no network calls - pure graph construction from already-normalized
records in the ``normalized_records`` SQLite table.

Two edge families:

- Direct edges - one per ``EntityRef`` on a record, straight from normalization's
  ``entities``/``relationships`` output.
- Inferred edges - ``document_join`` (records across different files that share a
  business-document identifier) and ``has_receipt`` (a vendor posting matched
  against a goods-receipt record). See the module-level constants below for the
  exact join fields and the hub-avoidance fan-out cap.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx

from app.graph.schema import (
    EdgeType,
    Node,
    NodeType,
    entity_node_id,
    make_edge,
    record_node_id,
)
from app.persistence.database import iter_records_by_dossier

logger = logging.getLogger(__name__)

# Column names that carry a shared business-document identifier, verified against
# the real GDPdU/GoBD sample export: Sachkontobuchungen/Lieferantenbuchungen/
# Kundenbuchungen carry BELEGNUMMER + BUCHUNGSNUMMER + DOKUMENT; Anlagenbuchungen
# carries BELEGNUMMER; Wareneingangsliste/Warenausgangsliste/Fakturajournal carry
# RECHNUNGSNUMMER (which equals the vendor/customer ledger's BUCHUNGSNUMMER for the
# same invoice). Extend this list, not the join logic, if a new file format needs
# to join in - this is the extension point for further document-number joins.
_DOCUMENT_ID_FIELDS = ("BELEGNUMMER", "BUCHUNGSNUMMER", "DOKUMENT", "RECHNUNGSNUMMER")

# A document id referenced by more records than this is a batch/carryforward tag
# (e.g. "AB-2024" opening-balance carryforward, "AfA" generic depreciation-run
# marker), not a single business transaction - joining on it would recreate the
# hub-node problem this module otherwise avoids by clustering on document ids
# instead of raw connected components. Verified against the real sample: genuine
# per-transaction ids top out at ~12 shared records; "AB-2024" and "AfA" are the
# only outliers (145+ and 48 respectively), cleanly separated from everything else.
_MAX_DOCUMENT_ID_FANOUT = 20

# Relationship labels that normalization already recognizes, and the entity_type
# each one is expected to point at - used so record -> entity edges pick up the
# semantic label without accidentally matching an unrelated entity that happens to
# share the same id string.
_RELATIONSHIP_ENTITY_TYPE = {
    "posted_by": "user",
    "changed_by": "user",
    "approved_by": "user",
    "created_by": "user",
    "processed_by": "user",
    "paid_to": "vendor",
    "received_from": "vendor",
    "sold_to": "customer",
    "to_account": "account",
    "counter_account": "account",
    "capitalized_to": "account",
}


def _is_bare_asset_group_code(entity_id: str) -> bool:
    """True for a bare GL group code (e.g. "040000") rather than a real asset.

    Real AV/Anlagen.txt asset numbers in the sample are always
    "<group>-<six digit suffix>". Bare group codes appear on Anlagenbuchungen.txt
    depreciation-summary rows and must not become dangling asset nodes with no
    matching master record - see the brief's "hub-node problem" section.
    """
    return "-" not in entity_id


def _get_ci(data: dict, field_name: str):
    """Case-insensitive dict lookup - column names come from index.xml/CSV headers
    verbatim and casing is not guaranteed to match our constants' casing."""
    if field_name in data:
        return data[field_name]
    upper = field_name.upper()
    for key, value in data.items():
        if key.upper() == upper:
            return value
    return None


def _document_ids(data: dict) -> set[str]:
    ids: set[str] = set()
    for field_name in _DOCUMENT_ID_FIELDS:
        value = _get_ci(data, field_name)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            ids.add(text)
    return ids


@dataclass
class _RecordView:
    record_id: str
    record_type: str
    date: str | None
    data: dict
    entities: list[tuple[str, str]] = field(default_factory=list)
    doc_ids: set[str] = field(default_factory=set)


def build_graph(dossier_id: str, db_path: Path) -> nx.MultiDiGraph:
    """Build the full local graph for one dossier.

    Pass 1 adds a record node per normalized record, an entity node per distinct
    (entity_type, entity_id), and direct edges for every EntityRef. Pass 2 adds
    document_join edges between records that share a business-document id and,
    where the whole document group agrees on exactly one user, lets a record with
    no user column of its own (e.g. a vendor posting) inherit a posted_by edge -
    never fabricated, always backed by both the record and the record it borrowed
    the fact from. Pass 3 adds has_receipt edges from a vendor entity node to any
    goods-receipt record matched on vendor + document number.
    """
    graph = nx.MultiDiGraph()
    views: dict[str, _RecordView] = {}
    doc_fanout: dict[str, int] = {}

    for row in iter_records_by_dossier(db_path, dossier_id):
        parsed = json.loads(row["data_json"])
        record_id = row["record_id"]
        source = parsed.get("source") or {}
        data = parsed.get("data") or {}
        relationships = parsed.get("relationships") or {}

        rnode_id = record_node_id(record_id)
        graph.add_node(
            rnode_id,
            **Node(
                node_id=rnode_id,
                node_type=NodeType.record,
                record_type=row["record_type"],
                date=row["date"],
                period=parsed.get("period"),
                amount=row["amount"],
                currency=row["currency"],
                file_id=source.get("file_id") or row["file_id"],
                relative_path=source.get("relative_path"),
                row_number=source.get("row_number"),
            ).to_attrs(),
        )

        # Dedupe entity refs within this record and remap bare asset-group codes.
        unique_entities: dict[tuple[str, str], str | None] = {}
        for ent in parsed.get("entities") or []:
            etype = ent.get("entity_type")
            eid = ent.get("entity_id")
            if not etype or not eid:
                continue
            if etype == "asset" and _is_bare_asset_group_code(eid):
                etype = "account"
            key = (etype, eid)
            label = ent.get("label")
            if key not in unique_entities or (unique_entities[key] is None and label):
                unique_entities[key] = label

        for (etype, eid), label in unique_entities.items():
            enode_id = entity_node_id(etype, eid)
            if not graph.has_node(enode_id):
                graph.add_node(
                    enode_id,
                    **Node(
                        node_id=enode_id,
                        node_type=NodeType.entity,
                        entity_type=etype,
                        entity_id=eid,
                        label=label,
                    ).to_attrs(),
                )
            elif label and not graph.nodes[enode_id].get("label"):
                graph.nodes[enode_id]["label"] = label

            matching_labels = sorted(
                rel_label
                for rel_label, value in relationships.items()
                if value == eid and _RELATIONSHIP_ENTITY_TYPE.get(rel_label) == etype
            )
            if matching_labels:
                for rel_label in matching_labels:
                    edge = make_edge(dossier_id, rnode_id, enode_id, EdgeType(rel_label), [record_id])
                    graph.add_edge(rnode_id, enode_id, key=edge.edge_id, **edge.to_attrs())
            else:
                edge = make_edge(dossier_id, rnode_id, enode_id, EdgeType.references, [record_id])
                graph.add_edge(rnode_id, enode_id, key=edge.edge_id, **edge.to_attrs())

        doc_ids = _document_ids(data)
        for doc_id in doc_ids:
            doc_fanout[doc_id] = doc_fanout.get(doc_id, 0) + 1

        views[record_id] = _RecordView(
            record_id=record_id,
            record_type=row["record_type"],
            date=row["date"],
            data=data,
            entities=list(unique_entities.keys()),
            doc_ids=doc_ids,
        )

    _add_document_join_edges(graph, dossier_id, views, doc_fanout)
    _add_has_receipt_edges(graph, dossier_id, views)

    return graph


def _add_document_join_edges(
    graph: nx.MultiDiGraph,
    dossier_id: str,
    views: dict[str, _RecordView],
    doc_fanout: dict[str, int],
) -> None:
    excluded = {doc_id for doc_id, count in doc_fanout.items() if count > _MAX_DOCUMENT_ID_FANOUT}
    if excluded:
        logger.warning(
            "dossier %s: excluding %d document id(s) from clustering - referenced by "
            "more records than the %d-record fan-out cap (batch/carryforward markers, "
            "not single transactions): %s",
            dossier_id,
            len(excluded),
            _MAX_DOCUMENT_ID_FANOUT,
            sorted(excluded)[:10],
        )

    doc_groups: dict[str, list[str]] = {}
    for record_id, view in views.items():
        for doc_id in view.doc_ids:
            if doc_id not in excluded:
                doc_groups.setdefault(doc_id, []).append(record_id)

    for record_ids in doc_groups.values():
        if len(record_ids) < 2:
            continue

        # Chronological chain (falling back to record_id for a stable tiebreak) -
        # a strict total order over the group is trivially acyclic.
        ordered = sorted(record_ids, key=lambda rid: (views[rid].date or "", views[rid].record_type, rid))
        for a, b in zip(ordered, ordered[1:]):
            edge = make_edge(dossier_id, record_node_id(a), record_node_id(b), EdgeType.document_join, [a, b])
            graph.add_edge(record_node_id(a), record_node_id(b), key=edge.edge_id, **edge.to_attrs())

        # posted_by inheritance: only when every record in the group that *does*
        # carry a user agrees on exactly the same one. Otherwise leave the gap -
        # guessing between two different real users would be its own kind of
        # fabrication.
        users_by_record: dict[str, str] = {}
        for rid in record_ids:
            for etype, eid in views[rid].entities:
                if etype == "user":
                    users_by_record[rid] = entity_node_id("user", eid)
                    break

        distinct_users = set(users_by_record.values())
        if len(distinct_users) != 1:
            continue
        (only_user_node,) = distinct_users
        source_rid = min(users_by_record)

        for rid in record_ids:
            if rid in users_by_record:
                continue
            rnode_id = record_node_id(rid)
            edge = make_edge(dossier_id, rnode_id, only_user_node, EdgeType.posted_by, [rid, source_rid])
            graph.add_edge(rnode_id, only_user_node, key=edge.edge_id, **edge.to_attrs())


def _add_has_receipt_edges(
    graph: nx.MultiDiGraph,
    dossier_id: str,
    views: dict[str, _RecordView],
) -> None:
    vendor_postings_by_key: dict[tuple[str, str], list[str]] = {}
    for record_id, view in views.items():
        if view.record_type != "vendor_posting":
            continue
        vendor_id = _get_ci(view.data, "LIEFERANTENKONTONUMMER")
        document_no = _get_ci(view.data, "BUCHUNGSNUMMER")
        if vendor_id and document_no:
            key = (str(vendor_id).strip(), str(document_no).strip())
            vendor_postings_by_key.setdefault(key, []).append(record_id)

    for record_id, view in views.items():
        if view.record_type != "goods_receipt":
            continue
        vendor_id = _get_ci(view.data, "KREDITOR")
        document_no = _get_ci(view.data, "RECHNUNGSNUMMER")
        if not vendor_id or not document_no:
            continue

        key = (str(vendor_id).strip(), str(document_no).strip())
        matches = vendor_postings_by_key.get(key)
        if not matches:
            continue

        vendor_node = entity_node_id("vendor", str(vendor_id).strip())
        if not graph.has_node(vendor_node):
            continue

        receipt_node = record_node_id(record_id)
        for vendor_posting_rid in sorted(matches):
            edge = make_edge(
                dossier_id,
                vendor_node,
                receipt_node,
                EdgeType.has_receipt,
                [vendor_posting_rid, record_id],
            )
            graph.add_edge(vendor_node, receipt_node, key=edge.edge_id, **edge.to_attrs())
