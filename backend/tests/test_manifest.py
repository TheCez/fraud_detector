import json
from pathlib import Path

import pytest

from app.ingestion.manifest import (
    Manifest,
    ManifestEntry,
    build_manifest,
    save_manifest,
    _classify_file,
    _compute_sha256,
    _detect_mime_type,
    _generate_file_id,
)
from app.models.schemas import FileClassification, ParseStatus


@pytest.fixture
def sample_dir(tmp_path: Path) -> Path:
    (tmp_path / "Sachkonten").mkdir()
    (tmp_path / "Sachkonten" / "buchungen.txt").write_text("konto;betrag\n1000;500.00\n")
    (tmp_path / "Kreditoren").mkdir()
    (tmp_path / "Kreditoren" / "kred.txt").write_text("id;name\n1;Lieferant A\n")
    (tmp_path / "Debitoren").mkdir()
    (tmp_path / "Debitoren" / "deb.csv").write_text("id;name\n1;Kunde A\n")
    (tmp_path / "AV").mkdir()
    (tmp_path / "AV" / "anlagen.txt").write_text("id;bez\n1;Maschine\n")
    (tmp_path / "Begleitdokumente").mkdir()
    (tmp_path / "Begleitdokumente" / "bericht.xlsx").write_bytes(b"\x00" * 100)
    (tmp_path / "Begleitdokumente" / "notizen.docx").write_bytes(b"\x00" * 50)
    (tmp_path / "Begleitdokumente" / "rechnung.pdf").write_bytes(b"\x00" * 75)
    (tmp_path / "Begleitdokumente" / "daten.csv").write_text("a;b\n1;2\n")
    (tmp_path / "index.xml").write_text("<index/>")
    (tmp_path / "gdpdu-01-08-2002.dtd").write_text("<!ELEMENT index EMPTY>")
    (tmp_path / "extra.xml").write_text("<data/>")
    (tmp_path / "empty.txt").write_bytes(b"")
    return tmp_path


DOSSIER_ID = "test-dossier-001"


def test_manifest_entry_count(sample_dir: Path):
    manifest = build_manifest(sample_dir, DOSSIER_ID)
    assert manifest.entry_count == 12
    assert len(manifest.entries) == 12


def test_manifest_dossier_id(sample_dir: Path):
    manifest = build_manifest(sample_dir, DOSSIER_ID)
    assert manifest.dossier_id == DOSSIER_ID


def test_classification_evidence_accounting_txt(sample_dir: Path):
    manifest = build_manifest(sample_dir, DOSSIER_ID)
    by_path = {e.relative_path: e for e in manifest.entries}

    assert by_path["Sachkonten/buchungen.txt"].classification == FileClassification.evidence
    assert by_path["Kreditoren/kred.txt"].classification == FileClassification.evidence
    assert by_path["Debitoren/deb.csv"].classification == FileClassification.evidence
    assert by_path["AV/anlagen.txt"].classification == FileClassification.evidence


def test_classification_supporting_documents(sample_dir: Path):
    manifest = build_manifest(sample_dir, DOSSIER_ID)
    by_path = {e.relative_path: e for e in manifest.entries}

    assert by_path["Begleitdokumente/bericht.xlsx"].classification == FileClassification.supporting
    assert by_path["Begleitdokumente/notizen.docx"].classification == FileClassification.supporting
    assert by_path["Begleitdokumente/rechnung.pdf"].classification == FileClassification.supporting
    assert by_path["Begleitdokumente/daten.csv"].classification == FileClassification.supporting


def test_classification_technical_metadata(sample_dir: Path):
    manifest = build_manifest(sample_dir, DOSSIER_ID)
    by_path = {e.relative_path: e for e in manifest.entries}

    assert by_path["index.xml"].classification == FileClassification.technical_metadata
    assert by_path["gdpdu-01-08-2002.dtd"].classification == FileClassification.technical_metadata


def test_classification_other_xml_is_supporting(sample_dir: Path):
    manifest = build_manifest(sample_dir, DOSSIER_ID)
    by_path = {e.relative_path: e for e in manifest.entries}

    assert by_path["extra.xml"].classification == FileClassification.supporting


def test_sha256_stability(sample_dir: Path):
    manifest1 = build_manifest(sample_dir, DOSSIER_ID)
    manifest2 = build_manifest(sample_dir, DOSSIER_ID)

    for e1, e2 in zip(manifest1.entries, manifest2.entries):
        assert e1.sha256 == e2.sha256


def test_sha256_correctness(tmp_path: Path):
    test_file = tmp_path / "test.txt"
    test_file.write_text("hello world")
    import hashlib
    expected = hashlib.sha256(b"hello world").hexdigest()
    assert _compute_sha256(test_file) == expected


def test_file_ids_deterministic(sample_dir: Path):
    manifest1 = build_manifest(sample_dir, DOSSIER_ID)
    manifest2 = build_manifest(sample_dir, DOSSIER_ID)

    for e1, e2 in zip(manifest1.entries, manifest2.entries):
        assert e1.file_id == e2.file_id


def test_file_ids_differ_across_dossiers(sample_dir: Path):
    manifest1 = build_manifest(sample_dir, "dossier-A")
    manifest2 = build_manifest(sample_dir, "dossier-B")

    for e1, e2 in zip(manifest1.entries, manifest2.entries):
        assert e1.file_id != e2.file_id


def test_empty_file_excluded(sample_dir: Path):
    manifest = build_manifest(sample_dir, DOSSIER_ID)
    by_path = {e.relative_path: e for e in manifest.entries}

    empty_entry = by_path["empty.txt"]
    assert empty_entry.excluded_from_analysis is True
    assert empty_entry.exclusion_reason == "empty file"
    assert empty_entry.size_bytes == 0


def test_non_empty_files_not_excluded(sample_dir: Path):
    manifest = build_manifest(sample_dir, DOSSIER_ID)
    for entry in manifest.entries:
        if entry.size_bytes > 0:
            assert entry.excluded_from_analysis is False
            assert entry.exclusion_reason is None


def test_mime_type_detection():
    assert _detect_mime_type(".txt") == "text/plain"
    assert _detect_mime_type(".csv") == "text/csv"
    assert _detect_mime_type(".xlsx") == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert _detect_mime_type(".docx") == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert _detect_mime_type(".pdf") == "application/pdf"
    assert _detect_mime_type(".xml") == "application/xml"
    assert _detect_mime_type(".dtd") == "application/xml-dtd"
    assert _detect_mime_type(".unknown") == "application/octet-stream"


def test_parse_status_always_pending(sample_dir: Path):
    manifest = build_manifest(sample_dir, DOSSIER_ID)
    for entry in manifest.entries:
        assert entry.parse_status == ParseStatus.pending


def test_total_size_bytes(sample_dir: Path):
    manifest = build_manifest(sample_dir, DOSSIER_ID)
    expected_total = sum(e.size_bytes for e in manifest.entries)
    assert manifest.total_size_bytes == expected_total


def test_save_manifest(sample_dir: Path, tmp_path: Path):
    manifest = build_manifest(sample_dir, DOSSIER_ID)
    output = tmp_path / "output" / "manifest.json"
    save_manifest(manifest, output)

    assert output.exists()
    loaded = json.loads(output.read_text(encoding="utf-8"))
    assert loaded["dossier_id"] == DOSSIER_ID
    assert loaded["entry_count"] == 12
    assert len(loaded["entries"]) == 12


def test_relative_paths_use_forward_slashes(sample_dir: Path):
    manifest = build_manifest(sample_dir, DOSSIER_ID)
    for entry in manifest.entries:
        assert "\\" not in entry.relative_path
