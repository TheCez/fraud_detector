"""Tests for the GDPdU metadata/data separation and graph entity extraction.

These are driven by the real sample dossier (sample_data/Uebungsdaten_Muster_Verpackungen.zip)
wherever the scenario exists in that data. A few scenarios - a missing index.xml, an
unrecognized accounting folder - cannot occur in the real (valid) sample, so those use
small synthetic GDPdU folders instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ingestion.manifest import ACCOUNTING_FOLDERS, build_manifest
from app.models.schemas import FileClassification, ParseStatus
from app.normalization.orchestrator import normalize_dossier
from app.normalization.parsers.gdpdu_txt import parse_gdpdu_folder
from tests.conftest import SAMPLE_DOSSIER_ID, requires_sample_zip

DOSSIER_ID = SAMPLE_DOSSIER_ID


@pytest.fixture
def extracted_dir(sample_extracted_dir: Path) -> Path:
    return sample_extracted_dir


@pytest.fixture
def normalized(sample_manifest_and_records):
    """(manifest, records) from the session-shared real-sample pipeline - see
    conftest.py's ``sample_manifest_and_records``."""
    manifest, records, _db_path = sample_manifest_and_records
    return manifest, records


# ---------------------------------------------------------------------------
# Work item 1: data-vs-metadata classification and exclusion
# ---------------------------------------------------------------------------


@requires_sample_zip
def test_index_xml_and_dtd_are_technical_metadata_excluded_with_reason(
    extracted_dir: Path,
):
    manifest = build_manifest(extracted_dir, DOSSIER_ID)
    by_path = {e.relative_path: e for e in manifest.entries}

    metadata_paths = [
        p
        for p in by_path
        if p.endswith("index.xml") or p.endswith("gdpdu-01-08-2002.dtd")
    ]
    assert metadata_paths, "sample dossier should contain index.xml/.dtd files"

    for path in metadata_paths:
        entry = by_path[path]
        assert entry.classification == FileClassification.technical_metadata
        assert entry.excluded_from_analysis is True
        assert entry.exclusion_reason is not None


@requires_sample_zip
def test_data_tables_and_supporting_documents_not_excluded(extracted_dir: Path):
    manifest = build_manifest(extracted_dir, DOSSIER_ID)
    by_path = {e.relative_path: e for e in manifest.entries}

    evidence_path = next(
        p for p in by_path if p.endswith("Sachkonten/Sachkontobuchungen.txt")
    )
    assert by_path[evidence_path].excluded_from_analysis is False

    supporting_path = next(
        p for p in by_path if "Begleitdokumente" in p and p.endswith(".pdf")
    )
    assert by_path[supporting_path].excluded_from_analysis is False


@requires_sample_zip
def test_index_xml_excluded_from_normalized_output_but_still_readable(normalized):
    """'Excluded from analysis' means 'never emitted as normalized records', not
    'unreadable' - the .txt tables in the same folder must still be fully parsed
    using index.xml's column definitions."""
    manifest, records = normalized
    by_path = {e.relative_path: e for e in manifest.entries}

    index_entries = [e for e in manifest.entries if e.relative_path.endswith("index.xml")]
    assert index_entries
    for entry in index_entries:
        assert entry.parse_status == ParseStatus.skipped
        assert entry.normalized_record_count == 0

    sachkontobuchungen = next(
        e
        for e in manifest.entries
        if e.relative_path.endswith("Sachkonten/Sachkontobuchungen.txt")
    )
    assert sachkontobuchungen.parse_status == ParseStatus.parsed
    assert sachkontobuchungen.normalized_record_count > 0


# ---------------------------------------------------------------------------
# Work item 2: harden GDPdU folder loading
# ---------------------------------------------------------------------------


def test_missing_index_xml_marks_folder_entries_failed(tmp_path: Path):
    """A GDPdU folder whose index.xml is missing must surface as ParseStatus.error,
    never as a silently-empty 'parsed' result."""
    extracted_dir = tmp_path / "dossier"
    folder = extracted_dir / "Sachkonten"
    folder.mkdir(parents=True)
    (folder / "Sachkontobuchungen.txt").write_text("1000;500,00\n", encoding="cp1252")
    # Deliberately no index.xml

    manifest = build_manifest(extracted_dir, "broken-dossier")
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    manifest = normalize_dossier(extracted_dir, workspace_root, manifest, "broken-dossier")

    txt_entry = next(
        e for e in manifest.entries if e.relative_path.endswith("Sachkontobuchungen.txt")
    )
    assert txt_entry.parse_status == ParseStatus.error
    assert txt_entry.normalized_record_count == 0
    assert txt_entry.parse_error is not None


def test_index_xml_lookup_is_case_insensitive(tmp_path: Path):
    folder = tmp_path / "Sachkonten"
    folder.mkdir()
    (folder / "INDEX.XML").write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<DataSet>
  <Media>
    <Table>
      <URL>Sachkontobuchungen.txt</URL>
      <VariableLength>
        <VariableColumn><Name>SACHKONTONUMMER</Name></VariableColumn>
        <VariableColumn><Name>BUCHUNGSBETRAG</Name></VariableColumn>
      </VariableLength>
    </Table>
  </Media>
</DataSet>
""",
        encoding="utf-8",
    )
    (folder / "Sachkontobuchungen.txt").write_text(
        '"1000";"500,00"\n', encoding="cp1252"
    )

    records = parse_gdpdu_folder(
        folder, "case-dossier", {"Sachkonten/Sachkontobuchungen.txt": "file-1"}
    )
    assert len(records) == 1


def test_steuercodes_is_a_recognized_accounting_folder():
    """A real export with Steuercodes/Steuercodes.txt must not have its .txt silently
    dropped by folder routing - Steuercodes must be a shared accounting folder."""
    assert "steuercodes" in ACCOUNTING_FOLDERS


# ---------------------------------------------------------------------------
# Work item 3: missing graph entities
# ---------------------------------------------------------------------------


@requires_sample_zip
def test_asset_records_and_postings_carry_asset_entities(normalized):
    _manifest, records = normalized

    asset_records = [r for r in records if r.record_type.value == "asset_record"]
    asset_postings = [r for r in records if r.record_type.value == "asset_posting"]
    assert asset_records, "AV/Anlagen.txt should produce asset_record records"
    assert asset_postings, "AV/Anlagenbuchungen.txt should produce asset_posting records"

    for record in asset_records:
        assert any(e.entity_type == "asset" for e in record.entities), record.entities

    for record in asset_postings:
        assert any(e.entity_type == "asset" for e in record.entities), record.entities


@requires_sample_zip
def test_asset_postings_have_a_date(normalized):
    _manifest, records = normalized
    asset_postings = [r for r in records if r.record_type.value == "asset_posting"]
    assert asset_postings
    for record in asset_postings:
        assert record.date is not None


@requires_sample_zip
def test_composite_account_key_yields_account_and_vendor_nodes(normalized):
    _manifest, records = normalized
    vendor_leg = next(
        r for r in records if r.data.get("SACHKONTONUMMER") == "330000-200007"
    )
    entity_types = {(e.entity_type, e.entity_id) for e in vendor_leg.entities}
    assert ("account", "330000") in entity_types
    assert ("vendor", "200007") in entity_types
    # Provenance: the raw composite value must still be present, untouched.
    assert vendor_leg.data["SACHKONTONUMMER"] == "330000-200007"


@requires_sample_zip
def test_composite_account_key_yields_account_and_customer_nodes(normalized):
    _manifest, records = normalized
    customer_leg = next(
        r for r in records if r.data.get("SACHKONTONUMMER") == "230000-100151"
    )
    entity_types = {(e.entity_type, e.entity_id) for e in customer_leg.entities}
    assert ("account", "230000") in entity_types
    assert ("customer", "100151") in entity_types
    assert customer_leg.data["SACHKONTONUMMER"] == "230000-100151"


@requires_sample_zip
def test_composite_account_key_for_asset_control_account_does_not_fabricate_vendor(
    normalized,
):
    """040000-000191 is an asset control account posting (verified against
    AV/Anlagen.txt), not a vendor - decomposition must not assume every hyphenated
    value is a vendor/customer split."""
    _manifest, records = normalized
    asset_leg = next(
        r for r in records if r.data.get("SACHKONTONUMMER") == "040000-000191"
    )
    entity_types = {(e.entity_type, e.entity_id) for e in asset_leg.entities}
    assert ("account", "040000") in entity_types
    assert ("asset", "040000-000191") in entity_types
    assert not any(t == "vendor" for t, _ in entity_types)
    assert asset_leg.data["SACHKONTONUMMER"] == "040000-000191"


@requires_sample_zip
def test_vendor_postings_have_no_fabricated_posted_by(normalized):
    """Kreditoren/Lieferantenbuchungen.txt genuinely has no user column - posted_by
    must not be invented here."""
    _manifest, records = normalized
    vendor_postings = [r for r in records if r.record_type.value == "vendor_posting"]
    assert vendor_postings
    for record in vendor_postings:
        assert "posted_by" not in record.relationships
