"""
PDF parser for GDPdU/GoBD dossier documents.

Uses PyMuPDF (fitz) to extract text per page. Each page becomes a
document_text NormalizedRecord with page number provenance. Attempts basic
table detection from text structure.
"""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path

import fitz  # PyMuPDF

from app.normalization.models import (
    EntityRef,
    NormalizedRecord,
    RecordType,
    SourceProvenance,
)

logger = logging.getLogger(__name__)

NAMESPACE_URL = uuid.NAMESPACE_URL

# Patterns for entity extraction from text
_EUR_AMOUNT_RE = re.compile(
    r"(?:EUR|€)\s*([+-]?\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?)"
    r"|([+-]?\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?)\s*(?:EUR|€)",
    re.IGNORECASE,
)
_USER_ID_RE = re.compile(r"\b(MV-U\d{2,3})\b")
_GERMAN_DATE_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")

# Heuristic: a line with multiple whitespace-separated columns is a table row
_TABLE_ROW_RE = re.compile(r"^(.+?)\s{2,}(.+?)(?:\s{2,}(.+))?$")


def _make_record_id(dossier_id: str, file_id: str, row: int, index: int) -> str:
    """Generate stable UUID5 for a record."""
    dossier_ns = uuid.uuid5(NAMESPACE_URL, f"dossier:{dossier_id}")
    return str(uuid.uuid5(dossier_ns, f"{file_id}:{row}:{index}"))


def _extract_entities_from_text(text: str) -> list[EntityRef]:
    """Extract entity references from PDF text."""
    entities: list[EntityRef] = []
    seen: set[tuple[str, str]] = set()

    # Extract user IDs (e.g., MV-U05)
    for match in _USER_ID_RE.finditer(text):
        user_id = match.group(1)
        key = ("user", user_id)
        if key not in seen:
            seen.add(key)
            entities.append(
                EntityRef(entity_type="user", entity_id=user_id, label=user_id)
            )

    return entities


def _extract_amount_from_text(text: str) -> float | None:
    """Extract the first EUR amount from text."""
    match = _EUR_AMOUNT_RE.search(text)
    if match:
        raw = match.group(1) or match.group(2)
        if raw:
            cleaned = raw.strip()
            if "," in cleaned and "." in cleaned:
                cleaned = cleaned.replace(".", "").replace(",", ".")
            elif "," in cleaned:
                cleaned = cleaned.replace(",", ".")
            try:
                return float(cleaned)
            except ValueError:
                pass
    return None


def _extract_date_from_text(text: str) -> str | None:
    """Extract the first German date from text and convert to ISO."""
    match = _GERMAN_DATE_RE.search(text)
    if match:
        day, month, year = match.groups()
        return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
    return None


def _detect_table_rows(page_text: str) -> list[str]:
    """
    Attempt to detect table-like rows in page text.

    A table row is identified by having multiple values separated by
    two or more whitespace characters on a single line.

    Returns lines that appear to be table rows.
    """
    table_rows: list[str] = []
    for line in page_text.split("\n"):
        stripped = line.strip()
        if stripped and _TABLE_ROW_RE.match(stripped):
            table_rows.append(stripped)
    return table_rows


def parse_pdf_file(
    file_path: Path,
    relative_path: str,
    dossier_id: str,
    file_id: str,
) -> list[NormalizedRecord]:
    """
    Parse a PDF file into NormalizedRecords.

    Extracts text per page using PyMuPDF. Each page becomes one document_text
    record. If table-like rows are detected, they are additionally split into
    separate sub-records with row provenance.

    Args:
        file_path: Absolute path to the PDF file.
        relative_path: Path relative to dossier root.
        dossier_id: ID of the parent dossier.
        file_id: Stable ID of this file in the manifest.

    Returns:
        List of NormalizedRecords. Empty list on failure.
    """
    records: list[NormalizedRecord] = []

    try:
        doc = fitz.open(str(file_path))
    except Exception as exc:
        logger.warning(
            "Failed to open PDF file %s: %s", relative_path, exc
        )
        return []

    try:
        for page_num in range(len(doc)):
            try:
                page = doc[page_num]
                page_text = page.get_text("text")

                if not page_text or not page_text.strip():
                    logger.debug(
                        "Page %d of %s is empty, skipping.",
                        page_num + 1,
                        relative_path,
                    )
                    continue

                page_text_clean = page_text.strip()

                # Extract entities and metadata from full page text
                entities = _extract_entities_from_text(page_text_clean)
                amount = _extract_amount_from_text(page_text_clean)
                date = _extract_date_from_text(page_text_clean)

                relationships: dict[str, str] = {}
                for entity in entities:
                    relationships[entity.entity_type] = entity.entity_id

                # Create the page-level record
                record_id = _make_record_id(
                    dossier_id, file_id, page_num + 1, 0
                )

                record = NormalizedRecord(
                    record_id=record_id,
                    dossier_id=dossier_id,
                    record_type=RecordType.document_text,
                    source=SourceProvenance(
                        file_id=file_id,
                        relative_path=relative_path,
                        page=page_num + 1,
                    ),
                    entities=entities,
                    relationships=relationships,
                    data={
                        "page_number": page_num + 1,
                        "char_count": len(page_text_clean),
                    },
                    text_content=page_text_clean,
                    date=date,
                    amount=amount,
                    currency="EUR" if amount is not None else None,
                )
                records.append(record)

                # Attempt table row extraction for additional granularity
                table_rows = _detect_table_rows(page_text_clean)
                if len(table_rows) >= 3:
                    # Looks like a table - create sub-records for each row
                    for row_idx, row_text in enumerate(table_rows, start=1):
                        try:
                            row_entities = _extract_entities_from_text(row_text)
                            row_amount = _extract_amount_from_text(row_text)
                            row_date = _extract_date_from_text(row_text)

                            row_relationships: dict[str, str] = {}
                            for entity in row_entities:
                                row_relationships[entity.entity_type] = (
                                    entity.entity_id
                                )

                            row_record_id = _make_record_id(
                                dossier_id,
                                file_id,
                                page_num + 1,
                                row_idx,
                            )

                            row_record = NormalizedRecord(
                                record_id=row_record_id,
                                dossier_id=dossier_id,
                                record_type=RecordType.document_text,
                                source=SourceProvenance(
                                    file_id=file_id,
                                    relative_path=relative_path,
                                    page=page_num + 1,
                                    row_number=row_idx,
                                ),
                                entities=row_entities,
                                relationships=row_relationships,
                                data={
                                    "page_number": page_num + 1,
                                    "table_row": row_idx,
                                },
                                text_content=row_text,
                                date=row_date,
                                amount=row_amount,
                                currency="EUR" if row_amount is not None else None,
                            )
                            records.append(row_record)

                        except Exception as exc:
                            logger.warning(
                                "Skipping table row %d on page %d of %s: %s",
                                row_idx,
                                page_num + 1,
                                relative_path,
                                exc,
                            )
                            continue

            except Exception as exc:
                logger.warning(
                    "Skipping page %d of %s: %s",
                    page_num + 1,
                    relative_path,
                    exc,
                )
                continue

    finally:
        doc.close()

    logger.info(
        "Parsed %d records from PDF %s", len(records), relative_path
    )
    return records
