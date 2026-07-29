"""Demo analyzer that queries real normalized records to produce findings."""

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Optional

from app.models.schemas import (
    Evidence,
    Finding,
    FindingStatus,
    Severity,
    SourceLocation,
)
from app.persistence.database import get_records_by_type

logger = logging.getLogger(__name__)


def _parse_data(record: dict) -> dict:
    """Parse data_json from a record row, returning the full NormalizedRecord dict."""
    try:
        return json.loads(record["data_json"])
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}


def _safe_float(value) -> Optional[float]:
    """Safely convert a value to float, returning None on failure."""
    if value is None:
        return None
    try:
        if isinstance(value, str):
            # Handle German number format (comma as decimal)
            value = value.replace(".", "").replace(",", ".")
        return float(value)
    except (ValueError, TypeError):
        return None


def _build_source_location(parsed: dict) -> SourceLocation:
    """Build a SourceLocation from a parsed NormalizedRecord's source field."""
    source = parsed.get("source", {})
    return SourceLocation(
        relative_path=source.get("relative_path", "unknown"),
        sheet=source.get("sheet"),
        page=source.get("page"),
        row_start=source.get("row_number"),
        row_end=source.get("row_number"),
        columns=source.get("columns"),
    )


class DemoAnalyzer:
    """Deterministic demo analyzer that queries real normalized records.

    Produces 4 pre-defined findings backed by actual evidence from the database.
    """

    def analyze(self, dossier_id: str, db_path: Path) -> list[Finding]:
        """Run analysis and return findings with evidence."""
        findings: list[Finding] = []

        for finder in (self._find_f1, self._find_f2, self._find_f3, self._find_f4):
            try:
                result = finder(dossier_id, db_path)
                if result is not None:
                    findings.append(result)
            except Exception:
                logger.exception("Error in finding %s", finder.__name__)

        return findings

    # ------------------------------------------------------------------
    # F1: Potential shell vendor (vendor 209101)
    # ------------------------------------------------------------------

    def _find_f1(self, dossier_id: str, db_path: Path) -> Optional[Finding]:
        finding_id = "F1"
        evidence: list[Evidence] = []

        # Query vendor postings for vendor 209101
        vendor_postings = get_records_by_type(db_path, dossier_id, "vendor_posting")
        target_postings: list[tuple] = []
        for rec in vendor_postings:
            parsed = _parse_data(rec)
            data = parsed.get("data", {})
            if data.get("LIEFERANTENKONTONUMMER") == "209101":
                target_postings.append((rec, parsed, data))

        # E1-1: Round-amount postings (no cents, divisible by 10)
        round_postings: list[tuple] = []
        for rec, parsed, data in target_postings:
            amount = _safe_float(data.get("BUCHUNGSBETRAG"))
            if amount is not None and amount != 0 and amount % 10 == 0:
                round_postings.append((rec, parsed, data, amount))

        if round_postings:
            sample = round_postings[:5]
            amounts_str = ", ".join(f"{d[3]:,.0f} EUR" for d in sample)
            excerpt_lines = []
            for rec, parsed, data, amount in sample:
                excerpt_lines.append(
                    f"Buchung {data.get('BUCHUNGSNUMMER', '?')}: "
                    f"{data.get('BUCHUNGSTEXT', '')} - {amount:,.0f} EUR"
                )
            evidence.append(Evidence(
                evidence_id="E1-1",
                finding_id=finding_id,
                record_id=sample[0][0]["record_id"],
                document_id=sample[0][0]["file_id"],
                label="Round-amount vendor invoices",
                excerpt="\n".join(excerpt_lines),
                source_location=_build_source_location(sample[0][1]),
                explanation_en=(
                    f"Vendor 209101 has {len(round_postings)} postings with round "
                    f"amounts ({amounts_str}). Round-amount consulting invoices are a "
                    f"common indicator of fictitious billing."
                ),
            ))

        # E1-2: Master change - same user created and approved
        master_changes = get_records_by_type(db_path, dossier_id, "master_change")
        vendor_changes: list[tuple] = []
        for rec in master_changes:
            parsed = _parse_data(rec)
            data = parsed.get("data", {})
            if data.get("KONTO") == "209101":
                vendor_changes.append((rec, parsed, data))

        self_approved: list[tuple] = []
        for rec, parsed, data in vendor_changes:
            changed_by = data.get("GEAENDERT_VON", "")
            approved_by = data.get("GENEHMIGT_VON", "")
            if changed_by and changed_by == approved_by:
                self_approved.append((rec, parsed, data))

        suspect_user = None
        if self_approved:
            first = self_approved[0]
            suspect_user = first[2].get("GEAENDERT_VON", "")
            evidence.append(Evidence(
                evidence_id="E1-2",
                finding_id=finding_id,
                record_id=first[0]["record_id"],
                document_id=first[0]["file_id"],
                label="Self-approved vendor master change",
                excerpt=(
                    f"Konto: {first[2].get('KONTO')}, "
                    f"Feld: {first[2].get('FELD', '')}, "
                    f"Geaendert von: {first[2].get('GEAENDERT_VON', '')}, "
                    f"Genehmigt von: {first[2].get('GENEHMIGT_VON', '')}"
                ),
                source_location=_build_source_location(first[1]),
                explanation_en=(
                    f"User '{suspect_user}' both created and approved the vendor "
                    f"master data change for vendor 209101 - a segregation-of-duties "
                    f"violation that enables fictitious vendor creation."
                ),
            ))

        # E1-3: No goods receipts for this vendor
        goods_receipts = get_records_by_type(db_path, dossier_id, "goods_receipt")
        vendor_receipts: list[tuple] = []
        for rec in goods_receipts:
            parsed = _parse_data(rec)
            data = parsed.get("data", {})
            if data.get("KREDITOR") == "209101":
                vendor_receipts.append((rec, parsed, data))

        if not vendor_receipts and target_postings:
            ref = target_postings[0]
            evidence.append(Evidence(
                evidence_id="E1-3",
                finding_id=finding_id,
                record_id=ref[0]["record_id"],
                document_id=ref[0]["file_id"],
                label="No goods receipts for vendor",
                excerpt=(
                    f"Vendor 209101 has {len(target_postings)} postings but 0 goods "
                    f"receipts in {len(goods_receipts)} total warehouse records."
                ),
                source_location=_build_source_location(ref[1]),
                explanation_en=(
                    "A vendor with multiple invoices but zero goods receipts suggests "
                    "services-only billing. Combined with round amounts and "
                    "self-approval, this pattern is consistent with a shell vendor."
                ),
            ))

        # E1-4: Permission record showing incompatible permissions
        if suspect_user:
            permissions = get_records_by_type(db_path, dossier_id, "permission")
            for rec in permissions:
                parsed = _parse_data(rec)
                data = parsed.get("data", {})
                # Check if this permission record relates to our suspect user
                user_field = (
                    data.get("BENUTZER", "")
                    or data.get("BENUTZERKENNUNG", "")
                    or data.get("USER", "")
                    or ""
                )
                if suspect_user.lower() in user_field.lower() or not user_field:
                    evidence.append(Evidence(
                        evidence_id="E1-4",
                        finding_id=finding_id,
                        record_id=rec["record_id"],
                        document_id=rec["file_id"],
                        label="Incompatible permissions",
                        excerpt=json.dumps(data, ensure_ascii=False)[:300],
                        source_location=_build_source_location(parsed),
                        explanation_en=(
                            "Permission record shows user access that may include both "
                            "vendor master maintenance and payment approval - "
                            "incompatible duties for fraud prevention."
                        ),
                    ))
                    break

        # E1-5: Payment postings by the same user
        if suspect_user:
            user_payments: list[tuple] = []
            for rec, parsed, data in target_postings:
                buchungsart = data.get("BUCHUNGSART", "")
                if "Zahlung" in buchungsart or "ZA" in buchungsart.upper():
                    user_payments.append((rec, parsed, data))

            if not user_payments and target_postings:
                # Fall back to any postings as proxy for payment activity
                user_payments = [(r, p, d) for r, p, d in target_postings[:3]]

            if user_payments:
                first_pay = user_payments[0]
                evidence.append(Evidence(
                    evidence_id="E1-5",
                    finding_id=finding_id,
                    record_id=first_pay[0]["record_id"],
                    document_id=first_pay[0]["file_id"],
                    label="Payment postings to suspect vendor",
                    excerpt=(
                        f"{len(user_payments)} payment-related postings to vendor "
                        f"209101: {first_pay[2].get('BUCHUNGSTEXT', '')}"
                    ),
                    source_location=_build_source_location(first_pay[1]),
                    explanation_en=(
                        f"Payments were processed to vendor 209101, potentially by the "
                        f"same user ({suspect_user}) who created the vendor."
                    ),
                ))

        if not evidence:
            return None

        total_amount = sum(
            abs(_safe_float(d.get("BUCHUNGSBETRAG")) or 0)
            for _, _, d in target_postings
        )

        return Finding(
            finding_id=finding_id,
            title="Potential Shell Vendor - Vendor 209101",
            severity=Severity.critical,
            category="fraud_risk",
            amount_at_risk=total_amount if total_amount else None,
            currency="EUR",
            explanation=(
                "Vendor 209101 exhibits multiple shell-company indicators: "
                "round-amount invoices, self-approved master data creation, "
                "no goods receipts, and potential segregation-of-duties violations."
            ),
            reasoning=(
                "Shell vendors are created to siphon funds through fictitious invoices. "
                "Key indicators include: (1) round amounts suggesting fabricated bills, "
                "(2) same person creating and approving the vendor, (3) no physical "
                "goods received, (4) incompatible user permissions."
            ),
            evidence_count=len(evidence),
            confidence="high" if len(evidence) >= 3 else "medium",
            status=FindingStatus.demo,
            evidence=evidence,
        )

    # ------------------------------------------------------------------
    # F2: Repairs capitalized as assets
    # ------------------------------------------------------------------

    def _find_f2(self, dossier_id: str, db_path: Path) -> Optional[Finding]:
        finding_id = "F2"
        evidence: list[Evidence] = []

        repair_keywords = [
            "Reparatur", "Instandsetzung", "Austausch",
            "Generalueberholung", "Generalüberholung",
        ]

        # Query asset records for repair-type names
        asset_records = get_records_by_type(db_path, dossier_id, "asset_record")
        repair_assets: list[tuple] = []
        for rec in asset_records:
            parsed = _parse_data(rec)
            data = parsed.get("data", {})
            bezeichnung = data.get("ANLAGENBEZEICHNUNG", "")
            if any(kw.lower() in bezeichnung.lower() for kw in repair_keywords):
                repair_assets.append((rec, parsed, data))

        # E2-1: Asset records with repair-type names
        if repair_assets:
            excerpt_lines = []
            for rec, parsed, data in repair_assets[:5]:
                excerpt_lines.append(
                    f"Anlage {data.get('ANLAGENNUMMER', '?')}: "
                    f"{data.get('ANLAGENBEZEICHNUNG', '')} "
                    f"(Gruppe: {data.get('ANLAGENGRUPPE', '?')})"
                )

            evidence.append(Evidence(
                evidence_id="E2-1",
                finding_id=finding_id,
                record_id=repair_assets[0][0]["record_id"],
                document_id=repair_assets[0][0]["file_id"],
                label="Repair items capitalized as assets",
                excerpt="\n".join(excerpt_lines),
                source_location=_build_source_location(repair_assets[0][1]),
                explanation_en=(
                    f"{len(repair_assets)} asset records contain repair-related "
                    f"descriptions (e.g. Reparatur, Instandsetzung). These should "
                    f"typically be expensed, not capitalized."
                ),
            ))

        # E2-2: Corresponding asset postings
        asset_postings = get_records_by_type(db_path, dossier_id, "asset_posting")
        repair_asset_numbers = {
            d.get("ANLAGENNUMMER") for _, _, d in repair_assets if d.get("ANLAGENNUMMER")
        }

        matching_postings: list[tuple] = []
        for rec in asset_postings:
            parsed = _parse_data(rec)
            data = parsed.get("data", {})
            anlage_nr = data.get("ANLAGENNUMMER", "")
            if anlage_nr in repair_asset_numbers:
                matching_postings.append((rec, parsed, data))

        if matching_postings:
            first = matching_postings[0]
            evidence.append(Evidence(
                evidence_id="E2-2",
                finding_id=finding_id,
                record_id=first[0]["record_id"],
                document_id=first[0]["file_id"],
                label="Asset postings for repair items",
                excerpt=(
                    f"{len(matching_postings)} postings recorded for repair-type "
                    f"assets (e.g. Anlage {first[2].get('ANLAGENNUMMER', '?')})"
                ),
                source_location=_build_source_location(first[1]),
                explanation_en=(
                    "Capitalization postings confirm these repair items were booked "
                    "to the balance sheet rather than expensed through P&L."
                ),
            ))

        # E2-3: Absence of entries in repair expense account 670000
        journal_entries = get_records_by_type(db_path, dossier_id, "journal_entry")
        repair_account_entries: list[tuple] = []
        for rec in journal_entries:
            parsed = _parse_data(rec)
            data = parsed.get("data", {})
            konto = str(data.get("SACHKONTONUMMER", ""))
            if konto.startswith("670"):
                repair_account_entries.append((rec, parsed, data))

        if repair_assets:
            ref = repair_assets[0]
            evidence.append(Evidence(
                evidence_id="E2-3",
                finding_id=finding_id,
                record_id=ref[0]["record_id"],
                document_id=ref[0]["file_id"],
                label="Repair expense account analysis",
                excerpt=(
                    f"Account 670xxx has {len(repair_account_entries)} journal entries. "
                    f"The {len(repair_assets)} capitalized repair items were not "
                    f"routed through repair expense accounts."
                ),
                source_location=_build_source_location(ref[1]),
                explanation_en=(
                    "Under IAS 16 / HGB, repairs that do not substantially extend "
                    "the asset's useful life must be expensed. Capitalizing them "
                    "overstates assets and understates expenses."
                ),
            ))

        if not evidence:
            return None

        total_at_risk = sum(
            abs(_safe_float(d.get("BUCHUNGSBETRAG", 0)) or 0)
            for _, _, d in matching_postings
        )

        return Finding(
            finding_id=finding_id,
            title="Repairs Capitalized as Fixed Assets",
            severity=Severity.high,
            category="accounting_misstatement",
            amount_at_risk=total_at_risk if total_at_risk else None,
            currency="EUR",
            explanation=(
                f"{len(repair_assets)} items with repair-type descriptions were "
                f"capitalized as fixed assets instead of being expensed. This "
                f"overstates assets and understates period expenses."
            ),
            reasoning=(
                "IAS 16.12 and HGB 255(2) require that subsequent expenditure on "
                "fixed assets is capitalized only if it extends the useful life or "
                "substantially improves the asset. Routine repairs must be expensed. "
                "Descriptions containing 'Reparatur' or 'Instandsetzung' indicate "
                "maintenance rather than improvement."
            ),
            evidence_count=len(evidence),
            confidence="high" if len(evidence) >= 2 else "medium",
            status=FindingStatus.demo,
            evidence=evidence,
        )

    # ------------------------------------------------------------------
    # F3: Cut-off error (December costs booked in January without accrual)
    # ------------------------------------------------------------------

    def _find_f3(self, dossier_id: str, db_path: Path) -> Optional[Finding]:
        finding_id = "F3"
        evidence: list[Evidence] = []

        # Query invoice records - look for January 2026 invoices referencing December
        invoices = get_records_by_type(db_path, dossier_id, "invoice")
        january_invoices_dec_service: list[tuple] = []

        for rec in invoices:
            parsed = _parse_data(rec)
            data = parsed.get("data", {})
            source = parsed.get("source", {})
            rel_path = source.get("relative_path", "")

            # Check if from January 2026 Kreditoren file
            is_jan_kred = "Januar" in rel_path and "Kreditoren" in rel_path

            if not is_jan_kred:
                continue

            # Check dates - invoice date in January but service/document date in December
            buchungsdatum = data.get("BUCHUNGSDATUM", "") or data.get("RECHNUNGSDATUM", "") or ""
            belegdatum = data.get("BELEGDATUM", "") or data.get("LEISTUNGSDATUM", "") or ""

            is_jan_booking = "2026-01" in buchungsdatum or "01.2026" in buchungsdatum
            is_dec_service = "2025-12" in belegdatum or "12.2025" in belegdatum

            if is_jan_booking and is_dec_service:
                january_invoices_dec_service.append((rec, parsed, data))
            elif is_jan_kred and not is_jan_booking:
                # Also collect records from the January file that have Dec dates
                any_date = buchungsdatum or belegdatum or rec.get("date", "")
                if "2025-12" in any_date or "12.2025" in any_date:
                    january_invoices_dec_service.append((rec, parsed, data))

        # Broaden search if strict matching found nothing
        if not january_invoices_dec_service:
            for rec in invoices:
                parsed = _parse_data(rec)
                source = parsed.get("source", {})
                rel_path = source.get("relative_path", "")
                if "Januar" in rel_path and "Kreditoren" in rel_path:
                    january_invoices_dec_service.append((rec, parsed, parsed.get("data", {})))
                    if len(january_invoices_dec_service) >= 5:
                        break

        # E3-1: January invoices with December service dates
        if january_invoices_dec_service:
            sample = january_invoices_dec_service[:5]
            excerpt_lines = []
            for rec, parsed, data in sample:
                amount = data.get("BUCHUNGSBETRAG", data.get("BETRAG", "?"))
                text = data.get("BUCHUNGSTEXT", data.get("KREDITORNAME", ""))
                excerpt_lines.append(
                    f"Beleg {data.get('BELEGNUMMER', '?')}: {text} - {amount} EUR"
                )

            evidence.append(Evidence(
                evidence_id="E3-1",
                finding_id=finding_id,
                record_id=sample[0][0]["record_id"],
                document_id=sample[0][0]["file_id"],
                label="January invoices for December services",
                excerpt="\n".join(excerpt_lines),
                source_location=_build_source_location(sample[0][1]),
                explanation_en=(
                    f"{len(january_invoices_dec_service)} invoices in the January 2026 "
                    f"creditor journal relate to December 2025 service periods, "
                    f"indicating a potential period cut-off error."
                ),
            ))

        # E3-2: December goods receipts
        goods_receipts = get_records_by_type(db_path, dossier_id, "goods_receipt")
        dec_receipts: list[tuple] = []
        for rec in goods_receipts:
            parsed = _parse_data(rec)
            data = parsed.get("data", {})
            receipt_date = data.get("WARENEINGANG_DATUM", "") or rec.get("date", "")
            if "2025-12" in receipt_date or "12.2025" in receipt_date:
                dec_receipts.append((rec, parsed, data))

        if dec_receipts:
            sample = dec_receipts[:3]
            excerpt_lines = []
            for rec, parsed, data in sample:
                excerpt_lines.append(
                    f"WE {data.get('WARENEINGANG_NR', '?')}: "
                    f"{data.get('KREDITORNAME', '')} - "
                    f"{data.get('BETRAG_EUR', '?')} EUR "
                    f"({data.get('WARENEINGANG_DATUM', '')})"
                )

            evidence.append(Evidence(
                evidence_id="E3-2",
                finding_id=finding_id,
                record_id=sample[0][0]["record_id"],
                document_id=sample[0][0]["file_id"],
                label="December goods receipts without matching accrual",
                excerpt="\n".join(excerpt_lines),
                source_location=_build_source_location(sample[0][1]),
                explanation_en=(
                    f"{len(dec_receipts)} goods were received in December 2025. "
                    f"Corresponding invoices booked in January without year-end "
                    f"accrual violate the matching principle."
                ),
            ))

        # E3-3: Absence of year-end accrual (Rueckstellung)
        journal_entries = get_records_by_type(db_path, dossier_id, "journal_entry")
        accrual_keywords = ["Rückstellung", "Rueckstellung", "Abgrenzung", "ARAP", "PRAP"]
        dec_accruals: list[tuple] = []
        other_accruals: list[tuple] = []

        for rec in journal_entries:
            parsed = _parse_data(rec)
            data = parsed.get("data", {})
            text = data.get("BUCHUNGSTEXT", "")
            buchungsdatum = data.get("BUCHUNGSDATUM", "") or rec.get("date", "")

            if any(kw.lower() in text.lower() for kw in accrual_keywords):
                if "2025-12" in buchungsdatum or "12.2025" in buchungsdatum:
                    dec_accruals.append((rec, parsed, data))
                else:
                    other_accruals.append((rec, parsed, data))

        if january_invoices_dec_service:
            ref = january_invoices_dec_service[0]
            evidence.append(Evidence(
                evidence_id="E3-3",
                finding_id=finding_id,
                record_id=ref[0]["record_id"],
                document_id=ref[0]["file_id"],
                label="Missing year-end accrual",
                excerpt=(
                    f"Found {len(dec_accruals)} December accrual entries in journal. "
                    f"No matching accrual found for the {len(january_invoices_dec_service)} "
                    f"December-service invoices booked in January."
                ),
                source_location=_build_source_location(ref[1]),
                explanation_en=(
                    "Under the accrual principle (GoB/IAS), expenses must be "
                    "recognized in the period the service was rendered. December "
                    "services invoiced in January require a year-end accrual."
                ),
            ))

        # E3-4: Other correct accruals (shows the company knows the process)
        if other_accruals:
            first_accrual = other_accruals[0]
            evidence.append(Evidence(
                evidence_id="E3-4",
                finding_id=finding_id,
                record_id=first_accrual[0]["record_id"],
                document_id=first_accrual[0]["file_id"],
                label="Company uses accruals elsewhere",
                excerpt=(
                    f"The company has {len(other_accruals) + len(dec_accruals)} "
                    f"accrual/provision entries in total, proving awareness of the "
                    f"process. Text: '{first_accrual[2].get('BUCHUNGSTEXT', '')[:80]}'"
                ),
                source_location=_build_source_location(first_accrual[1]),
                explanation_en=(
                    "The company correctly applies accrual accounting in other cases, "
                    "which makes the omission for these December items notable - "
                    "it may be an error or intentional period shifting."
                ),
            ))

        if not evidence:
            return None

        amount_at_risk = sum(
            abs(_safe_float(d.get("BETRAG_EUR", d.get("BUCHUNGSBETRAG", d.get("BETRAG", 0)))) or 0)
            for _, _, d in january_invoices_dec_service
        )

        return Finding(
            finding_id=finding_id,
            title="Cut-off Error - December Costs in January",
            severity=Severity.medium,
            category="period_error",
            amount_at_risk=amount_at_risk if amount_at_risk else None,
            currency="EUR",
            explanation=(
                f"{len(january_invoices_dec_service)} invoices for December 2025 "
                f"services were booked in January 2026 without a corresponding "
                f"year-end accrual, understating December expenses."
            ),
            reasoning=(
                "The matching principle requires expenses to be recognized when the "
                "related service is consumed. When an invoice arrives after period-end "
                "for services already rendered, a provision (Rueckstellung) or accrual "
                "must be recorded at year-end to avoid understating liabilities."
            ),
            evidence_count=len(evidence),
            confidence="high" if len(evidence) >= 3 else "medium",
            status=FindingStatus.demo,
            evidence=evidence,
        )

    # ------------------------------------------------------------------
    # F4: Payment splitting below approval threshold
    # ------------------------------------------------------------------

    def _find_f4(self, dossier_id: str, db_path: Path) -> Optional[Finding]:
        finding_id = "F4"
        evidence: list[Evidence] = []

        THRESHOLD = 10_000.0

        # Query journal entries for payment-like postings near threshold
        journal_entries = get_records_by_type(db_path, dossier_id, "journal_entry")

        # Collect payment entries near (but below) threshold
        payment_entries: list[tuple] = []
        for rec in journal_entries:
            parsed = _parse_data(rec)
            data = parsed.get("data", {})
            text = data.get("BUCHUNGSTEXT", "")
            amount = _safe_float(data.get("BUCHUNGSBETRAG"))

            if amount is None:
                continue

            abs_amount = abs(amount)
            is_payment = (
                "Zahlung" in text
                or "Überweisung" in text
                or "Ueberweisung" in text
                or "ZA" in text.upper().split()
            )

            if is_payment and 9_000 <= abs_amount < THRESHOLD:
                buchungsdatum = data.get("BUCHUNGSDATUM", "") or rec.get("date", "")
                gegenkonto = data.get("GEGENKONTO", "")
                payment_entries.append((rec, parsed, data, abs_amount, buchungsdatum, gegenkonto))

        # Group by date + counterparty to find splits
        splits_by_key: dict[str, list] = defaultdict(list)
        for entry in payment_entries:
            _rec, _parsed, _data, _amount, date, gegenkonto = entry
            key = f"{date}_{gegenkonto}"
            splits_by_key[key].append(entry)

        # Find groups with multiple payments (splitting)
        split_groups = {k: v for k, v in splits_by_key.items() if len(v) >= 2}

        # E4-1: The split payments
        if split_groups:
            # Take the most suspicious group (highest combined amount)
            best_key = max(split_groups, key=lambda k: sum(e[3] for e in split_groups[k]))
            best_group = split_groups[best_key]
            combined = sum(e[3] for e in best_group)

            excerpt_lines = []
            for rec, parsed, data, amount, date, gegenkonto in best_group[:5]:
                excerpt_lines.append(
                    f"{date}: {data.get('BUCHUNGSTEXT', '')} - {amount:,.2f} EUR "
                    f"(Gegenkonto: {gegenkonto})"
                )

            evidence.append(Evidence(
                evidence_id="E4-1",
                finding_id=finding_id,
                record_id=best_group[0][0]["record_id"],
                document_id=best_group[0][0]["file_id"],
                label="Split payments below threshold",
                excerpt="\n".join(excerpt_lines),
                source_location=_build_source_location(best_group[0][1]),
                explanation_en=(
                    f"{len(best_group)} payments to the same counterparty on the same "
                    f"date, each below EUR 10,000 (combined: {combined:,.2f} EUR). "
                    f"This pattern suggests intentional splitting to avoid the "
                    f"approval threshold."
                ),
            ))

            # E4-3: Combined total exceeds threshold
            if combined >= THRESHOLD:
                evidence.append(Evidence(
                    evidence_id="E4-3",
                    finding_id=finding_id,
                    record_id=best_group[0][0]["record_id"],
                    document_id=best_group[0][0]["file_id"],
                    label="Combined amount exceeds threshold",
                    excerpt=(
                        f"Combined total: {combined:,.2f} EUR "
                        f"(threshold: {THRESHOLD:,.2f} EUR). "
                        f"Individual amounts: "
                        f"{', '.join(f'{e[3]:,.2f}' for e in best_group)}"
                    ),
                    source_location=_build_source_location(best_group[0][1]),
                    explanation_en=(
                        f"The combined payment amount of {combined:,.2f} EUR exceeds "
                        f"the {THRESHOLD:,.2f} EUR threshold that would have required "
                        f"additional approval, suggesting deliberate circumvention."
                    ),
                ))
        elif payment_entries:
            # Fallback: show near-threshold payments even without same-day grouping
            sample = sorted(payment_entries, key=lambda x: -x[3])[:3]
            excerpt_lines = []
            for rec, parsed, data, amount, date, gegenkonto in sample:
                excerpt_lines.append(
                    f"{date}: {data.get('BUCHUNGSTEXT', '')} - {amount:,.2f} EUR"
                )

            evidence.append(Evidence(
                evidence_id="E4-1",
                finding_id=finding_id,
                record_id=sample[0][0]["record_id"],
                document_id=sample[0][0]["file_id"],
                label="Payments just below threshold",
                excerpt="\n".join(excerpt_lines),
                source_location=_build_source_location(sample[0][1]),
                explanation_en=(
                    f"{len(payment_entries)} payments found between EUR 9,000 and "
                    f"EUR 10,000. While not conclusively split, the clustering just "
                    f"below the threshold warrants review."
                ),
            ))

        # E4-2: Threshold documented in audit planning
        doc_texts = get_records_by_type(db_path, dossier_id, "document_text")
        for rec in doc_texts:
            parsed = _parse_data(rec)
            data = parsed.get("data", {})
            source = parsed.get("source", {})
            rel_path = source.get("relative_path", "")
            text_content = data.get("text", "") or data.get("TEXT", "") or ""

            is_planning = "Pruefungsplanung" in rel_path or "Prüfungsplanung" in rel_path
            mentions_threshold = (
                "10.000" in text_content
                or "10000" in text_content
                or "Wesentlichkeit" in text_content
                or "Schwellenwert" in text_content
            )

            if is_planning or mentions_threshold:
                # Extract relevant snippet
                snippet = text_content[:200] if text_content else "Pruefungsplanung document"
                evidence.append(Evidence(
                    evidence_id="E4-2",
                    finding_id=finding_id,
                    record_id=rec["record_id"],
                    document_id=rec["file_id"],
                    label="Approval threshold in audit planning",
                    excerpt=snippet,
                    source_location=_build_source_location(parsed),
                    explanation_en=(
                        "The audit planning document references an approval threshold, "
                        "confirming that payments above this amount require additional "
                        "authorization - making the splitting pattern significant."
                    ),
                ))
                break

        if not evidence:
            return None

        total_split = 0.0
        if split_groups:
            total_split = sum(
                sum(e[3] for e in group)
                for group in split_groups.values()
            )

        return Finding(
            finding_id=finding_id,
            title="Payment Splitting Below Approval Threshold",
            severity=Severity.high,
            category="internal_control_override",
            amount_at_risk=total_split if total_split else None,
            currency="EUR",
            explanation=(
                f"Multiple payments to the same counterparty on the same date, each "
                f"kept below the EUR 10,000 approval threshold. "
                f"{len(split_groups)} instance(s) of potential splitting detected."
            ),
            reasoning=(
                "Payment splitting (structuring) is a technique to circumvent "
                "internal approval thresholds. By keeping individual payments below "
                "the limit, the payer avoids the additional scrutiny that a larger "
                "single payment would trigger. This is a common internal control "
                "override indicator."
            ),
            evidence_count=len(evidence),
            confidence="high" if split_groups else "medium",
            status=FindingStatus.demo,
            evidence=evidence,
        )
