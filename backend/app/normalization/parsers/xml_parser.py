"""
XML parser for GDPdU/GoBD dossier files.

GDPdU index.xml files are classified as technical_metadata by
app.ingestion.manifest and never reach this parser - the orchestrator's
_route_file skips them before dispatch. They are still read directly by
app.normalization.parsers.gdpdu_txt.parse_gdpdu_folder for column definitions;
this module only ever sees other, content-bearing XML files.

Extracts structural metadata from those files as document_text records.
"""

from __future__ import annotations

import logging
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from app.normalization.models import (
    EntityRef,
    NormalizedRecord,
    RecordType,
    SourceProvenance,
)

logger = logging.getLogger(__name__)

NAMESPACE_URL = uuid.NAMESPACE_URL

# Max file size to parse (prevent archive bombs)
_MAX_XML_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB


def _make_record_id(dossier_id: str, file_id: str, row: int, index: int) -> str:
    """Generate stable UUID5 for a record."""
    dossier_ns = uuid.uuid5(NAMESPACE_URL, f"dossier:{dossier_id}")
    return str(uuid.uuid5(dossier_ns, f"{file_id}:{row}:{index}"))


def _strip_namespace(tag: str) -> str:
    """Remove XML namespace prefix from tag name."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _element_to_text(element: ET.Element, depth: int = 0) -> str:
    """Convert an XML element and its children to readable text."""
    tag = _strip_namespace(element.tag)
    text_parts: list[str] = []

    # Element's own text
    if element.text and element.text.strip():
        text_parts.append(f"{tag}: {element.text.strip()}")

    # Attributes
    if element.attrib:
        attrs = ", ".join(f"{k}={v}" for k, v in element.attrib.items())
        text_parts.append(f"{tag}[{attrs}]")

    # Child elements (only one level deep for record text)
    if depth == 0:
        for child in element:
            child_tag = _strip_namespace(child.tag)
            if child.text and child.text.strip():
                text_parts.append(f"{child_tag}: {child.text.strip()}")

    return "; ".join(text_parts) if text_parts else tag


def parse_xml_file(
    file_path: Path,
    relative_path: str,
    dossier_id: str,
    file_id: str,
) -> list[NormalizedRecord]:
    """
    Parse an XML file into NormalizedRecords, extracting top-level elements as
    document_text records.

    Args:
        file_path: Absolute path to the XML file.
        relative_path: Path relative to dossier root.
        dossier_id: ID of the parent dossier.
        file_id: Stable ID of this file in the manifest.

    Returns:
        List of NormalizedRecords. Empty list on failure.
    """
    # Safety check: file size
    try:
        file_size = file_path.stat().st_size
        if file_size > _MAX_XML_SIZE_BYTES:
            logger.warning(
                "XML file %s exceeds size limit (%d bytes), skipping.",
                relative_path,
                file_size,
            )
            return []
        if file_size == 0:
            logger.debug("XML file %s is empty, skipping.", relative_path)
            return []
    except OSError as exc:
        logger.warning("Cannot stat XML file %s: %s", relative_path, exc)
        return []

    # Try parsing with multiple encodings
    tree: ET.ElementTree | None = None
    encodings_to_try = ["utf-8", "iso-8859-1", "cp1252", "latin-1"]

    for encoding in encodings_to_try:
        try:
            # Read file content with specific encoding
            content = file_path.read_bytes()
            text = content.decode(encoding)
            # Remove BOM if present
            if text.startswith("﻿"):
                text = text[1:]
            root = ET.fromstring(text)
            tree = ET.ElementTree(root)
            break
        except (ET.ParseError, UnicodeDecodeError) as exc:
            logger.debug(
                "Failed to parse %s with encoding %s: %s",
                relative_path,
                encoding,
                exc,
            )
            continue
        except Exception as exc:
            logger.debug(
                "Unexpected error parsing %s with encoding %s: %s",
                relative_path,
                encoding,
                exc,
            )
            continue

    if tree is None:
        logger.warning(
            "Failed to parse XML file %s with any supported encoding.",
            relative_path,
        )
        return []

    root = tree.getroot()
    records: list[NormalizedRecord] = []

    # Extract records from top-level children
    # Each top-level element (or second-level if root is a container) becomes a record
    children = list(root)
    if not children:
        # Root element itself has content
        text_content = _element_to_text(root)
        if text_content:
            record_id = _make_record_id(dossier_id, file_id, 1, 0)
            record = NormalizedRecord(
                record_id=record_id,
                dossier_id=dossier_id,
                record_type=RecordType.document_text,
                source=SourceProvenance(
                    file_id=file_id,
                    relative_path=relative_path,
                    row_number=1,
                ),
                entities=[],
                relationships={},
                data={
                    "root_tag": _strip_namespace(root.tag),
                    "element_count": 0,
                },
                text_content=text_content,
            )
            records.append(record)
        return records

    # Determine if children are repeating elements (data records)
    # or structural containers
    child_tags = [_strip_namespace(child.tag) for child in children]
    is_repeating = len(set(child_tags)) <= len(child_tags) / 2

    if is_repeating:
        # Treat each child as a data record
        for idx, child in enumerate(children, start=1):
            try:
                text_content = _element_to_text(child)
                if not text_content:
                    continue

                record_id = _make_record_id(dossier_id, file_id, idx, 0)
                data: dict[str, str | int | float | None] = {
                    "element_tag": _strip_namespace(child.tag),
                    "element_index": idx,
                }
                # Add child element values to data
                for sub_elem in child:
                    sub_tag = _strip_namespace(sub_elem.tag)
                    if sub_elem.text and sub_elem.text.strip():
                        data[sub_tag] = sub_elem.text.strip()

                record = NormalizedRecord(
                    record_id=record_id,
                    dossier_id=dossier_id,
                    record_type=RecordType.document_text,
                    source=SourceProvenance(
                        file_id=file_id,
                        relative_path=relative_path,
                        row_number=idx,
                    ),
                    entities=[],
                    relationships={},
                    data=data,
                    text_content=text_content,
                )
                records.append(record)

            except Exception as exc:
                logger.warning(
                    "Skipping XML element %d in %s: %s",
                    idx,
                    relative_path,
                    exc,
                )
                continue
    else:
        # Structural XML - create one record per top-level section
        for idx, child in enumerate(children, start=1):
            try:
                # Build text from this section and its direct children
                section_parts: list[str] = []
                child_tag = _strip_namespace(child.tag)
                section_parts.append(f"[{child_tag}]")

                if child.text and child.text.strip():
                    section_parts.append(child.text.strip())

                for sub_elem in child:
                    sub_text = _element_to_text(sub_elem)
                    if sub_text:
                        section_parts.append(sub_text)

                text_content = " ".join(section_parts)
                if not text_content.strip():
                    continue

                record_id = _make_record_id(dossier_id, file_id, idx, 0)

                record = NormalizedRecord(
                    record_id=record_id,
                    dossier_id=dossier_id,
                    record_type=RecordType.document_text,
                    source=SourceProvenance(
                        file_id=file_id,
                        relative_path=relative_path,
                        row_number=idx,
                    ),
                    entities=[],
                    relationships={},
                    data={
                        "section_tag": child_tag,
                        "section_index": idx,
                    },
                    text_content=text_content,
                )
                records.append(record)

            except Exception as exc:
                logger.warning(
                    "Skipping XML section %d in %s: %s",
                    idx,
                    relative_path,
                    exc,
                )
                continue

    logger.info(
        "Parsed %d records from XML %s", len(records), relative_path
    )
    return records
