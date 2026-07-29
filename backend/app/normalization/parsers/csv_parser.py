"""
CSV parser for German GDPdU/GoBD dossier files.

Handles semicolon-delimited CSV files with cp1252 encoding, German number
format (comma decimal separator), and DD.MM.YYYY dates. Produces
NormalizedRecords with full source provenance and entity extraction.
"""

from __future__ import annotations

import csv
import logging
import re
import uuid
from pathlib import Path

from app.normalization.models import (
    EntityRef,
    NormalizedRecord,
    RecordType,
    SourceProvenance,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Filename-to-RecordType mapping
# ---------------------------------------------------------------------------

_FILENAME_RECORD_TYPE_MAP: list[tuple[str, RecordType]] = [
    ("stammdatenaenderungen", RecordType.master_change),
    ("wareneingangsliste", RecordType.goods_receipt),
    ("warenausgangsliste", RecordType.goods_dispatch),
    ("fakturajournal", RecordType.invoice),
    ("freigabe-log", RecordType.master_data),
    ("gesellschafterliste", RecordType.master_data),
    ("kreditlimitliste", RecordType.master_data),
    ("buchungen_folgeperiode", RecordType.journal_entry),
    ("saldenliste", RecordType.balance),
    ("op-liste", RecordType.open_item),
    ("op_liste", RecordType.open_item),
    ("berechtigungsauswertung", RecordType.permission),
    ("sachkonto", RecordType.journal_entry),
    ("kreditoren", RecordType.vendor_posting),
    ("debitoren", RecordType.customer_posting),
    ("anlagenspiegel", RecordType.asset_record),
    ("anlagenbuchung", RecordType.asset_posting),
]

# ---------------------------------------------------------------------------
# Column pattern detection
# ---------------------------------------------------------------------------

_DATE_COLUMN_PATTERNS: list[str] = [
    "datum",
    "date",
    "genehmigt_am",
    "erstellt_am",
    "geaendert_am",
]

_AMOUNT_COLUMN_PATTERNS: list[str] = [
    "betrag",
    "wert",
    "summe",
    "saldo",
    "limit",
    "haben",
    "soll",
    "netto",
    "brutto",
    "steuer",
    "mwst",
    "eur",
]

# Entity extraction: maps column name patterns to (entity_type, relationship_label)
_ENTITY_COLUMN_MAP: dict[str, tuple[str, str | None]] = {
    "konto": ("account", None),
    "sachkonto": ("account", None),
    "gegenkonto": ("account", "counter_account"),
    "kreditor": ("vendor", None),
    "lieferant": ("vendor", None),
    "debitor": ("customer", None),
    "kunde": ("customer", None),
    "kostenstelle": ("cost_center", None),
    "geaendert_von": ("user", "changed_by"),
    "genehmigt_von": ("user", "approved_by"),
    "erstellt_von": ("user", "created_by"),
    "bearbeiter": ("user", "processed_by"),
    "benutzer": ("user", None),
    "user": ("user", None),
    "anlage": ("asset", None),
    "anlagen_nr": ("asset", None),
}

# Relationship extraction: column patterns that yield labeled relationships
# (without creating a separate entity node)
_RELATIONSHIP_COLUMN_MAP: dict[str, str] = {
    "kreditor": "received_from",
    "lieferant": "received_from",
    "debitor": "sold_to",
    "kunde": "sold_to",
    "geaendert_von": "changed_by",
    "genehmigt_von": "approved_by",
    "erstellt_von": "created_by",
    "bearbeiter": "processed_by",
}

# German date pattern DD.MM.YYYY
_DATE_PATTERN = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")

# German number pattern: optional sign, digits with optional dot thousands sep,
# comma decimal separator
_GERMAN_NUMBER_PATTERN = re.compile(
    r"^-?\d{1,3}(?:\.\d{3})*,\d+$"
)


# ---------------------------------------------------------------------------
# Encoding and delimiter detection
# ---------------------------------------------------------------------------


def _detect_encoding(file_path: Path) -> str:
    """Try encodings in priority order, return the first that decodes cleanly."""
    raw = file_path.read_bytes()[:8192]

    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            raw.decode(encoding)
            return encoding
        except (UnicodeDecodeError, ValueError):
            continue

    # Fallback - cp1252 is the GDPdU standard
    return "cp1252"


def _detect_delimiter(first_line: str) -> str:
    """Detect delimiter from the header line."""
    # Count occurrences of candidate delimiters
    candidates = [";", ",", "\t"]
    counts = [(first_line.count(d), d) for d in candidates]
    # Pick the one with the most occurrences (header should have many fields)
    counts.sort(reverse=True)
    if counts[0][0] > 0:
        return counts[0][1]
    return ";"  # Default for GDPdU


# ---------------------------------------------------------------------------
# Value parsing
# ---------------------------------------------------------------------------


def _parse_german_date(value: str) -> str | None:
    """Convert DD.MM.YYYY to ISO 8601 YYYY-MM-DD. Returns None on failure."""
    match = _DATE_PATTERN.match(value.strip())
    if not match:
        return None
    day, month, year = match.groups()
    try:
        # Validate ranges
        d, m, y = int(day), int(month), int(year)
        if not (1 <= m <= 12 and 1 <= d <= 31 and 1900 <= y <= 2100):
            return None
        return f"{y:04d}-{m:02d}-{d:02d}"
    except ValueError:
        return None


def _parse_german_number(value: str) -> float | None:
    """
    Parse German number format (dot thousands, comma decimal).
    Examples: '61802,00' -> 61802.0, '1.234,56' -> 1234.56, '-500,00' -> -500.0
    """
    cleaned = value.strip()
    if not cleaned:
        return None

    # Check if it matches the German number pattern
    if _GERMAN_NUMBER_PATTERN.match(cleaned):
        # Remove dot thousands separators, replace comma with dot
        normalized = cleaned.replace(".", "").replace(",", ".")
        try:
            return float(normalized)
        except ValueError:
            return None

    # Also handle simple comma-decimal without thousands separators
    # e.g. "500,00" or "-12,5"
    simple_pattern = re.match(r"^-?\d+,\d+$", cleaned)
    if simple_pattern:
        normalized = cleaned.replace(",", ".")
        try:
            return float(normalized)
        except ValueError:
            return None

    return None


def _is_date_column(col_name: str) -> bool:
    """Check if column name suggests it contains dates."""
    lower = col_name.lower()
    return any(pattern in lower for pattern in _DATE_COLUMN_PATTERNS)


def _is_amount_column(col_name: str) -> bool:
    """Check if column name suggests it contains monetary amounts."""
    lower = col_name.lower()
    return any(pattern in lower for pattern in _AMOUNT_COLUMN_PATTERNS)


# ---------------------------------------------------------------------------
# Record type detection
# ---------------------------------------------------------------------------


def _detect_record_type(filename: str) -> RecordType:
    """Map filename to a RecordType based on known German accounting patterns."""
    lower = filename.lower()
    for pattern, record_type in _FILENAME_RECORD_TYPE_MAP:
        if pattern in lower:
            return record_type
    # Default fallback for unrecognized CSV files
    return RecordType.master_data


# ---------------------------------------------------------------------------
# Entity and relationship extraction
# ---------------------------------------------------------------------------


def _is_label_column(col_name: str) -> bool:
    """Check if a column is a name/label/description (not an ID column)."""
    lower = col_name.lower()
    label_suffixes = ("name", "bezeichnung", "beschreibung", "text", "bemerkung")
    return any(lower.endswith(suffix) for suffix in label_suffixes)


def _extract_entities_and_relationships(
    columns: list[str],
    row_data: dict[str, str | int | float | None],
) -> tuple[list[EntityRef], dict[str, str]]:
    """
    Extract entity references and relationships from a row based on column names.
    Skips label/name columns to avoid treating descriptive text as entity IDs.
    """
    entities: list[EntityRef] = []
    relationships: dict[str, str] = {}

    for col in columns:
        col_lower = col.lower()

        # Skip columns that are clearly labels/names, not IDs
        if _is_label_column(col):
            continue

        value = row_data.get(col)
        if not value or (isinstance(value, str) and not value.strip()):
            continue

        str_value = str(value).strip()

        # Check entity mapping
        for pattern, (entity_type, rel_label) in _ENTITY_COLUMN_MAP.items():
            if pattern in col_lower:
                entities.append(
                    EntityRef(
                        entity_type=entity_type,
                        entity_id=str_value,
                        label=_find_label_for_entity(col, columns, row_data),
                    )
                )
                if rel_label:
                    relationships[rel_label] = str_value
                break

        # Check relationship mapping (some overlap with entities is intentional)
        for pattern, rel_label in _RELATIONSHIP_COLUMN_MAP.items():
            if pattern in col_lower and rel_label not in relationships:
                relationships[rel_label] = str_value
                break

    return entities, relationships


def _find_label_for_entity(
    id_column: str,
    columns: list[str],
    row_data: dict[str, str | int | float | None],
) -> str | None:
    """
    Try to find a human-readable name/label column for an entity ID column.
    Common patterns: KREDITOR -> KREDITORNAME, KONTO -> NAME
    """
    id_lower = id_column.lower()

    # Look for a corresponding name column
    name_candidates = [
        f"{id_column}NAME",
        f"{id_column}_NAME",
        f"{id_column}BEZEICHNUNG",
        "NAME",
        "BEZEICHNUNG",
    ]

    for candidate in name_candidates:
        # Case-insensitive match
        for col in columns:
            if col.upper() == candidate.upper():
                val = row_data.get(col)
                if val and isinstance(val, str) and val.strip():
                    return val.strip()

    # Also check if there's a column that is the id column + "name" suffix
    for col in columns:
        if col.lower().startswith(id_lower) and "name" in col.lower():
            val = row_data.get(col)
            if val and isinstance(val, str) and val.strip():
                return val.strip()

    return None


# ---------------------------------------------------------------------------
# Record ID generation
# ---------------------------------------------------------------------------


def _generate_record_id(dossier_id: str, file_id: str, row_number: int) -> str:
    """Generate a stable UUID5 record ID from dossier, file, and row."""
    namespace = uuid.uuid5(uuid.NAMESPACE_URL, f"dossier:{dossier_id}")
    return str(uuid.uuid5(namespace, f"{file_id}:{row_number}"))


# ---------------------------------------------------------------------------
# Primary date and amount extraction
# ---------------------------------------------------------------------------


def _extract_primary_date(
    columns: list[str],
    parsed_data: dict[str, str | int | float | None],
) -> str | None:
    """Find the primary date from parsed data, preferring columns named DATUM."""
    # Priority: columns with DATUM in name
    for col in columns:
        if _is_date_column(col):
            val = parsed_data.get(col)
            if isinstance(val, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", val):
                return val
    return None


def _extract_primary_amount(
    columns: list[str],
    parsed_data: dict[str, str | int | float | None],
) -> float | None:
    """Find the primary monetary amount from parsed data."""
    for col in columns:
        if _is_amount_column(col):
            val = parsed_data.get(col)
            if isinstance(val, (int, float)):
                return float(val)
    return None


def _extract_period_from_date(iso_date: str | None) -> str | None:
    """Derive accounting period (YYYY-MM) from an ISO date string."""
    if iso_date and re.match(r"^\d{4}-\d{2}-\d{2}$", iso_date):
        return iso_date[:7]
    return None


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------


def parse_csv_file(
    file_path: Path,
    relative_path: str,
    dossier_id: str,
    file_id: str,
) -> list[NormalizedRecord]:
    """
    Parse a CSV file from a GDPdU/GoBD dossier into NormalizedRecords.

    Handles:
    - Auto-detection of encoding (utf-8-sig, utf-8, cp1252)
    - Auto-detection of delimiter (;  ,  tab)
    - German date format (DD.MM.YYYY) to ISO conversion
    - German number format (comma decimal) to float conversion
    - Entity and relationship extraction from column names
    - Record type mapping from filename

    Args:
        file_path: Absolute path to the CSV file on disk.
        relative_path: Path relative to dossier root (for provenance).
        dossier_id: ID of the parent dossier.
        file_id: Stable file ID from the manifest.

    Returns:
        List of NormalizedRecords, one per data row. Empty list for
        empty files or files with only headers.
    """
    if not file_path.exists():
        logger.warning("CSV file not found: %s", file_path)
        return []

    if file_path.stat().st_size == 0:
        logger.info("Skipping empty CSV file: %s", relative_path)
        return []

    # Detect encoding
    encoding = _detect_encoding(file_path)
    logger.debug("Detected encoding %s for %s", encoding, relative_path)

    # Read file content
    try:
        content = file_path.read_text(encoding=encoding)
    except (UnicodeDecodeError, OSError) as e:
        logger.error("Failed to read CSV file %s: %s", relative_path, e)
        return []

    # Split into lines and remove empty trailing lines
    lines = content.splitlines()
    if not lines:
        logger.info("No lines in CSV file: %s", relative_path)
        return []

    # Detect delimiter from header
    delimiter = _detect_delimiter(lines[0])
    logger.debug("Detected delimiter %r for %s", delimiter, relative_path)

    # Determine record type from original filename (relative_path preserves it)
    original_filename = Path(relative_path).name
    record_type = _detect_record_type(original_filename)
    logger.debug("Record type %s for %s", record_type.value, relative_path)

    # Parse with csv.reader for proper quoting support
    reader = csv.reader(lines, delimiter=delimiter, quotechar='"')

    # Read header
    try:
        header = next(reader)
    except StopIteration:
        logger.info("Empty CSV (no header): %s", relative_path)
        return []

    # Strip BOM artifacts and whitespace from column names
    columns = [col.strip().strip("﻿") for col in header]

    if not columns or all(not c for c in columns):
        logger.warning("CSV has empty header row: %s", relative_path)
        return []

    # Identify column types
    date_columns = {col for col in columns if _is_date_column(col)}
    amount_columns = {col for col in columns if _is_amount_column(col)}

    records: list[NormalizedRecord] = []
    # Row numbering: header is row 1, first data row is row 2
    row_number = 1

    for raw_row in reader:
        row_number += 1

        # Skip completely empty rows
        if not raw_row or all(not cell.strip() for cell in raw_row):
            continue

        # Handle rows with fewer columns than header (pad with empty)
        if len(raw_row) < len(columns):
            raw_row.extend([""] * (len(columns) - len(raw_row)))

        # Handle rows with more columns than header (truncate with warning)
        if len(raw_row) > len(columns):
            logger.debug(
                "Row %d in %s has %d columns (expected %d), truncating",
                row_number,
                relative_path,
                len(raw_row),
                len(columns),
            )
            raw_row = raw_row[: len(columns)]

        # Parse cell values
        parsed_data: dict[str, str | int | float | None] = {}
        for col, raw_value in zip(columns, raw_row):
            value = raw_value.strip()

            if not value:
                parsed_data[col] = None
                continue

            # Try date parsing for date columns
            if col in date_columns:
                iso_date = _parse_german_date(value)
                if iso_date:
                    parsed_data[col] = iso_date
                    continue

            # Try number parsing for amount columns
            if col in amount_columns:
                number = _parse_german_number(value)
                if number is not None:
                    parsed_data[col] = number
                    continue

            # For non-typed columns, still try German number if it matches
            if _GERMAN_NUMBER_PATTERN.match(value) or re.match(
                r"^-?\d+,\d+$", value
            ):
                # Only convert if not in a clearly non-numeric column
                if col not in date_columns:
                    number = _parse_german_number(value)
                    if number is not None:
                        parsed_data[col] = number
                        continue

            # Default: keep as string
            parsed_data[col] = value

        # Extract entities and relationships
        entities, relationships = _extract_entities_and_relationships(
            columns, parsed_data
        )

        # Extract primary date, amount, period
        primary_date = _extract_primary_date(columns, parsed_data)
        primary_amount = _extract_primary_amount(columns, parsed_data)
        period = _extract_period_from_date(primary_date)

        # Detect currency - default EUR for amount columns with EUR in name
        currency: str | None = None
        if primary_amount is not None:
            currency = "EUR"

        record = NormalizedRecord(
            record_id=_generate_record_id(dossier_id, file_id, row_number),
            dossier_id=dossier_id,
            record_type=record_type,
            source=SourceProvenance(
                file_id=file_id,
                relative_path=relative_path,
                row_number=row_number,
                columns=columns,
            ),
            entities=entities,
            relationships=relationships,
            data=parsed_data,
            date=primary_date,
            period=period,
            amount=primary_amount,
            currency=currency,
        )
        records.append(record)

    logger.info(
        "Parsed %d records from CSV %s (type: %s)",
        len(records),
        relative_path,
        record_type.value,
    )
    return records
