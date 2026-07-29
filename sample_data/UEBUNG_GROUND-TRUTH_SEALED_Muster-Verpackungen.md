## PART 1: WHERE THE FRAUD IS (brief)

**F1 (headline): Fake vendor paid for nothing.** A shell vendor **"Ratio Consulting GmbH" (209101)**, created mid-year, receives **5 round "Beratung" invoices totalling €248,000**. No goods receipt, and the vendor was set up, invoiced and paid by the *same* user **MV-U05** (created "by MV-U05, approved by MV-U05"). Cash misappropriation.

**F2: Repairs capitalised as fixed assets.** Six repair/maintenance bills (**€150,800 net**) booked as asset additions (accounts 040000/060000) instead of expense 670000. The "assets" carry repair names ("Reparatur Konfektioniermaschine", "Austausch Hydraulikaggregat"…). Profit and assets overstated.

**F3: December costs parked in January.** Eight supplier invoices for December 2025 deliveries (**€192,000 net**) are booked in January 2026 and **not accrued** at year-end (goods received in December, no 2025 posting). Profit overstated.

**F4: Split payments under the approval limit.** Four payments on **14.10.2025** to vendor **200007 (Castor Papier GmbH)**, each just under €10,000 (9,780 / 9,820 / 9,750 / 9,690 = **€39,040**), to dodge the €10,000 two-signature rule.

**Effect on the accounts:** F2 + F3 overstate profit by **~€342,800** (reported €2.60m → true ~€2.26m). F1 is €248,000 of cash stolen (booked as real expense, found via vendor analysis). F4 is a control breach, no misstatement.

---

## PART 2: HOW TO FIND EACH

### F1: fake vendor (main)
- `Kreditoren/Lieferantenbuchungen.txt`: vendor **209101** has 5 invoices + 5 payments, all round amounts, text "Beratungsleistungen".
- `Begleitdokumente/Wareneingangsliste_2025.csv`: **no goods receipt** for 209101 (services never delivered / no contract).
- `Begleitdokumente/Stammdatenaenderungen_2025.csv`: 209101 **"Neuanlage Kreditor" 12.05.2025, GEAENDERT_VON = MV-U05, GENEHMIGT_VON = MV-U05** (creator = approver, no four-eyes).
- `Begleitdokumente/Berechtigungsauswertung_2025.xlsx`: **MV-U05** holds *Buchen* + *Zahlungslauf* + *Kreditor anlegen*: one person can create and pay a vendor (broken segregation of duties).
- Cross-check the general ledger: the same user MV-U05 booked both the invoices and the payments; no prior-year balance for the vendor.
- Contrast with decoy **D3** (Vega Werkstoffe 209112): also new mid-year but with four-eyes + real goods receipts → legitimate.

### F2: repairs capitalised
- `AV/Anlagen.txt`: six asset records with **repair-type names** (grep "Reparatur|Instandsetzung|Austausch|Generalüberholung|Kälteanlage") in classes 040000/060000, added in 2025.
- `AV/Anlagenbuchungen.txt` / `Sachkontobuchungen.txt`: their acquisitions post to the asset account, not to 670000 "Instandhaltung und Reparaturen".
- Cross-check to the vendor invoices (same document numbers): the invoice describes a repair, but the debit is an asset.
- Tell: repairs belong in expense; these inflate assets and profit. (Contrast decoy **D1**: a €480k machine, a real capital asset with an investment request.)

### F3: December costs in January (cut-off)
- `Begleitdokumente/Fakturajournal_Januar_2026_Kreditoren.csv`: **8 invoices with FAKTURADATUM in January 2026 but LEISTUNGSDATUM in December 2025**.
- `Begleitdokumente/Wareneingangsliste_2025.csv`: matching **December goods receipts marked "Rechnung offen"** (goods in, invoice not yet booked).
- `Sachkonten/Sachkontobuchungen.txt`: **no 2025 accrual** for these (no Rückstellung / Verbindlichkeit booked in December).
- Contrast: a **legitimate €86,500 accrual** ("Rückstellung unfakturierte Leistungen Dez 2025") *is* booked for other unbilled December work, so the missing accrual on these 8 is the anomaly, not year-end accruals in general.

### F4: split payments
- `Sachkonten/Sachkontobuchungen.txt`: filter payments (BUCHUNGSTYP "Zahlung") and group by vendor + date. Vendor **200007 on 14.10.2025** has **4 payments each just under €10,000** (belegnr "SAMMEL-200007").
- `Begleitdokumente/Pruefungsplanung_JET_2025.docx`: states the **€10,000 payment-approval threshold**. Several near-threshold payments same day, same payee = threshold-splitting.

---

## DECOYS: look suspicious, are clean (accusing them loses precision)
- **D1**: €480,000 machine (round/large). Real capital investment, Investitionsantrag IA-2025-04 + asset. Legit.
- **D2**: "Nord Logistik GmbH" (209110) vs "Nordlicht Logistik GmbH" (209111): similar names but **different VAT-IDs and both with real goods receipts**, not a duplicate vendor.
- **D3**: "Vega Werkstoffe GmbH" (209112): new mid-year vendor **but** four-eyes + real deliveries/invoices. The honest twin of F1.
- **D4**: Year-end volume bonuses (440020, 22 customers): documented rebate, booked against 332000. Legit.
- **D5**: €220,000 Konzernumlage to the Austrian parent (209113): related party but disclosed in `Gesellschafterliste_Beteiligungen.csv`, arm's length.
- **D6**: Asset disposal 040000-000005 for €1,200 (book value ~€111,595): scrapping of an old machine, documented, not an under-value sale to a related party.
- **D7**: Invoice AR502040 + credit note SG502041 (€18,500 each) same period, revenue-neutral, a normal correction.

---

## Suggested scoring
- Top marks: catch **F1** by *combining* sources (new vendor + no goods receipt + creator=approver + rights), plus **F2/F3** (the profit-overstatement pair).
- Bonus: **F4**.
- Penalty: accusing any decoy (D1–D7).

## Note
100% synthetic (seeded generator, no real names/IBANs/tax-IDs). Regenerable and adjustable. The scheme set is deliberately *different* from the real BSP dataset (this one is purchasing/asset/cut-off/controls), so practising here does not reveal the real answers.
