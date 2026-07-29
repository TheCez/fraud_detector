"""
Normalized record models - the universal intermediate format.

These models define the universal intermediate format between raw GDPdU/GoBD
data and the local knowledge graph (`app/graph/`). Each NormalizedRecord becomes
a graph node; EntityRef entries become labeled edges connecting nodes.

Design principles:
- Self-contained JSON objects with clear entity types
- Relationships expressed as labeled fields (posted_by, paid_to, approved_by)
- Stable IDs and full source provenance for auditability
- Exportable as JSONL, one record per line, for streaming graph construction
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class RecordType(str, Enum):
    """Types of normalized records - maps to a record-node type in the local graph."""

    journal_entry = "journal_entry"  # GL postings (Sachkontobuchungen)
    vendor_posting = "vendor_posting"  # Kreditoren postings
    customer_posting = "customer_posting"  # Debitoren postings
    asset_record = "asset_record"  # AV/Anlagen
    asset_posting = "asset_posting"  # AV/Anlagenbuchungen
    master_data = "master_data"  # Sachkonten, Lieferanten, Kunden
    master_change = "master_change"  # Stammdatenaenderungen
    goods_receipt = "goods_receipt"  # Wareneingangsliste
    goods_dispatch = "goods_dispatch"  # Warenausgangsliste
    invoice = "invoice"  # Fakturajournal
    permission = "permission"  # Berechtigungsauswertung
    document_text = "document_text"  # PDF/DOCX content blocks
    balance = "balance"  # Saldenliste
    open_item = "open_item"  # OP-Listen


class EntityRef(BaseModel):
    """
    A typed reference to a domain entity - becomes a graph edge in the local graph.

    The entity_type+entity_id pair identifies a unique node in the graph.
    Multiple records referencing the same pair will share a single node,
    creating the connectivity that powers graph-based analysis.
    """

    entity_type: str = Field(
        description=(
            'Domain entity type: "vendor", "customer", "account", '
            '"user", "asset", "document", "cost_center"'
        )
    )
    entity_id: str = Field(
        description='Business identifier, e.g. "200007", "MV-U05", "040000"'
    )
    label: str | None = Field(
        default=None,
        description="Human-readable display name if available",
    )


class SourceProvenance(BaseModel):
    """
    Exact source location for traceability.

    Every claim in the system must be traceable back to a specific location
    in the original uploaded evidence. This model captures that location
    with enough precision to highlight the exact row, cell, or paragraph.
    """

    file_id: str = Field(description="Stable ID of the source file in the manifest")
    relative_path: str = Field(
        description="Path relative to dossier root, e.g. 'Daten/Buchungen.csv'"
    )
    row_number: int | None = Field(
        default=None, description="1-based row number in tabular data"
    )
    row_end: int | None = Field(
        default=None,
        description="End row for multi-row records (inclusive)",
    )
    sheet: str | None = Field(
        default=None, description="Worksheet name for Excel files"
    )
    page: int | None = Field(
        default=None, description="1-based page number for PDF documents"
    )
    paragraph: int | None = Field(
        default=None, description="Paragraph index for DOCX documents"
    )
    columns: list[str] | None = Field(
        default=None,
        description="Column names that contributed to this record",
    )


class NormalizedRecord(BaseModel):
    """
    A single normalized record - the universal unit for local graph construction.

    Each record maps to a node in the local graph (`app/graph/builder.py`).
    Entity references (entities field) become edges to shared entity nodes.
    The relationships dict provides human-readable labeled edges.
    The data dict holds the actual parsed values from the source.

    Graph construction flow:
        NormalizedRecord -> JSONL line -> SQLite -> app.graph.builder.build_graph
    """

    record_id: str = Field(
        description="Stable UUID5 derived from (dossier_id, file_id, row/key)"
    )
    dossier_id: str = Field(description="ID of the parent dossier/upload")
    record_type: RecordType = Field(
        description="Semantic type - determines which graph node type is created"
    )
    source: SourceProvenance = Field(
        description="Exact location in the original evidence"
    )

    # Local graph fields
    entities: list[EntityRef] = Field(
        default_factory=list,
        description="All entity references - each becomes a graph edge to a shared node",
    )
    relationships: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Labeled relationships for graph edges, e.g. "
            '{"posted_by": "MV-U05", "paid_to": "200007", "approved_by": "MV-U01"}'
        ),
    )

    # Parsed data
    data: dict[str, str | int | float | None] = Field(
        default_factory=dict,
        description="The actual field values extracted from the source row/block",
    )

    # Temporal
    date: str | None = Field(
        default=None,
        description="ISO 8601 date of the record (primary date, e.g. posting date)",
    )
    period: str | None = Field(
        default=None,
        description="Accounting period, e.g. '2020-01' or 'Q3-2020'",
    )

    # Financial
    amount: float | None = Field(
        default=None, description="Primary monetary amount if applicable"
    )
    currency: str | None = Field(
        default=None, description="ISO 4217 currency code, e.g. 'EUR'"
    )

    # Text content (for document-type records)
    text_content: str | None = Field(
        default=None,
        description="Extracted text content for PDF/DOCX document blocks",
    )


class NormalizedOutput(BaseModel):
    """
    Output from a single file's normalization.

    Represents all records extracted from one source file. Written as JSONL
    where each line is one NormalizedRecord, for streaming graph construction.
    """

    file_id: str = Field(description="ID of the source file that was normalized")
    relative_path: str = Field(
        description="Original relative path within the dossier"
    )
    record_type: RecordType = Field(
        description="The record type produced from this file"
    )
    record_count: int = Field(description="Number of records extracted")
    records: list[NormalizedRecord] = Field(
        default_factory=list,
        description="The normalized records extracted from this file",
    )

    def to_jsonl(self) -> str:
        """Serialize records as JSONL, one record per line."""
        lines: list[str] = []
        for record in self.records:
            lines.append(record.model_dump_json())
        return "\n".join(lines)
