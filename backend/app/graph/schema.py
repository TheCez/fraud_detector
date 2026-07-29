"""Node/edge schema for the local audit-dossier graph.

Two kinds of node:
- record nodes - one per normalized record (a posting, invoice, receipt, master-data
  change). Node id is the bare ``record_id``.
- entity nodes - one per distinct ``(entity_type, entity_id)`` pair seen in the
  dossier. Node id is a namespaced composite (``"vendor:209101"``) so entities of
  different types that happen to share a number can never collide.

Every edge carries the ``record_ids`` that justify it - a rendered graph and a
future chat answer both have to be traceable back to source rows.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

_NAMESPACE = uuid.NAMESPACE_URL


class NodeType(str, Enum):
    record = "record"
    entity = "entity"


class EdgeType(str, Enum):
    """Known edge types.

    ``posted_by`` .. ``counter_account`` mirror the relationship labels normalization
    already produces (see ``app/normalization/parsers/gdpdu_txt.py`` and
    ``csv_parser.py``). ``references`` is the generic fallback for an ``EntityRef``
    with no matching relationship label. ``document_join`` and ``has_receipt`` are
    the two inferred edge types this module adds.
    """

    posted_by = "posted_by"
    paid_to = "paid_to"
    approved_by = "approved_by"
    changed_by = "changed_by"
    to_account = "to_account"
    capitalized_to = "capitalized_to"
    received_from = "received_from"
    sold_to = "sold_to"
    created_by = "created_by"
    processed_by = "processed_by"
    counter_account = "counter_account"
    references = "references"
    document_join = "document_join"
    has_receipt = "has_receipt"


def record_node_id(record_id: str) -> str:
    return f"record:{record_id}"


def entity_node_id(entity_type: str, entity_id: str) -> str:
    """Namespaced composite id - keeps e.g. a vendor and an account with the same
    number from colliding into one node."""
    return f"{entity_type}:{entity_id}"


@dataclass(frozen=True)
class Node:
    node_id: str
    node_type: NodeType
    record_type: str | None = None
    date: str | None = None
    period: str | None = None
    amount: float | None = None
    currency: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None
    label: str | None = None
    file_id: str | None = None
    relative_path: str | None = None
    row_number: int | None = None

    def to_attrs(self) -> dict:
        """Attribute dict suitable for ``nx.MultiDiGraph.add_node(node_id, **attrs)``."""
        return {
            "node_type": self.node_type.value,
            "record_type": self.record_type,
            "date": self.date,
            "period": self.period,
            "amount": self.amount,
            "currency": self.currency,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "label": self.label,
            "file_id": self.file_id,
            "relative_path": self.relative_path,
            "row_number": self.row_number,
        }

    @staticmethod
    def from_attrs(node_id: str, attrs: dict) -> "Node":
        return Node(
            node_id=node_id,
            node_type=NodeType(attrs["node_type"]),
            record_type=attrs.get("record_type"),
            date=attrs.get("date"),
            period=attrs.get("period"),
            amount=attrs.get("amount"),
            currency=attrs.get("currency"),
            entity_type=attrs.get("entity_type"),
            entity_id=attrs.get("entity_id"),
            label=attrs.get("label"),
            file_id=attrs.get("file_id"),
            relative_path=attrs.get("relative_path"),
            row_number=attrs.get("row_number"),
        )


@dataclass(frozen=True)
class Edge:
    edge_id: str
    source: str
    target: str
    edge_type: EdgeType
    record_ids: tuple[str, ...]

    def to_attrs(self) -> dict:
        return {
            "edge_id": self.edge_id,
            "edge_type": self.edge_type.value,
            "record_ids": self.record_ids,
        }


def make_edge_id(dossier_id: str, source: str, target: str, edge_type: EdgeType, record_ids: Iterable[str]) -> str:
    """Deterministic edge id - same inputs always hash to the same id.

    Follows the pattern ``agent_analyzer.py`` uses for finding ids: uuid5 over a
    sorted, joined key. Used as the MultiDiGraph edge key so re-running the builder
    replaces rather than duplicates an edge.
    """
    sorted_ids = "|".join(sorted(set(record_ids)))
    key = f"{dossier_id}|{source}|{target}|{edge_type.value}|{sorted_ids}"
    return uuid.uuid5(_NAMESPACE, key).hex


def make_edge(
    dossier_id: str,
    source: str,
    target: str,
    edge_type: EdgeType,
    record_ids: Iterable[str],
) -> Edge:
    rid_tuple = tuple(sorted(set(record_ids)))
    edge_id = make_edge_id(dossier_id, source, target, edge_type, rid_tuple)
    return Edge(edge_id=edge_id, source=source, target=target, edge_type=edge_type, record_ids=rid_tuple)
