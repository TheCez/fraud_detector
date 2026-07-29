"""
XLSX header-detection tests driven by the real sample dossier.

These deliberately use `sample_data/Uebungsdaten_Muster_Verpackungen.zip`
rather than synthetic fixtures - every workbook in it has a banner/title
row (or two) above the real header, which is exactly the shape the parser
must handle correctly.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app.normalization.parsers.xlsx_parser import parse_xlsx_file

SAMPLE_ZIP = (
    Path(__file__).resolve().parent.parent.parent
    / "sample_data"
    / "Uebungsdaten_Muster_Verpackungen.zip"
)

pytestmark = pytest.mark.skipif(
    not SAMPLE_ZIP.exists(), reason="sample ZIP not available"
)


def _extract_member(tmp_path: Path, filename: str) -> Path:
    """Pull one XLSX member out of the sealed sample ZIP onto disk."""
    with zipfile.ZipFile(SAMPLE_ZIP) as zf:
        matches = [n for n in zf.namelist() if n.endswith(filename)]
        assert matches, f"{filename} not found in sample ZIP"
        data = zf.read(matches[0])
    out_path = tmp_path / filename
    out_path.write_bytes(data)
    return out_path


def test_berechtigungsauswertung_detects_real_header_and_users(tmp_path: Path):
    path = _extract_member(tmp_path, "Berechtigungsauswertung_2025.xlsx")

    records = parse_xlsx_file(
        path, "Begleitdokumente/Berechtigungsauswertung_2025.xlsx", "dossier-1", "file-1"
    )

    assert records, "expected records to be extracted"

    columns = records[0].source.columns
    assert columns == [
        "Benutzer",
        "Abteilung",
        "Buchen",
        "Journal freigeben",
        "Zahlungslauf",
        "Stammdaten/Kreditor anlegen",
        "Perioden",
        "Systemadmin",
        "Management",
        "Bemerkung",
    ]

    # Real column names mean data is keyed meaningfully, not column_N.
    assert all("Benutzer" in r.data for r in records)
    assert not any(key.startswith("column_") for r in records for key in r.data)

    # The segregation-of-duties user (MV-U02: Buchen + Zahlungslauf +
    # Stammdaten/Kreditor anlegen) must be extractable as a user entity.
    user_ids = {
        e.entity_id
        for r in records
        for e in r.entities
        if e.entity_type == "user"
    }
    assert "MV-U02" in user_ids
    mv_u02 = next(r for r in records if r.data.get("Benutzer") == "MV-U02")
    assert mv_u02.data["Zahlungslauf"] == "X"
    assert mv_u02.data["Stammdaten/Kreditor anlegen"] == "X"


def test_berechtigungsauswertung_banner_rows_are_not_records(tmp_path: Path):
    path = _extract_member(tmp_path, "Berechtigungsauswertung_2025.xlsx")

    records = parse_xlsx_file(
        path, "Begleitdokumente/Berechtigungsauswertung_2025.xlsx", "dossier-1", "file-1"
    )

    banner_fragment = "Berechtigungsauswertung D365"
    for record in records:
        for value in record.data.values():
            assert not (isinstance(value, str) and banner_fragment in value)
        # The header row itself must never resurface as a data record.
        assert record.data.get("Benutzer") != "Benutzer"


def test_berechtigungsauswertung_row_number_provenance(tmp_path: Path):
    path = _extract_member(tmp_path, "Berechtigungsauswertung_2025.xlsx")

    records = parse_xlsx_file(
        path, "Begleitdokumente/Berechtigungsauswertung_2025.xlsx", "dossier-1", "file-1"
    )

    # Row 3 is the header (0-based sheet row); MV-U01 is the first data row,
    # so it must resolve to true sheet row 4.
    mv_u01 = next(r for r in records if r.data.get("Benutzer") == "MV-U01")
    assert mv_u01.source.row_number == 4
    assert mv_u01.source.sheet == "Berechtigungen"


def test_saldenliste_detects_real_header(tmp_path: Path):
    path = _extract_member(tmp_path, "Saldenliste_2025.xlsx")

    records = parse_xlsx_file(
        path, "Begleitdokumente/Saldenliste_2025.xlsx", "dossier-1", "file-1"
    )

    saldenliste_records = [r for r in records if r.source.sheet == "Saldenliste 2025"]
    assert saldenliste_records
    assert saldenliste_records[0].source.columns == [
        "Konto",
        "Bezeichnung",
        "Kontenart",
        "EB 01.01.2025",
        "Soll 2025",
        "Haben 2025",
        "Saldo 31.12.2025",
    ]
    # First data row is true sheet row 5 (header detected at row 4).
    assert saldenliste_records[0].source.row_number == 5

    account_ids = {
        e.entity_id
        for r in saldenliste_records
        for e in r.entities
        if e.entity_type == "account"
    }
    # The parser's numeric normalization drops the leading zero from
    # account codes (a pre-existing quirk outside this task's scope).
    assert "20000" in account_ids


def test_multi_sheet_workbook_detects_header_per_sheet(tmp_path: Path):
    path = _extract_member(tmp_path, "OP-Liste_Debitoren_2025.xlsx")

    records = parse_xlsx_file(
        path, "Begleitdokumente/OP-Liste_Debitoren_2025.xlsx", "dossier-1", "file-1"
    )

    sheets = {r.source.sheet for r in records}
    assert sheets == {"Saldenliste Personenkonten", "Offene Posten (Auszug)"}

    saldo_sheet = [
        r for r in records if r.source.sheet == "Saldenliste Personenkonten"
    ]
    assert saldo_sheet[0].source.columns == ["Konto", "Name", "Gruppe", "Saldo 31.12.2025"]
    assert saldo_sheet[0].source.row_number == 4

    op_sheet = [r for r in records if r.source.sheet == "Offene Posten (Auszug)"]
    assert op_sheet[0].source.columns == [
        "Konto",
        "Name",
        "Belegnummer",
        "Belegdatum",
        "Betrag EUR",
    ]
    assert op_sheet[0].source.row_number == 4


def test_saldenliste_vorjahr_detects_header_with_no_data_rows(tmp_path: Path):
    # This workbook's sheet is only a banner, a blank row, and the header -
    # zero data rows follow. Header detection must not require data below
    # the header to succeed.
    path = _extract_member(tmp_path, "Saldenliste_2024_Vorjahr.xlsx")

    records = parse_xlsx_file(
        path,
        "Begleitdokumente/Saldenliste_2024_Vorjahr.xlsx",
        "dossier-1",
        "file-1",
    )

    assert records == []


def test_op_liste_kreditoren_detects_real_header(tmp_path: Path):
    path = _extract_member(tmp_path, "OP-Liste_Kreditoren_2025.xlsx")

    records = parse_xlsx_file(
        path, "Begleitdokumente/OP-Liste_Kreditoren_2025.xlsx", "dossier-1", "file-1"
    )

    assert records
    assert records[0].source.columns == ["Konto", "Name", "Gruppe", "Saldo 31.12.2025"]
    assert records[0].source.row_number == 4

    # The "Konto" column name matches the account pattern before the
    # vendor-specific fallback ever runs, so these surface as "account"
    # entities - a pre-existing entity-extraction behavior, not something
    # this task's header-detection fix changes.
    account_ids = {
        e.entity_id for r in records for e in r.entities if e.entity_type == "account"
    }
    assert "200001" in account_ids or "200002" in account_ids


def test_no_header_workbook_handled_without_crashing_or_nonsense_entities(
    tmp_path: Path,
):
    path = _extract_member(tmp_path, "Abstimmung_Nebenbuecher_HB_2025.xlsx")

    records = parse_xlsx_file(
        path,
        "Begleitdokumente/Abstimmung_Nebenbuecher_HB_2025.xlsx",
        "dossier-1",
        "file-1",
    )

    assert records, "label/value rows should still be parsed as records"
    assert all(r.source.columns == ["column_0", "column_1"] for r in records)

    # Section labels like "DEBITOREN" must not be mistaken for account
    # entities just because they sit in the first column.
    entity_ids = {e.entity_id for r in records for e in r.entities}
    assert "DEBITOREN" not in entity_ids
    assert "KREDITOREN" not in entity_ids

    # Every row in this sheet is data - there is no header to skip.
    row_numbers = {r.source.row_number for r in records}
    assert 1 in row_numbers
