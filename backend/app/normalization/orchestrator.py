"""
Normalization orchestrator - routes dossier files to appropriate parsers,
writes JSONL output for Cognee ingestion, and persists records to SQLite.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.ingestion.manifest import Manifest, ManifestEntry, save_manifest
from app.models.schemas import ParseStatus
from app.normalization.models import NormalizedOutput, NormalizedRecord
from app.persistence.database import bulk_insert_records, init_normalized_table

logger = logging.getLogger(__name__)

# GDPdU accounting folders that require folder-level parsing (index.xml driven)
_GDPDU_FOLDERS = {"sachkonten", "kreditoren", "debitoren", "av"}

# Parser module names mapped to extensions
_EXTENSION_PARSERS: dict[str, str] = {
    ".csv": "csv_parser",
    ".xlsx": "xlsx_parser",
    ".docx": "docx_parser",
    ".pdf": "pdf_parser",
    ".xml": "xml_parser",
}

# Extensions to skip entirely
_SKIP_EXTENSIONS = {".dtd"}


def normalize_dossier(
    extracted_dir: Path,
    workspace_root: Path,
    manifest: Manifest,
    dossier_id: str,
) -> Manifest:
    """
    Run all parsers, write JSONL output, update manifest.
    Returns updated manifest with parse_status and record counts.
    """
    output_dir = workspace_root / "normalized"
    output_dir.mkdir(parents=True, exist_ok=True)

    db_path = workspace_root.parent.parent / "registry.db"
    init_normalized_table(db_path)

    all_records: list[NormalizedRecord] = []

    # Group GDPdU folder entries so we can parse them together
    gdpdu_folders_seen: set[str] = set()

    for entry in manifest.entries:
        parser_name = _route_file(entry, extracted_dir)

        if parser_name is None:
            entry.parse_status = ParseStatus.skipped
            entry.parser = None
            continue

        # For GDPdU txt files, parse the entire folder once
        if parser_name == "gdpdu_txt":
            folder_key = _get_gdpdu_folder(entry.relative_path)
            if folder_key in gdpdu_folders_seen:
                # Already parsed as part of the folder batch
                continue
            gdpdu_folders_seen.add(folder_key)

            # Collect all entries in this folder
            folder_entries = [
                e
                for e in manifest.entries
                if _get_gdpdu_folder(e.relative_path) == folder_key
                and e.extension == ".txt"
            ]

            records = _parse_gdpdu_folder(
                folder_key, folder_entries, extracted_dir, dossier_id
            )

            # Distribute records back to individual file entries
            records_by_file: dict[str, list[NormalizedRecord]] = {}
            for rec in records:
                records_by_file.setdefault(rec.source.file_id, []).append(rec)

            for fe in folder_entries:
                file_records = records_by_file.get(fe.file_id, [])
                fe.parse_status = ParseStatus.parsed
                fe.parser = "gdpdu_txt"
                fe.normalized_record_count = len(file_records)

                if file_records:
                    _write_jsonl(file_records, output_dir / f"{fe.file_id}.jsonl")

            all_records.extend(records)
        else:
            # Single-file parser
            records = _parse_single_file(parser_name, entry, extracted_dir, dossier_id)
            entry.parser = parser_name
            entry.normalized_record_count = len(records)

            if records:
                entry.parse_status = ParseStatus.parsed
                _write_jsonl(records, output_dir / f"{entry.file_id}.jsonl")
                all_records.extend(records)
            else:
                entry.parse_status = ParseStatus.parsed

    # Write combined JSONL for Cognee bulk ingestion
    if all_records:
        _write_jsonl(all_records, output_dir / "all_records.jsonl")

    # Bulk insert into SQLite
    if all_records:
        record_dicts = [
            {
                "record_id": r.record_id,
                "dossier_id": r.dossier_id,
                "file_id": r.source.file_id,
                "record_type": r.record_type.value,
                "date": r.date,
                "amount": r.amount,
                "currency": r.currency,
                "data_json": r.model_dump_json(),
            }
            for r in all_records
        ]
        bulk_insert_records(db_path, record_dicts)

    # Update manifest entry_count to reflect any changes
    manifest.entry_count = len(manifest.entries)

    # Save updated manifest
    manifest_path = workspace_root / "manifest.json"
    save_manifest(manifest, manifest_path)

    logger.info(
        "Normalization complete: %d records from %d files",
        len(all_records),
        sum(1 for e in manifest.entries if e.parse_status == ParseStatus.parsed),
    )

    return manifest


def _write_jsonl(records: list[NormalizedRecord], output_path: Path) -> None:
    """Write records as one-JSON-per-line file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(record.model_dump_json())
            f.write("\n")


def _route_file(entry: ManifestEntry, extracted_dir: Path) -> str | None:
    """Return parser name for a manifest entry, or None to skip."""
    # Skip excluded files
    if entry.excluded_from_analysis:
        return None

    # Skip technical metadata like .dtd files
    if entry.extension in _SKIP_EXTENSIONS:
        return None

    # GDPdU txt files in accounting folders
    if entry.extension == ".txt":
        folder = _get_gdpdu_folder(entry.relative_path)
        if folder is not None:
            return "gdpdu_txt"
        # Non-accounting .txt files - skip for now (no generic txt parser)
        return None

    # Extension-based routing
    parser = _EXTENSION_PARSERS.get(entry.extension)
    return parser


def _get_gdpdu_folder(relative_path: str) -> str | None:
    """
    If the file is in a GDPdU accounting folder, return the folder name (lowercased).
    Returns None if not in an accounting folder.
    """
    parts = Path(relative_path).parts
    for part in parts[:-1]:  # Exclude the filename itself
        if part.lower() in _GDPDU_FOLDERS:
            return part.lower()
    return None


def _parse_gdpdu_folder(
    folder_key: str,
    entries: list[ManifestEntry],
    extracted_dir: Path,
    dossier_id: str,
) -> list[NormalizedRecord]:
    """
    Parse an entire GDPdU folder using the gdpdu_txt parser.
    Returns all records from the folder.
    """
    try:
        from app.normalization.parsers.gdpdu_txt import parse_gdpdu_folder

        # Find the actual folder path within extracted_dir
        # The folder_key is lowercased; find the real path by case-insensitive match
        folder_path: Path | None = None
        for child in extracted_dir.rglob("*"):
            if child.is_dir() and child.name.lower() == folder_key:
                folder_path = child
                break

        if folder_path is None:
            logger.warning("GDPdU folder not found in extracted dir: %s", folder_key)
            return []

        # Build file_id_map: relative_path -> file_id
        file_id_map: dict[str, str] = {}
        for entry in entries:
            file_id_map[entry.relative_path] = entry.file_id

        return parse_gdpdu_folder(folder_path, dossier_id, file_id_map)
    except ImportError:
        logger.warning("gdpdu_txt parser not yet implemented, skipping folder: %s", folder_key)
        return []
    except Exception:
        logger.exception("Error parsing GDPdU folder: %s", folder_key)
        return []


def _parse_single_file(
    parser_name: str,
    entry: ManifestEntry,
    extracted_dir: Path,
    dossier_id: str,
) -> list[NormalizedRecord]:
    """
    Parse a single file using the named parser.
    Returns list of records, empty list on error.
    """
    file_path = extracted_dir / entry.relative_path

    if not file_path.exists():
        logger.warning("File not found for parsing: %s", file_path)
        entry.parse_status = ParseStatus.error
        return []

    try:
        parse_fn = _get_parse_function(parser_name)
        if parse_fn is None:
            logger.warning("Parser not implemented: %s", parser_name)
            entry.parse_status = ParseStatus.skipped
            return []

        return parse_fn(file_path, entry.relative_path, dossier_id, entry.file_id)
    except Exception:
        logger.exception("Error parsing file %s with %s", entry.relative_path, parser_name)
        entry.parse_status = ParseStatus.error
        return []


# Parser function name per module
_PARSER_FUNC_NAMES: dict[str, str] = {
    "csv_parser": "parse_csv_file",
    "xlsx_parser": "parse_xlsx_file",
    "docx_parser": "parse_docx_file",
    "pdf_parser": "parse_pdf_file",
    "xml_parser": "parse_xml_file",
}


def _get_parse_function(parser_name: str):
    """
    Dynamically import and return the parse function for a parser.
    Each parser exposes parse_<type>_file(file_path, relative_path, dossier_id, file_id).
    Returns None if the parser module doesn't exist yet.
    """
    import importlib

    module_path = f"app.normalization.parsers.{parser_name}"
    func_name = _PARSER_FUNC_NAMES.get(parser_name)
    if func_name is None:
        return None

    try:
        mod = importlib.import_module(module_path)
        return getattr(mod, func_name)
    except ImportError:
        return None
    except AttributeError:
        logger.warning("Parser module %s missing %s function", module_path, func_name)
        return None
