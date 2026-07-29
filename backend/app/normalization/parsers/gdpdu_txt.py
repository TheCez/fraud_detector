"""Parser for GDPdU semicolon-delimited TXT files.

GDPdU (Grundsaetze zum Datenzugriff und zur Pruefbarkeit digitaler Unterlagen)
exports consist of semicolon-delimited TXT files with metadata defined in an
accompanying index.xml.  Files use latin-1/cp1252 encoding, German number format
(comma as decimal separator), and German date format (DD.MM.YYYY).  There is no
header row - column names are defined in index.xml.
"""

from __future__ import annotations

import csv
import re
import uuid
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from app.normalization.models import (
    EntityRef,
    NormalizedRecord,
    RecordType,
    SourceProvenance,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ENCODING = "cp1252"
DELIMITER = ";"
QUOTECHAR = '"'

# Namespace for deterministic record ID generation
_NAMESPACE_URL = uuid.NAMESPACE_URL

# Mapping from filename stem (lowercase) to record type
_FILENAME_TO_RECORD_TYPE: dict[str, RecordType] = {
    "sachkontobuchungen": RecordType.journal_entry,
    "lieferantenbuchungen": RecordType.vendor_posting,
    "kundenbuchungen": RecordType.customer_posting,
    "sachkonten": RecordType.master_data,
    "lieferanten": RecordType.master_data,
    "kunden": RecordType.master_data,
    "anlagen": RecordType.asset_record,
    "anlagenbuchungen": RecordType.asset_posting,
}

# Fields that contain entity references, keyed by canonical upper-case column name
_ACCOUNT_FIELDS = {"SACHKONTONUMMER", "GEGENKONTO", "KONTO"}
_VENDOR_FIELDS = {"LIEFERANTENKONTONUMMER"}
_CUSTOMER_FIELDS = {"KUNDENKONTONUMMER"}
_USER_FIELDS = {"BENUTZERKENNUNG", "ERFASSER", "GEAENDERT_VON"}

# Date fields (values in DD.MM.YYYY format)
_DATE_FIELDS = {
    "BUCHUNGSDATUM",
    "BELEGDATUM",
    "ERFASSUNGSDATUM",
    "LETZTER_AUSGLEICH",
    "ANSCHAFFUNGSDATUM",
    "ABGANGSDATUM",
}

# Primary date field used for the top-level `date` attribute
_PRIMARY_DATE_FIELD = "BUCHUNGSDATUM"

# Numeric fields (German decimal format)
_NUMERIC_FIELDS = {
    "BUCHUNGSBETRAG",
    "BUCHUNGSWERT",
    "ANSCHAFFUNGSWERT",
    "BUCHWERT",
    "ABSCHREIBUNGSBETRAG",
}

# Primary amount field
_PRIMARY_AMOUNT_FIELD = "BUCHUNGSBETRAG"

# German decimal pattern: optional thousands separator (.), mandatory comma decimal
_GERMAN_NUMBER_RE = re.compile(r"^-?\d{1,3}(?:\.\d{3})*,\d+$")

# German date pattern: DD.MM.YYYY
_GERMAN_DATE_RE = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$")


# ---------------------------------------------------------------------------
# index.xml parsing
# ---------------------------------------------------------------------------


def parse_index_xml(index_path: Path) -> dict[str, list[str]]:
    """Parse a GDPdU index.xml and return a mapping of filename -> column names.

    The index.xml structure (simplified):
        <Index>
          <Table>
            <URL>Sachkontobuchungen.txt</URL>
            <VariableLength>
              <VariableColumn><Name>SACHKONTONUMMER</Name>...</VariableColumn>
              ...
            </VariableLength>
          </Table>
          ...
        </Index>

    Returns:
        dict mapping filename (e.g. "Sachkontobuchungen.txt") to ordered list of
        column names.
    """
    tree = ElementTree.parse(index_path)  # noqa: S314
    root = tree.getroot()

    # Handle potential namespace in the XML
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    tables: dict[str, list[str]] = {}

    for table_elem in root.iter(f"{ns}Table"):
        url_elem = table_elem.find(f"{ns}URL")
        if url_elem is None or not url_elem.text:
            continue
        filename = url_elem.text.strip()

        columns: list[str] = []
        # Look for VariableLength section with VariableColumn children
        var_length = table_elem.find(f"{ns}VariableLength")
        if var_length is not None:
            for col_elem in var_length.findall(f"{ns}VariableColumn"):
                name_elem = col_elem.find(f"{ns}Name")
                if name_elem is not None and name_elem.text:
                    columns.append(name_elem.text.strip())

        # Fallback: look for FixedLength columns
        if not columns:
            fixed_length = table_elem.find(f"{ns}FixedLength")
            if fixed_length is not None:
                for col_elem in fixed_length.findall(f"{ns}FixedColumn"):
                    name_elem = col_elem.find(f"{ns}Name")
                    if name_elem is not None and name_elem.text:
                        columns.append(name_elem.text.strip())

        if columns:
            tables[filename] = columns

    return tables


# ---------------------------------------------------------------------------
# Value conversion helpers
# ---------------------------------------------------------------------------


def convert_german_decimal(value: str) -> float | None:
    """Convert a German-format decimal string to a Python float.

    German format uses '.' as thousands separator and ',' as decimal separator.
    Examples: "2339597,00" -> 2339597.0, "1.234,56" -> 1234.56
    """
    value = value.strip()
    if not value:
        return None

    # Check if it looks like a German number
    if _GERMAN_NUMBER_RE.match(value):
        # Remove thousands separator, replace decimal comma with dot
        normalized = value.replace(".", "").replace(",", ".")
        try:
            return float(normalized)
        except ValueError:
            return None

    # Handle simple numbers with just a comma (no thousands sep)
    # e.g. "2339597,00"
    if "," in value and "." not in value:
        try:
            return float(value.replace(",", "."))
        except ValueError:
            return None

    # Try direct float parse as fallback (already in standard format)
    try:
        return float(value)
    except ValueError:
        return None


def convert_german_date(value: str) -> str | None:
    """Convert a German date DD.MM.YYYY to ISO format YYYY-MM-DD.

    Returns None if the value is empty or doesn't match the expected format.
    """
    value = value.strip()
    if not value:
        return None

    match = _GERMAN_DATE_RE.match(value)
    if match:
        day, month, year = match.groups()
        return f"{year}-{month}-{day}"
    return None


# ---------------------------------------------------------------------------
# Entity extraction
# ---------------------------------------------------------------------------


def _extract_entities(
    row_data: dict[str, Any],
    columns: list[str],
) -> list[EntityRef]:
    """Extract entity references from a parsed row based on column names."""
    entities: list[EntityRef] = []
    seen: set[tuple[str, str]] = set()

    for col in columns:
        col_upper = col.upper()
        value = row_data.get(col)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue

        entity_type: str | None = None
        if col_upper in _ACCOUNT_FIELDS:
            entity_type = "account"
        elif col_upper in _VENDOR_FIELDS:
            entity_type = "vendor"
        elif col_upper in _CUSTOMER_FIELDS:
            entity_type = "customer"
        elif col_upper in _USER_FIELDS:
            entity_type = "user"

        if entity_type is not None:
            str_value = str(value).strip()
            key = (entity_type, str_value)
            if key not in seen:
                seen.add(key)
                entities.append(
                    EntityRef(
                        entity_type=entity_type,
                        entity_id=str_value,
                        label=None,
                    )
                )

    return entities


# ---------------------------------------------------------------------------
# Relationship extraction
# ---------------------------------------------------------------------------


def _extract_relationships(
    row_data: dict[str, Any],
    columns: list[str],
    filename_stem: str,
) -> dict[str, str]:
    """Extract relationships from a parsed row based on file context."""
    relationships: dict[str, str] = {}

    col_upper_set = {c.upper() for c in columns}

    # posted_by: user who created the entry
    for user_field in ("BENUTZERKENNUNG", "ERFASSER"):
        if user_field in col_upper_set:
            # Find the actual column name (preserving case from index.xml)
            actual_col = next(
                (c for c in columns if c.upper() == user_field), None
            )
            if actual_col and row_data.get(actual_col):
                val = str(row_data[actual_col]).strip()
                if val:
                    relationships["posted_by"] = val
                    break

    # to_account: counter-account for journal entries
    if filename_stem == "sachkontobuchungen" and "GEGENKONTO" in col_upper_set:
        actual_col = next(
            (c for c in columns if c.upper() == "GEGENKONTO"), None
        )
        if actual_col and row_data.get(actual_col):
            val = str(row_data[actual_col]).strip()
            if val:
                relationships["to_account"] = val

    # paid_to: vendor for vendor postings
    if filename_stem == "lieferantenbuchungen":
        for vendor_field in ("LIEFERANTENKONTONUMMER",):
            if vendor_field in col_upper_set:
                actual_col = next(
                    (c for c in columns if c.upper() == vendor_field), None
                )
                if actual_col and row_data.get(actual_col):
                    val = str(row_data[actual_col]).strip()
                    if val:
                        relationships["paid_to"] = val
                        break

    return relationships


# ---------------------------------------------------------------------------
# Record ID generation
# ---------------------------------------------------------------------------


def _generate_record_id(dossier_id: str, file_id: str, row_number: int) -> str:
    """Generate a deterministic record ID using uuid5.

    Uses: uuid5(uuid5(NAMESPACE_URL, "dossier:{dossier_id}"), "{file_id}:{row_number}")
    """
    dossier_ns = uuid.uuid5(_NAMESPACE_URL, f"dossier:{dossier_id}")
    return str(uuid.uuid5(dossier_ns, f"{file_id}:{row_number}"))


# ---------------------------------------------------------------------------
# Row parsing
# ---------------------------------------------------------------------------


def _parse_row_values(
    raw_values: list[str],
    columns: list[str],
) -> dict[str, Any]:
    """Parse raw string values into typed Python values based on column names.

    Applies German decimal and date conversions where appropriate.
    """
    data: dict[str, Any] = {}

    for i, col in enumerate(columns):
        if i >= len(raw_values):
            data[col] = None
            continue

        raw = raw_values[i].strip()
        if not raw:
            data[col] = None
            continue

        col_upper = col.upper()

        # Date conversion
        if col_upper in _DATE_FIELDS:
            converted = convert_german_date(raw)
            data[col] = converted if converted else raw
        # Numeric conversion
        elif col_upper in _NUMERIC_FIELDS:
            converted = convert_german_decimal(raw)
            data[col] = converted if converted is not None else raw
        else:
            # Try numeric conversion for fields that look like numbers
            data[col] = raw

    return data


# ---------------------------------------------------------------------------
# Single file parsing
# ---------------------------------------------------------------------------


def _parse_txt_file(
    file_path: Path,
    columns: list[str],
    dossier_id: str,
    file_id: str,
    relative_path: str,
    record_type: RecordType,
    filename_stem: str,
) -> list[NormalizedRecord]:
    """Parse a single GDPdU TXT file into NormalizedRecord objects."""
    records: list[NormalizedRecord] = []

    with open(file_path, encoding=ENCODING, newline="") as f:
        reader = csv.reader(
            f,
            delimiter=DELIMITER,
            quotechar=QUOTECHAR,
            doublequote=True,
        )

        for row_idx, raw_row in enumerate(reader, start=1):
            if not raw_row or all(cell.strip() == "" for cell in raw_row):
                continue

            row_data = _parse_row_values(raw_row, columns)

            # Extract primary fields
            primary_date: str | None = None
            for date_field in (_PRIMARY_DATE_FIELD, "BELEGDATUM"):
                actual_col = next(
                    (c for c in columns if c.upper() == date_field), None
                )
                if actual_col and row_data.get(actual_col):
                    primary_date = str(row_data[actual_col])
                    break

            amount: float | None = None
            actual_amount_col = next(
                (c for c in columns if c.upper() == _PRIMARY_AMOUNT_FIELD), None
            )
            if actual_amount_col and row_data.get(actual_amount_col) is not None:
                val = row_data[actual_amount_col]
                if isinstance(val, (int, float)):
                    amount = float(val)

            currency: str | None = None
            actual_currency_col = next(
                (c for c in columns if c.upper() == "BUCHUNGSWÄHRUNG"), None
            )
            # Fallback for encoding variants
            if actual_currency_col is None:
                actual_currency_col = next(
                    (c for c in columns if c.upper() in ("BUCHUNGSWAEHRUNG", "BUCHUNGSWÄHRUNG")),
                    None,
                )
            if actual_currency_col and row_data.get(actual_currency_col):
                currency = str(row_data[actual_currency_col])

            text_content: str | None = None
            actual_text_col = next(
                (c for c in columns if c.upper() == "BUCHUNGSTEXT"), None
            )
            if actual_text_col and row_data.get(actual_text_col):
                text_content = str(row_data[actual_text_col])

            period: str | None = None
            actual_period_col = next(
                (c for c in columns if c.upper() == "PERIODENCODE"), None
            )
            if actual_period_col and row_data.get(actual_period_col):
                period = str(row_data[actual_period_col])

            # Build entities and relationships
            entities = _extract_entities(row_data, columns)
            relationships = _extract_relationships(row_data, columns, filename_stem)

            # Build the data dict with string/numeric/None values
            data: dict[str, str | int | float | None] = {}
            for col in columns:
                val = row_data.get(col)
                if val is None:
                    data[col] = None
                elif isinstance(val, float):
                    data[col] = val
                elif isinstance(val, int):
                    data[col] = val
                else:
                    data[col] = str(val)

            record = NormalizedRecord(
                record_id=_generate_record_id(dossier_id, file_id, row_idx),
                dossier_id=dossier_id,
                record_type=record_type,
                source=SourceProvenance(
                    file_id=file_id,
                    relative_path=relative_path,
                    row_number=row_idx,
                    columns=columns,
                ),
                entities=entities,
                relationships=relationships,
                data=data,
                date=primary_date,
                period=period,
                amount=amount,
                currency=currency,
                text_content=text_content,
            )
            records.append(record)

    return records


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_gdpdu_folder(
    folder_path: Path,
    dossier_id: str,
    file_id_map: dict[str, str],
) -> list[NormalizedRecord]:
    """Parse all GDPdU TXT files in a folder using index.xml metadata.

    Args:
        folder_path: Path to the GDPdU export folder containing index.xml and
            one or more TXT data files.
        dossier_id: Unique identifier for the dossier being processed.
        file_id_map: Mapping of relative paths (e.g. "Sachkonten/Sachkontobuchungen.txt")
            to file IDs from the manifest.

    Returns:
        List of NormalizedRecord objects parsed from all TXT files in the folder.

    Raises:
        FileNotFoundError: If index.xml is missing from the folder.
        ValueError: If index.xml cannot be parsed or contains no table definitions.
    """
    index_path = folder_path / "index.xml"
    if not index_path.exists():
        raise FileNotFoundError(
            f"index.xml not found in GDPdU folder: {folder_path}"
        )

    table_definitions = parse_index_xml(index_path)
    if not table_definitions:
        raise ValueError(
            f"No table definitions found in index.xml: {index_path}"
        )

    all_records: list[NormalizedRecord] = []

    for filename, columns in table_definitions.items():
        txt_path = folder_path / filename
        if not txt_path.exists():
            # File referenced in index.xml but not present - skip gracefully
            continue

        # Determine record type from filename
        stem = Path(filename).stem.lower()
        record_type = _FILENAME_TO_RECORD_TYPE.get(stem, RecordType.master_data)

        # Find the file_id from the map
        # Try various relative path patterns to match
        file_id: str | None = None
        for rel_path, fid in file_id_map.items():
            # Match if the relative path ends with our filename or folder/filename
            normalized_rel = rel_path.replace("\\", "/")
            normalized_filename = filename.replace("\\", "/")
            if (
                normalized_rel.endswith(normalized_filename)
                or normalized_rel == normalized_filename
                or Path(normalized_rel).name == Path(normalized_filename).name
            ):
                file_id = fid
                break

        if file_id is None:
            # Generate a fallback file_id from the path
            file_id = str(
                uuid.uuid5(_NAMESPACE_URL, f"{dossier_id}:{filename}")
            )

        # Determine relative path for provenance
        relative_path = filename
        for rel_path in file_id_map:
            normalized_rel = rel_path.replace("\\", "/")
            normalized_filename = filename.replace("\\", "/")
            if (
                normalized_rel.endswith(normalized_filename)
                or normalized_rel == normalized_filename
            ):
                relative_path = rel_path
                break

        records = _parse_txt_file(
            file_path=txt_path,
            columns=columns,
            dossier_id=dossier_id,
            file_id=file_id,
            relative_path=relative_path,
            record_type=record_type,
            filename_stem=stem,
        )
        all_records.extend(records)

    return all_records
