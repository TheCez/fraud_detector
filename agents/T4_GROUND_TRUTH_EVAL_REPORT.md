# T4 - Live ground-truth evaluation of the single-call analyst

Model: `gpt-5.4`. 77 cases x 3 runs = 231 model calls in 80.8s wall clock (0 call(s) failed and are marked as such below, never scored as 'no finding').

Matching criterion: see this module's docstring. In short, an entry-scoped call means a
proposal here can only cite records belonging to this entry, so for F1-F4 'proposed anything'
and 'proposed something citing the seeded finding's own records' are the same event.

## Orchestrator findings

The generated diagnosis sections below were left as unfilled placeholders. This section replaces them
for the case that matters, and states the aggregate result the tables imply.

### Aggregate

| group | entries | flagged at least once | note |
|---|---|---|---|
| F1 shell vendor | 6 | 2 | neither hit is about the seeded issue - see below |
| F2 repair capitalization | 12 | 6 | 5 of 6 assets caught 3/3, titles on point |
| F3 period cut-off | 8 | 7 | one hit exact; the rest flag entry shape, not the cut-off |
| F4 split payments | 1 | 1 | 3/3, but titles are about posting classification, not splitting |
| D1-D7 decoys | 25 | 14 (56%) | includes vendor 209112, the honest twin, at 3/3 on two entries |
| CONTROL ordinary | 25 | 4 (16%) | extrapolates to roughly 780 findings across 4,902 entries |

Precision, not recall, is the problem. A 16% hit rate on ordinary entries makes the output unusable as
it stands, and the recurring false-positive shape is vague: "unusual balancing account", "unusual
period classification", "unusual counter account". Those are observations, not findings.

### F1 root cause - a context-assembly gap, not a model failure

The shell vendor's segregation-of-duties signal is **absent from every brief the analyst saw**. Verified
directly:

- Entry `PG-8134acaac5b855c4` holds the vendor master row from `Kreditoren/Lieferanten.txt`. Its fields
  are address and tax only - `LIEFERANTENNAME`, `LIEFERANTENUSTIDNR`, `MWST_GRUPPE` and so on. There is
  no changer and no approver column on that record at all.
- The self-approval lives in a *different* record entirely: a `master_change` row from
  `Begleitdokumente/Stammdatenaenderungen_2025.csv` carrying `KONTO: 209101`, `GEAENDERT_VON: MV-U05`,
  `GENEHMIGT_VON: MV-U05`, `GENEHMIGT: Ja`.
- That record is in **another process graph**. Entries are clustered by `document_join`, and the two
  records share no document - only the `vendor:209101` entity. The `Parties` section reports that
  vendor as aggregate counts, so the change record's own fields never appear anywhere in the brief.

The analyst could not have found this finding from what it was given. The graph exists to assemble all
data about a record; for an entity-mediated relationship like "who created and approved this vendor",
document-join clustering does not reach it.

Fix, bounded and cheap: for each party in an entry, render that party's own master-data and
master-change records verbatim rather than as counts. A vendor has one master row and a handful of
change rows, so this adds little to the brief and supplies exactly the who-created-and-approved-this
context the prompt already directs the model to observe.

### F4 - detected for the wrong reason

The split-payment entry was flagged 3/3, but every title is about payment posting classification. No
run remarked on four same-day payments each sitting below a round threshold while totalling above it.
Under the loose matching criterion this scores as recall; it is not. It is worth recording as a miss
that the criterion hides.

### The `Not present` section emits noise

Several briefs carry lines like "No record in this entry supplies a date; **0 of 197** entries with this
shape do." An absence that no peer supplies is information-free by construction - it says only that
this is normal for this shape. Suppress any absence line whose peer count is zero.

### What this says about the next tasks

Do not restore the deleted scenarios. Two of the four seeded findings were detected for demonstrably
correct reasons with no scenario list, and the F1 miss has nothing to do with the prompt.

The precision result is an argument for building T7 and T8 rather than a reason to doubt them. Many of
the 0/3 seeded entries are bare `asset_record` or `master_data` rows with no date, amount, counterparty
or document reference - exactly the `insufficient_data` class the gate exists to route out, which would
also remove them from the recall denominator honestly. And the refutation-biased verifier is aimed
precisely at the "unusual counter account" class of unsupported claim.


## F1 (seeded finding)

| entry | label | proposed (of 3 runs) | severities/titles seen |
|---|---|---|---|
| PG-0d0cb117c10c5123 | shell vendor 209101 | 0/3 | (none) |
| PG-682cb10675375f1d | shell vendor 209101 | 3/3 | Severity.medium: Vendor payment posted as another purchase booking in subledger; Severity.medium: Vendor payment posting is classified as purchase in vendor ledger; Severity.medium: Vendor posting for payment is classified as purchase |
| PG-8134acaac5b855c4 | shell vendor 209101 | 0/3 | (none) |
| PG-b2bf3e082bcf5751 | shell vendor 209101 | 0/3 | (none) |
| PG-cae577ce8ee35d35 | shell vendor 209101 | 0/3 | (none) |
| PG-cdf2d80edcb75829 | shell vendor 209101 | 1/3 | Severity.medium: Consulting invoice partly booked to asset account without supporting asset posting |

## F2 (seeded finding)

| entry | label | proposed (of 3 runs) | severities/titles seen |
|---|---|---|---|
| PG-e849340abf4f5b8a | asset 040000-000191 | 3/3 | Severity.medium: Capitalized acquisition posted with repair description; Severity.medium: Repair invoice partly capitalized as asset acquisition |
| PG-f8c22a8f1c625db0 | asset 040000-000191 | 2/3 | Severity.medium: Asset master record classifies a repair as a tangible fixed asset; Severity.medium: Asset master record describes a repair while classified in fixed-asset group 040000 |
| PG-7a6b12401fbe570a | asset 040000-000192 | 0/3 | (none) |
| PG-be8ce9514a6d5150 | asset 040000-000192 | 0/3 | (none) |
| PG-2f299a2fe3bd5cf2 | asset 040000-000194 | 0/3 | (none) |
| PG-5d20bfb443c753f1 | asset 040000-000194 | 3/3 | Severity.medium: Capitalized overhaul booked with full invoice amount split into asset and separate tax line, but no tax reference is present; Severity.medium: Capitalized overhaul split between asset cost and separate expense on same vendor invoice; Severity.medium: Capitalized overhaul with separate non-capitalized tax component warrants review |
| PG-81e8bcc57ce0558c | asset 040000-000196 | 0/3 | (none) |
| PG-9211e70182bf5800 | asset 040000-000196 | 3/3 | Severity.medium: Capitalized acquisition booked from repair-like invoice text without supporting distinction in entry; Severity.medium: Capitalized acquisition text suggests replacement work rather than a clearly identifiable new asset addition; Severity.medium: Capitalized acquisition text suggests replacement work rather than a new asset addition |
| PG-427ae1d8573d530c | asset 060000-000193 | 0/3 | (none) |
| PG-752a9dcc64fa589f | asset 060000-000193 | 3/3 | Severity.medium: Mixed capitalization and expense posting within one vendor invoice should be reviewed; Severity.medium: Mixed capitalization and expense treatment within one vendor invoice should be reviewed; Severity.medium: Mixed capitalization and repair expense in one vendor invoice should be reviewed |
| PG-185932582ebd510e | asset 060000-000195 | 3/3 | Severity.medium: Capitalized acquisition posted with repair expense text on the same vendor invoice; Severity.medium: Repair invoice partly capitalized as an asset acquisition; Severity.medium: Repair invoice partly capitalized as asset acquisition |
| PG-4969046e14c95ee6 | asset 060000-000195 | 0/3 | (none) |

## F3 (seeded finding)

| entry | label | proposed (of 3 runs) | severities/titles seen |
|---|---|---|---|
| PG-394a1a3cecbf5db6 | Jan-2026 invoice, Dec-2025 delivery | 0/3 | (none) |
| PG-973fb7048d375558 | Jan-2026 invoice, Dec-2025 delivery | 2/3 | Severity.low: Vendor invoice lacks linked posting records present in a comparable entry shape; Severity.medium: Standalone vendor invoice without corresponding posting records in an otherwise rare shape |
| PG-99da448b5cdd5756 | Jan-2026 invoice, Dec-2025 delivery | 2/3 | Severity.medium: Standalone vendor invoice without corresponding posting records in this entry; Severity.medium: Standalone vendor invoice without linked posting in a dossier where a fuller record shape also exists |
| PG-ad0ce0559efb5b07 | Jan-2026 invoice, Dec-2025 delivery | 3/3 | Severity.medium: Invoice date and service date do not align with the invoice note; Severity.medium: Invoice date and stated raw-material date do not align with recorded service date; Severity.medium: Invoice service date and remark date do not agree |
| PG-b4a07a4a52835383 | Jan-2026 invoice, Dec-2025 delivery | 2/3 | Severity.medium: Single vendor invoice lacks linked posting records seen elsewhere; Severity.medium: Standalone vendor invoice without corresponding posting records in this entry |
| PG-cf7b803ede4c5820 | Jan-2026 invoice, Dec-2025 delivery | 2/3 | Severity.medium: Invoice-only freight entry lacks the accounting records seen in a comparable entry shape; Severity.medium: Standalone freight invoice without linked posting records |
| PG-e7b5b9a2ca02564c | Jan-2026 invoice, Dec-2025 delivery | 2/3 | Severity.medium: High-value standalone vendor invoice without linked posting records; Severity.medium: Standalone vendor invoice without corresponding posting records |
| PG-ea3312fcbcb25993 | Jan-2026 invoice, Dec-2025 delivery | 3/3 | Severity.low: Standalone energy invoice without linked posting in a dossier where a fuller record shape also exists; Severity.medium: High-value energy invoice recorded without linked posting or settlement records; Severity.medium: Standalone vendor invoice without corresponding posting records in this entry |

## F4 (seeded finding)

| entry | label | proposed (of 3 runs) | severities/titles seen |
|---|---|---|---|
| PG-15782c5108775a9d | split payments, document SAMMEL-200007 | 3/3 | Severity.medium: Unusual vendor payment entry shape booked as purchase postings; Severity.medium: Vendor payment entry classified as purchase postings with no settlement reference; Severity.medium: Vendor payment recorded as purchase postings without matching bank-side journal lines in the entry |

## D1 (decoy)

| entry | label | proposed (of 3 runs) | severities/titles seen |
|---|---|---|---|
| PG-ac07e89706c35168 | 480k production-line investment (doc ER901435) | 2/3 | Severity.low: High-value acquisition posted with unusual period assignment text and one blank line description; Severity.low: High-value asset acquisition posted as a balanced vendor invoice with no entry-specific anomaly visible |

## D2 (decoy)

| entry | label | proposed (of 3 runs) | severities/titles seen |
|---|---|---|---|
| PG-5e577d2f5bb95579 | vendor 209110 | 3/3 | Severity.medium: Goods receipt amount does not reconcile to posted invoice total; Severity.medium: Invoice amount exceeds linked goods receipt without visible supporting breakdown; Severity.medium: Vendor invoice total exceeds linked goods receipt without explicit supporting detail |
| PG-6a59fb04d7465a6e | vendor 209110 | 0/3 | (none) |
| PG-7e280d83a53f50b1 | vendor 209110 | 1/3 | Severity.medium: Unusual balancing account used for the full invoice split |
| PG-a77b7c4cbecc56a5 | vendor 209110 | 0/3 | (none) |
| PG-0ec8dd31a8ac5b21 | vendor 209111 | 3/3 | Severity.medium: Goods receipt amount does not match booked invoice net amount; Severity.medium: Goods receipt amount does not match invoiced logistics expense and tax posting; Severity.medium: Goods receipt amount does not match invoiced net amount on the linked vendor invoice |
| PG-50b0e7164e4958ef | vendor 209111 | 0/3 | (none) |
| PG-9d8ef98e350c5659 | vendor 209111 | 0/3 | (none) |
| PG-bb9774a3aa9d572c | vendor 209111 | 0/3 | (none) |

## D3 (decoy)

| entry | label | proposed (of 3 runs) | severities/titles seen |
|---|---|---|---|
| PG-232365a05b6c5d8b | vendor 209112 (honest twin of F1) | 0/3 | (none) |
| PG-7891cbf6e1e9567e | vendor 209112 (honest twin of F1) | 3/3 | Severity.medium: Unusual balancing account used on a routine vendor invoice entry; Severity.medium: Unusual counter account and period classification on same-day material invoice; Severity.medium: Unusual interim account appears only on this posting date |
| PG-a2739d16a5cb54e6 | vendor 209112 (honest twin of F1) | 3/3 | Severity.medium: Invoice posting uses an unusual contra account and period classification for a standard material receipt; Severity.medium: Unusual balancing account and period classification on same-day material invoice; Severity.medium: Unusual balancing account used on all journal lines of the vendor invoice |
| PG-ab492caaf1165a84 | vendor 209112 (honest twin of F1) | 0/3 | (none) |
| PG-d53fdffa277453b6 | vendor 209112 (honest twin of F1) | 2/3 | Severity.medium: Unusual balancing line to one-off counter account warrants review; Severity.medium: Unusual offset account used only on this date for the vendor invoice posting |

## D4 (decoy)

| entry | label | proposed (of 3 runs) | severities/titles seen |
|---|---|---|---|
| PG-200bf7936c885588 | volume-bonus account 440020 (5 of 22 entries sampled, seed=4902) | 0/3 | (none) |
| PG-781e33dd7bd45084 | volume-bonus account 440020 (5 of 22 entries sampled, seed=4902) | 3/3 | Severity.medium: Year-end bonus journal entered after posting date without supporting document reference; Severity.medium: Year-end bonus journal posted after period end without supporting document reference; Severity.medium: Year-end manual bonus journal posted after period end without supporting document reference |
| PG-81ecbebffba75d83 | volume-bonus account 440020 (5 of 22 entries sampled, seed=4902) | 1/3 | Severity.medium: Year-end manual bonus journal entered after period end without underlying document reference |
| PG-96e9e98f93075d29 | volume-bonus account 440020 (5 of 22 entries sampled, seed=4902) | 2/3 | Severity.medium: Year-end bonus journal entered after posting date without supporting document reference; Severity.medium: Year-end manual journal for customer bonus posted after period end without linked supporting document |
| PG-e6d776959e5450bd | volume-bonus account 440020 (5 of 22 entries sampled, seed=4902) | 0/3 | (none) |

## D5 (decoy)

| entry | label | proposed (of 3 runs) | severities/titles seen |
|---|---|---|---|
| PG-3674ec3c25725c44 | vendor 209113 (disclosed related-party charge) | 3/3 | Severity.medium: Rare year-end vendor invoice entry with unusual posting pattern and missing invoice number; Severity.medium: Unusual year-end vendor invoice shape without the asset posting seen in related entries; Severity.medium: Year-end vendor invoice posted in unique shape without accompanying asset posting |
| PG-9587f665e5615a32 | vendor 209113 (disclosed related-party charge) | 0/3 | (none) |

## D6 (decoy)

| entry | label | proposed (of 3 runs) | severities/titles seen |
|---|---|---|---|
| PG-6762bf56a7575677 | asset 040000-000005 disposal | 0/3 | (none) |
| PG-aeb8f6f9665e5ab9 | asset 040000-000005 disposal | 3/3 | Severity.medium: Unique asset disposal journal uses only internal clearing and lacks the vendor posting seen in related entries; Severity.medium: Unique asset disposal journal with unusual period classification and sparse document detail; Severity.medium: Unusual fixed-asset disposal posted only through journals without a counterparty document |

## D7 (decoy)

| entry | label | proposed (of 3 runs) | severities/titles seen |
|---|---|---|---|
| PG-3b7fc01059d45890 | invoice AR502040 / credit note SG502041 | 3/3 | Severity.medium: Unique receivables posting uses unusual counter account and lacks invoice record seen in related shape; Severity.medium: Unique receivables posting without accompanying invoice record; Severity.medium: Unique sales posting lacks the invoice record that appears in the related comparable shape |
| PG-a21fa63b7dc55dbb | invoice AR502040 / credit note SG502041 | 3/3 | Severity.low: Unique customer credit note posting without goods dispatch record; Severity.low: Use of counter account 7708391 appears isolated to this single document set; Severity.medium: Standalone sales credit note without goods dispatch in a dossier where comparable sales entries usually include dispatch; Severity.medium: Unique sales credit note without goods dispatch record and with one-off counter account |

## CONTROL (control sample)

| entry | label | proposed (of 3 runs) | severities/titles seen |
|---|---|---|---|
| PG-095be45a11865429 | ordinary entry (25 of 4850 eligible sampled, seed=4903) | 0/3 | (none) |
| PG-11616d1c47d754b1 | ordinary entry (25 of 4850 eligible sampled, seed=4903) | 0/3 | (none) |
| PG-14bacfc8309c57c9 | ordinary entry (25 of 4850 eligible sampled, seed=4903) | 0/3 | (none) |
| PG-334fa6ea015c531d | ordinary entry (25 of 4850 eligible sampled, seed=4903) | 3/3 | Severity.medium: Payment-side vendor posting is classified as purchase while text and linked journal entries show an outgoing payment; Severity.medium: Vendor posting type and text do not match the September payment event; Severity.medium: Vendor subledger posting type/text on payment does not match the business event shown |
| PG-34ce6c4643ef5335 | ordinary entry (25 of 4850 eligible sampled, seed=4903) | 0/3 | (none) |
| PG-3720e9c2721b518b | ordinary entry (25 of 4850 eligible sampled, seed=4903) | 0/3 | (none) |
| PG-401ee2c19f235fc0 | ordinary entry (25 of 4850 eligible sampled, seed=4903) | 0/3 | (none) |
| PG-4a6f8e1487c75392 | ordinary entry (25 of 4850 eligible sampled, seed=4903) | 0/3 | (none) |
| PG-4c597a4025d25d31 | ordinary entry (25 of 4850 eligible sampled, seed=4903) | 0/3 | (none) |
| PG-7c490fce64a85077 | ordinary entry (25 of 4850 eligible sampled, seed=4903) | 0/3 | (none) |
| PG-7cd8bd4387e553ba | ordinary entry (25 of 4850 eligible sampled, seed=4903) | 0/3 | (none) |
| PG-890798747d5c5c55 | ordinary entry (25 of 4850 eligible sampled, seed=4903) | 0/3 | (none) |
| PG-8ca1a9e085b05c06 | ordinary entry (25 of 4850 eligible sampled, seed=4903) | 0/3 | (none) |
| PG-8fd708772cfd5c9e | ordinary entry (25 of 4850 eligible sampled, seed=4903) | 0/3 | (none) |
| PG-97ba3322aae95af5 | ordinary entry (25 of 4850 eligible sampled, seed=4903) | 0/3 | (none) |
| PG-aa9ba7f969295d3b | ordinary entry (25 of 4850 eligible sampled, seed=4903) | 0/3 | (none) |
| PG-b2a058a4e6075f7f | ordinary entry (25 of 4850 eligible sampled, seed=4903) | 0/3 | (none) |
| PG-b8db066a07295702 | ordinary entry (25 of 4850 eligible sampled, seed=4903) | 1/3 | Severity.low: Year-end sales entry uses unusual period assignment and split posting users |
| PG-bfd835dee9a056f1 | ordinary entry (25 of 4850 eligible sampled, seed=4903) | 0/3 | (none) |
| PG-c48f5b603b5b5739 | ordinary entry (25 of 4850 eligible sampled, seed=4903) | 0/3 | (none) |
| PG-ccc5fa5f6e225861 | ordinary entry (25 of 4850 eligible sampled, seed=4903) | 3/3 | Severity.medium: Fuhrpark invoice posted without accompanying asset posting despite split between expense and tax accounts; Severity.medium: Fuhrpark invoice posted without accompanying asset posting despite use of account 147000; Severity.medium: High-value fleet invoice posted without supporting asset posting although similar cases sometimes include one |
| PG-cdccef4523545fb0 | ordinary entry (25 of 4850 eligible sampled, seed=4903) | 0/3 | (none) |
| PG-dc07c459b33d5ba8 | ordinary entry (25 of 4850 eligible sampled, seed=4903) | 0/3 | (none) |
| PG-f435daf126f85bab | ordinary entry (25 of 4850 eligible sampled, seed=4903) | 0/3 | (none) |
| PG-fa1e5a2deb7c59df | ordinary entry (25 of 4850 eligible sampled, seed=4903) | 2/3 | Severity.low: Opening vendor balance posted without document number or settlement reference; Severity.medium: Opening vendor balance posted without supporting entry detail |

## Sample counts

- F1:shell vendor 209101: 6
- F2:asset 040000-000191: 2
- F2:asset 040000-000192: 2
- F2:asset 040000-000194: 2
- F2:asset 040000-000196: 2
- F2:asset 060000-000193: 2
- F2:asset 060000-000195: 2
- F3:Jan-2026 invoice, Dec-2025 delivery: 8
- F4:split payments, document SAMMEL-200007: 1
- D1:480k production-line investment (doc ER901435): 1
- D2:vendor 209110: 4
- D2:vendor 209111: 4
- D3:vendor 209112 (honest twin of F1): 5
- D4:volume-bonus account 440020 (5 of 22 entries sampled, seed=4902): 5
- D4:total_candidates: 22
- D5:vendor 209113 (disclosed related-party charge): 2
- D6:asset 040000-000005 disposal: 2
- D7:invoice AR502040 / credit note SG502041: 2
- CONTROL:ordinary entry (25 of 4850 eligible sampled, seed=4903): 25
- CONTROL:total_eligible: 4850

## Diagnosis of every seeded-finding entry with fewer than 3/3 proposals

### F1 - shell vendor 209101 - entry `PG-0d0cb117c10c5123` (0/3 runs proposed anything)

Relevant brief excerpt (Entry/Not present/Parties sections):

```
Entry PG-0d0cb117c10c5123
Record types: journal_entry, vendor_posting
Subtotals by record type: journal_entry EUR negative=-107100.00, journal_entry EUR positive=107100.00, vendor_posting EUR negative=-53550.00, vendor_posting EUR positive=53550.00
Date span: 2025-05-19 to 2025-05-21
Shape frequency: this combination of record types occurs 452 times of 4902 entries in the dossier

Parties
Party account:147000
  Roles in this entry: references (in, record:f9303bcf-a720-56f7-a978-724135dd007e)
  Dossier-wide: 1441 record(s), 2025-01-01 to 2025-12-31, total 9254878.34 (mean 6427.00), master-data references: 1, co-occurring entities: 1442
  Edges: references=1441 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, sold_to, to_account
Party account:271000
  Roles in this entry: references (in, record:47c3baab-6df1-54b2-82ca-902e8a58b184)
  Dossier-wide: 2824 record(s), 2025-01-01 to 2025-12-31, total 61539118.29 (mean 21799.19), master-data references: 1, co-occurring entities: 2826
  Edges: references=2824 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, sold_to, to_account
Party account:330000
  Roles in this entry: references (in, record:073f1f33-8f92-592c-a7db-c908a8848ad9), references (in, record:e7a13599-8713-5862-b844-df507b2a2e30)
  Dossier-wide: 2586 record(s), 2025-01-01 to 2025-12-31, total 27024153.36 (mean 10454.22), master-data references: 1, co-occurring entities: 2694
  Edges: references=2586 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, sold_to, to_account
Party account:673000
  Roles in this entry: references (in, record:db97699e-a632-5c2f-9230-9ceee4019a46)
  Dossier-wide: 68 record(s), 2025-01-09 to 2025-12-26, total 1897618.00 (mean 28322.66), master-data references: 1, co-occurring entities: 68
  Edges: references=68 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, sold_to, to_account
Party account:7708356
  Roles in this entry: to_account (in, record:073f1f33-8f92-592c-a7db-c908a8848ad9), to_account (in, record:db97699e-a632-5c2f-9230-9ceee4019a46), to_account (in, record:f9303bcf-a720-56f7-a978-724135dd007e)
  Dossier-wide: 3 record(s), 2025-05-19 to 2025-05-19, total 0.00 (mean 0.00), master-data references: 0, co-occurring entities: 5
  Edges: to_account=3 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, references, sold_to
Party account:7708357
  Roles in this entry: to_account (in, record:47c3baab-6df1-54b2-82ca-902e8a58b184), to_account (in, record:e7a13599-8713-5862-b844-df507b2a2e30)
  Dossier-wide: 2 record(s), 2025-05-21 to 2025-05-21, total 0.00 (mean 0.00), master-data references: 0, co-occurring entities: 4
  Edges: to_account=2 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, references, sold_to
Party user:MV-U05
  Roles in this entry: posted_by (in, record:073f1f33-8f92-592c-a7db-c908a8848ad9), posted_by (in, record:47c3baab-6df1-54b2-82ca-902e8a58b184), posted_by (in, record:5579780a-612b-52e9-b630-e8c842a2714b), posted_by (in, record:db97699e-a632-5c2f-9230-9ceee4019a46), posted_by (in, record:e7a13599-8713-5862-b844-df507b2a2e30), posted_by (in, record:f4abbb3e-bedc-5ad5-904d-84710d3612dd), posted_by (in, record:f9303bcf-a720-56f7-a978-724135dd007e)
  Dossier-wide: 1936 record(s), 2025-01-01 to 2025-12-31, total -1476064.16 (mean -763.22), master-data references: 1, co-occurring entities: 727
  Edges: approved_by=1, changed_by=1, posted_by=1934, references=1 | none: c
```

_Diagnosis: fill in by hand which of agents/PROMPTS.md SS2's observation directions (dates, documents present/absent, roles, amounts, classification, text) should have surfaced this from the excerpt above, and whether the brief actually carried the fact needed._

### F1 - shell vendor 209101 - entry `PG-8134acaac5b855c4` (0/3 runs proposed anything)

Relevant brief excerpt (Entry/Not present/Parties sections):

```
Entry PG-8134acaac5b855c4
Record types: master_data
Subtotals by record type: none
Date span: n/a to n/a
Shape frequency: this combination of record types occurs 601 times of 4902 entries in the dossier

Parties
Party vendor:209101
  Roles in this entry: references (in, record:1556a286-48ae-5e8a-8e67-be7e827628eb)
  Dossier-wide: 21 record(s), 2025-05-19 to 2025-12-20, total 0.00 (mean 0.00), master-data references: 1, co-occurring entities: 12
  Edges: paid_to=10, references=11 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, posted_by, processed_by, received_from, sold_to, to_account

Not present
No record in this entry supplies a date; 91 of 601 entries with this shape do.
No record in this entry supplies an amount; 160 of 601 entries with this shape do.
No record in this entry supplies a source-document reference; 0 of 601 entries with this shape do.
160 of 601 entries with this shape carry a sold_to edge; this entry does not.
```

_Diagnosis: fill in by hand which of agents/PROMPTS.md SS2's observation directions (dates, documents present/absent, roles, amounts, classification, text) should have surfaced this from the excerpt above, and whether the brief actually carried the fact needed._

### F1 - shell vendor 209101 - entry `PG-b2bf3e082bcf5751` (0/3 runs proposed anything)

Relevant brief excerpt (Entry/Not present/Parties sections):

```
Entry PG-b2bf3e082bcf5751
Record types: journal_entry, vendor_posting
Subtotals by record type: journal_entry EUR negative=-123760.00, journal_entry EUR positive=123760.00, vendor_posting EUR negative=-61880.00, vendor_posting EUR positive=61880.00
Date span: 2025-11-10 to 2025-11-12
Shape frequency: this combination of record types occurs 452 times of 4902 entries in the dossier

Parties
Party account:147000
  Roles in this entry: references (in, record:ece1bad7-cfa9-56d2-9998-f8c4eb0fb092)
  Dossier-wide: 1441 record(s), 2025-01-01 to 2025-12-31, total 9254878.34 (mean 6427.00), master-data references: 1, co-occurring entities: 1442
  Edges: references=1441 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, sold_to, to_account
Party account:271000
  Roles in this entry: references (in, record:5dbace70-c84e-58f2-88cd-e2ad01f600ba)
  Dossier-wide: 2824 record(s), 2025-01-01 to 2025-12-31, total 61539118.29 (mean 21799.19), master-data references: 1, co-occurring entities: 2826
  Edges: references=2824 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, sold_to, to_account
Party account:330000
  Roles in this entry: references (in, record:6fd95721-0676-565d-876c-762b8638d8af), references (in, record:9a7e3c0b-2479-5667-8b3b-0c1c205e897c)
  Dossier-wide: 2586 record(s), 2025-01-01 to 2025-12-31, total 27024153.36 (mean 10454.22), master-data references: 1, co-occurring entities: 2694
  Edges: references=2586 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, sold_to, to_account
Party account:673000
  Roles in this entry: references (in, record:3a464173-b7a7-56cb-a6b2-0a6fde9b5b15)
  Dossier-wide: 68 record(s), 2025-01-09 to 2025-12-26, total 1897618.00 (mean 28322.66), master-data references: 1, co-occurring entities: 68
  Edges: references=68 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, sold_to, to_account
Party account:7708362
  Roles in this entry: to_account (in, record:3a464173-b7a7-56cb-a6b2-0a6fde9b5b15), to_account (in, record:6fd95721-0676-565d-876c-762b8638d8af), to_account (in, record:ece1bad7-cfa9-56d2-9998-f8c4eb0fb092)
  Dossier-wide: 3 record(s), 2025-11-10 to 2025-11-10, total 0.00 (mean 0.00), master-data references: 0, co-occurring entities: 5
  Edges: to_account=3 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, references, sold_to
Party account:7708363
  Roles in this entry: to_account (in, record:5dbace70-c84e-58f2-88cd-e2ad01f600ba), to_account (in, record:9a7e3c0b-2479-5667-8b3b-0c1c205e897c)
  Dossier-wide: 2 record(s), 2025-11-12 to 2025-11-12, total 0.00 (mean 0.00), master-data references: 0, co-occurring entities: 4
  Edges: to_account=2 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, references, sold_to
Party user:MV-U05
  Roles in this entry: posted_by (in, record:10ebc697-f875-5ec9-a9af-7a6ab4052858), posted_by (in, record:30e370e5-8205-5df2-a7e0-836308cf93c8), posted_by (in, record:3a464173-b7a7-56cb-a6b2-0a6fde9b5b15), posted_by (in, record:5dbace70-c84e-58f2-88cd-e2ad01f600ba), posted_by (in, record:6fd95721-0676-565d-876c-762b8638d8af), posted_by (in, record:9a7e3c0b-2479-5667-8b3b-0c1c205e897c), posted_by (in, record:ece1bad7-cfa9-56d2-9998-f8c4eb0fb092)
  Dossier-wide: 1936 record(s), 2025-01-01 to 2025-12-31, total -1476064.16 (mean -763.22), master-data references: 1, co-occurring entities: 727
  Edges: approved_by=1, changed_by=1, posted_by=1934, references=1 | none: c
```

_Diagnosis: fill in by hand which of agents/PROMPTS.md SS2's observation directions (dates, documents present/absent, roles, amounts, classification, text) should have surfaced this from the excerpt above, and whether the brief actually carried the fact needed._

### F1 - shell vendor 209101 - entry `PG-cae577ce8ee35d35` (0/3 runs proposed anything)

Relevant brief excerpt (Entry/Not present/Parties sections):

```
Entry PG-cae577ce8ee35d35
Record types: journal_entry, vendor_posting
Subtotals by record type: journal_entry EUR negative=-90440.00, journal_entry EUR positive=90440.00, vendor_posting EUR negative=-45220.00, vendor_posting EUR positive=45220.00
Date span: 2025-09-15 to 2025-09-17
Shape frequency: this combination of record types occurs 452 times of 4902 entries in the dossier

Parties
Party account:147000
  Roles in this entry: references (in, record:365a1e94-1e4b-5a9d-88ab-68a1aafb3059)
  Dossier-wide: 1441 record(s), 2025-01-01 to 2025-12-31, total 9254878.34 (mean 6427.00), master-data references: 1, co-occurring entities: 1442
  Edges: references=1441 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, sold_to, to_account
Party account:271000
  Roles in this entry: references (in, record:f79d61bc-168b-5602-8c7f-ae8745b41284)
  Dossier-wide: 2824 record(s), 2025-01-01 to 2025-12-31, total 61539118.29 (mean 21799.19), master-data references: 1, co-occurring entities: 2826
  Edges: references=2824 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, sold_to, to_account
Party account:330000
  Roles in this entry: references (in, record:6eab5d4d-a145-5ced-b56e-8023d291a9c5), references (in, record:a3a079ab-81d7-5643-b8af-b477bab53b29)
  Dossier-wide: 2586 record(s), 2025-01-01 to 2025-12-31, total 27024153.36 (mean 10454.22), master-data references: 1, co-occurring entities: 2694
  Edges: references=2586 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, sold_to, to_account
Party account:673000
  Roles in this entry: references (in, record:78652e26-1140-558f-9a72-23d6bdcc3a0b)
  Dossier-wide: 68 record(s), 2025-01-09 to 2025-12-26, total 1897618.00 (mean 28322.66), master-data references: 1, co-occurring entities: 68
  Edges: references=68 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, sold_to, to_account
Party account:7708360
  Roles in this entry: to_account (in, record:365a1e94-1e4b-5a9d-88ab-68a1aafb3059), to_account (in, record:78652e26-1140-558f-9a72-23d6bdcc3a0b), to_account (in, record:a3a079ab-81d7-5643-b8af-b477bab53b29)
  Dossier-wide: 3 record(s), 2025-09-15 to 2025-09-15, total 0.00 (mean 0.00), master-data references: 0, co-occurring entities: 5
  Edges: to_account=3 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, references, sold_to
Party account:7708361
  Roles in this entry: to_account (in, record:6eab5d4d-a145-5ced-b56e-8023d291a9c5), to_account (in, record:f79d61bc-168b-5602-8c7f-ae8745b41284)
  Dossier-wide: 2 record(s), 2025-09-17 to 2025-09-17, total 0.00 (mean 0.00), master-data references: 0, co-occurring entities: 4
  Edges: to_account=2 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, references, sold_to
Party user:MV-U05
  Roles in this entry: posted_by (in, record:365a1e94-1e4b-5a9d-88ab-68a1aafb3059), posted_by (in, record:6eab5d4d-a145-5ced-b56e-8023d291a9c5), posted_by (in, record:78652e26-1140-558f-9a72-23d6bdcc3a0b), posted_by (in, record:94b2eeb0-265f-5dba-8a68-315f65a35af8), posted_by (in, record:a3a079ab-81d7-5643-b8af-b477bab53b29), posted_by (in, record:c4c89739-329b-5ffd-be22-d060b7300ca8), posted_by (in, record:f79d61bc-168b-5602-8c7f-ae8745b41284)
  Dossier-wide: 1936 record(s), 2025-01-01 to 2025-12-31, total -1476064.16 (mean -763.22), master-data references: 1, co-occurring entities: 727
  Edges: approved_by=1, changed_by=1, posted_by=1934, references=1 | none: cap
```

_Diagnosis: fill in by hand which of agents/PROMPTS.md SS2's observation directions (dates, documents present/absent, roles, amounts, classification, text) should have surfaced this from the excerpt above, and whether the brief actually carried the fact needed._

### F1 - shell vendor 209101 - entry `PG-cdf2d80edcb75829` (1/3 runs proposed anything)

Relevant brief excerpt (Entry/Not present/Parties sections):

```
Entry PG-cdf2d80edcb75829
Record types: journal_entry, vendor_posting
Subtotals by record type: journal_entry EUR negative=-126140.00, journal_entry EUR positive=126140.00, vendor_posting EUR negative=-63070.00, vendor_posting EUR positive=63070.00
Date span: 2025-12-18 to 2025-12-20
Shape frequency: this combination of record types occurs 452 times of 4902 entries in the dossier

Parties
Party account:147000
  Roles in this entry: references (in, record:2276d734-8683-5ecd-9869-0e4a5b7bc9cd)
  Dossier-wide: 1441 record(s), 2025-01-01 to 2025-12-31, total 9254878.34 (mean 6427.00), master-data references: 1, co-occurring entities: 1442
  Edges: references=1441 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, sold_to, to_account
Party account:271000
  Roles in this entry: references (in, record:c3dca6bb-e841-513e-b940-5746aa6c8d52)
  Dossier-wide: 2824 record(s), 2025-01-01 to 2025-12-31, total 61539118.29 (mean 21799.19), master-data references: 1, co-occurring entities: 2826
  Edges: references=2824 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, sold_to, to_account
Party account:330000
  Roles in this entry: references (in, record:6c93df72-9a42-526b-a7ca-a54ba83400d7), references (in, record:d6b2bdff-b413-52cb-a526-131eb40c6fb5)
  Dossier-wide: 2586 record(s), 2025-01-01 to 2025-12-31, total 27024153.36 (mean 10454.22), master-data references: 1, co-occurring entities: 2694
  Edges: references=2586 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, sold_to, to_account
Party account:673000
  Roles in this entry: references (in, record:976cf5de-2db8-513d-8483-6121f8bb8983)
  Dossier-wide: 68 record(s), 2025-01-09 to 2025-12-26, total 1897618.00 (mean 28322.66), master-data references: 1, co-occurring entities: 68
  Edges: references=68 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, sold_to, to_account
Party account:7708364
  Roles in this entry: to_account (in, record:2276d734-8683-5ecd-9869-0e4a5b7bc9cd), to_account (in, record:976cf5de-2db8-513d-8483-6121f8bb8983), to_account (in, record:d6b2bdff-b413-52cb-a526-131eb40c6fb5)
  Dossier-wide: 3 record(s), 2025-12-18 to 2025-12-18, total 0.00 (mean 0.00), master-data references: 0, co-occurring entities: 5
  Edges: to_account=3 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, references, sold_to
Party account:7708365
  Roles in this entry: to_account (in, record:6c93df72-9a42-526b-a7ca-a54ba83400d7), to_account (in, record:c3dca6bb-e841-513e-b940-5746aa6c8d52)
  Dossier-wide: 2 record(s), 2025-12-20 to 2025-12-20, total 0.00 (mean 0.00), master-data references: 0, co-occurring entities: 4
  Edges: to_account=2 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, references, sold_to
Party user:MV-U05
  Roles in this entry: posted_by (in, record:2276d734-8683-5ecd-9869-0e4a5b7bc9cd), posted_by (in, record:6c93df72-9a42-526b-a7ca-a54ba83400d7), posted_by (in, record:7472ccaa-8320-5dcf-9656-ed174fd8211d), posted_by (in, record:976cf5de-2db8-513d-8483-6121f8bb8983), posted_by (in, record:b860a0d4-7a69-56f3-8c45-6e703d36fa49), posted_by (in, record:c3dca6bb-e841-513e-b940-5746aa6c8d52), posted_by (in, record:d6b2bdff-b413-52cb-a526-131eb40c6fb5)
  Dossier-wide: 1936 record(s), 2025-01-01 to 2025-12-31, total -1476064.16 (mean -763.22), master-data references: 1, co-occurring entities: 727
  Edges: approved_by=1, changed_by=1, posted_by=1934, references=1 | none: c
```

_Diagnosis: fill in by hand which of agents/PROMPTS.md SS2's observation directions (dates, documents present/absent, roles, amounts, classification, text) should have surfaced this from the excerpt above, and whether the brief actually carried the fact needed._

### F2 - asset 040000-000191 - entry `PG-f8c22a8f1c625db0` (2/3 runs proposed anything)

Relevant brief excerpt (Entry/Not present/Parties sections):

```
Entry PG-f8c22a8f1c625db0
Record types: asset_record
Subtotals by record type: none
Date span: n/a to n/a
Shape frequency: this combination of record types occurs 197 times of 4902 entries in the dossier

Parties
Party account:040000
  Roles in this entry: references (in, record:864e33d5-19d2-5a39-a8e3-142baf11ab44)
  Dossier-wide: 128 record(s), 2025-01-01 to 2025-12-31, total 8167298.88 (mean 220737.81), master-data references: 1, co-occurring entities: 111
  Edges: capitalized_to=18, references=110 | none: approved_by, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, sold_to, to_account
Party asset:040000-000191
  Roles in this entry: references (in, record:864e33d5-19d2-5a39-a8e3-142baf11ab44)
  Dossier-wide: 3 record(s), 2025-11-20 to 2025-11-20, total 56000.00 (mean 28000.00), master-data references: 0, co-occurring entities: 3
  Edges: references=3 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, sold_to, to_account

Not present
No record in this entry supplies a date; 0 of 197 entries with this shape do.
No record in this entry supplies an amount; 0 of 197 entries with this shape do.
No record in this entry supplies a named counterparty; 0 of 197 entries with this shape do.
No record in this entry supplies a source-document reference; 0 of 197 entries with this shape do.
```

_Diagnosis: fill in by hand which of agents/PROMPTS.md SS2's observation directions (dates, documents present/absent, roles, amounts, classification, text) should have surfaced this from the excerpt above, and whether the brief actually carried the fact needed._

### F2 - asset 040000-000192 - entry `PG-7a6b12401fbe570a` (0/3 runs proposed anything)

Relevant brief excerpt (Entry/Not present/Parties sections):

```
Entry PG-7a6b12401fbe570a
Record types: asset_posting, journal_entry, vendor_posting
Subtotals by record type: asset_posting EUR positive=34000.00, journal_entry EUR negative=-40460.00, journal_entry EUR positive=40460.00, vendor_posting EUR negative=-40460.00
Date span: 2025-03-04 to 2025-03-04
Shape frequency: this combination of record types occurs 7 times of 4902 entries in the dossier

Parties
Party account:040000
  Roles in this entry: capitalized_to (in, record:3e267b5a-4876-59d3-a179-ba4f5159a095), references (in, record:7d5a1a0b-8c21-51c1-a53a-dcedbd473ebf)
  Dossier-wide: 128 record(s), 2025-01-01 to 2025-12-31, total 8167298.88 (mean 220737.81), master-data references: 1, co-occurring entities: 111
  Edges: capitalized_to=18, references=110 | none: approved_by, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, sold_to, to_account
Party account:147000
  Roles in this entry: references (in, record:db3d0aee-fcc6-5a5d-b473-010f454c28e6)
  Dossier-wide: 1441 record(s), 2025-01-01 to 2025-12-31, total 9254878.34 (mean 6427.00), master-data references: 1, co-occurring entities: 1442
  Edges: references=1441 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, sold_to, to_account
Party account:330000
  Roles in this entry: references (in, record:aa09ff1e-7d26-517d-a264-348bf8990202)
  Dossier-wide: 2586 record(s), 2025-01-01 to 2025-12-31, total 27024153.36 (mean 10454.22), master-data references: 1, co-occurring entities: 2694
  Edges: references=2586 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, sold_to, to_account
Party account:7708367
  Roles in this entry: to_account (in, record:7d5a1a0b-8c21-51c1-a53a-dcedbd473ebf), to_account (in, record:aa09ff1e-7d26-517d-a264-348bf8990202), to_account (in, record:db3d0aee-fcc6-5a5d-b473-010f454c28e6)
  Dossier-wide: 3 record(s), 2025-03-04 to 2025-03-04, total 0.00 (mean 0.00), master-data references: 0, co-occurring entities: 6
  Edges: to_account=3 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, references, sold_to
Party asset:040000-000192
  Roles in this entry: references (in, record:3e267b5a-4876-59d3-a179-ba4f5159a095), references (in, record:7d5a1a0b-8c21-51c1-a53a-dcedbd473ebf)
  Dossier-wide: 3 record(s), 2025-03-04 to 2025-03-04, total 68000.00 (mean 34000.00), master-data references: 0, co-occurring entities: 3
  Edges: references=3 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, sold_to, to_account
Party user:MV-U03
  Roles in this entry: posted_by (in, record:3e267b5a-4876-59d3-a179-ba4f5159a095), posted_by (in, record:78138d65-0131-5aca-8515-8bfc989fa556), posted_by (in, record:7d5a1a0b-8c21-51c1-a53a-dcedbd473ebf), posted_by (in, record:aa09ff1e-7d26-517d-a264-348bf8990202), posted_by (in, record:db3d0aee-fcc6-5a5d-b473-010f454c28e6)
  Dossier-wide: 2934 record(s), 2025-01-01 to 2025-12-31, total -2171643.84 (mean -741.94), master-data references: 6, co-occurring entities: 1024
  Edges: changed_by=6, posted_by=2927, references=1 | none: approved_by, capitalized_to, counter_account, created_by, document_join, has_receipt, paid_to, processed_by, received_from, sold_to, to_account
Party vendor:200059
  Roles in this entry: paid_to (in, record:78138d65-0131-5aca-8515-8bfc989fa556), references (in, record:aa09ff1e-7d26-517d-a264-348bf8990202)
  Dossier-wide: 73 record(s), 2025-01-01 to 2025-12-28, total -11411.40 (mean -158.49), master-data references: 1, co-occurring entities: 38
  Edges: has_receipt=14, paid_to=32, received_from=8, references=33 | none: approved_by, capitalized_to, chan
```

_Diagnosis: fill in by hand which of agents/PROMPTS.md SS2's observation directions (dates, documents present/absent, roles, amounts, classification, text) should have surfaced this from the excerpt above, and whether the brief actually carried the fact needed._

### F2 - asset 040000-000192 - entry `PG-be8ce9514a6d5150` (0/3 runs proposed anything)

Relevant brief excerpt (Entry/Not present/Parties sections):

```
Entry PG-be8ce9514a6d5150
Record types: asset_record
Subtotals by record type: none
Date span: n/a to n/a
Shape frequency: this combination of record types occurs 197 times of 4902 entries in the dossier

Parties
Party account:040000
  Roles in this entry: references (in, record:9b901e81-91bd-5870-9b57-95540133aa2b)
  Dossier-wide: 128 record(s), 2025-01-01 to 2025-12-31, total 8167298.88 (mean 220737.81), master-data references: 1, co-occurring entities: 111
  Edges: capitalized_to=18, references=110 | none: approved_by, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, sold_to, to_account
Party asset:040000-000192
  Roles in this entry: references (in, record:9b901e81-91bd-5870-9b57-95540133aa2b)
  Dossier-wide: 3 record(s), 2025-03-04 to 2025-03-04, total 68000.00 (mean 34000.00), master-data references: 0, co-occurring entities: 3
  Edges: references=3 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, sold_to, to_account

Not present
No record in this entry supplies a date; 0 of 197 entries with this shape do.
No record in this entry supplies an amount; 0 of 197 entries with this shape do.
No record in this entry supplies a named counterparty; 0 of 197 entries with this shape do.
No record in this entry supplies a source-document reference; 0 of 197 entries with this shape do.
```

_Diagnosis: fill in by hand which of agents/PROMPTS.md SS2's observation directions (dates, documents present/absent, roles, amounts, classification, text) should have surfaced this from the excerpt above, and whether the brief actually carried the fact needed._

### F2 - asset 040000-000194 - entry `PG-2f299a2fe3bd5cf2` (0/3 runs proposed anything)

Relevant brief excerpt (Entry/Not present/Parties sections):

```
Entry PG-2f299a2fe3bd5cf2
Record types: asset_record
Subtotals by record type: none
Date span: n/a to n/a
Shape frequency: this combination of record types occurs 197 times of 4902 entries in the dossier

Parties
Party account:040000
  Roles in this entry: references (in, record:de1a4c0a-7660-5092-a889-e15d9d49e91d)
  Dossier-wide: 128 record(s), 2025-01-01 to 2025-12-31, total 8167298.88 (mean 220737.81), master-data references: 1, co-occurring entities: 111
  Edges: capitalized_to=18, references=110 | none: approved_by, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, sold_to, to_account
Party asset:040000-000194
  Roles in this entry: references (in, record:de1a4c0a-7660-5092-a889-e15d9d49e91d)
  Dossier-wide: 3 record(s), 2025-11-26 to 2025-11-26, total 82000.00 (mean 41000.00), master-data references: 0, co-occurring entities: 3
  Edges: references=3 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, sold_to, to_account

Not present
No record in this entry supplies a date; 0 of 197 entries with this shape do.
No record in this entry supplies an amount; 0 of 197 entries with this shape do.
No record in this entry supplies a named counterparty; 0 of 197 entries with this shape do.
No record in this entry supplies a source-document reference; 0 of 197 entries with this shape do.
```

_Diagnosis: fill in by hand which of agents/PROMPTS.md SS2's observation directions (dates, documents present/absent, roles, amounts, classification, text) should have surfaced this from the excerpt above, and whether the brief actually carried the fact needed._

### F2 - asset 040000-000196 - entry `PG-81e8bcc57ce0558c` (0/3 runs proposed anything)

Relevant brief excerpt (Entry/Not present/Parties sections):

```
Entry PG-81e8bcc57ce0558c
Record types: asset_record
Subtotals by record type: none
Date span: n/a to n/a
Shape frequency: this combination of record types occurs 197 times of 4902 entries in the dossier

Parties
Party account:040000
  Roles in this entry: references (in, record:ba0c9f7f-dc71-5028-9340-26885df9eab9)
  Dossier-wide: 128 record(s), 2025-01-01 to 2025-12-31, total 8167298.88 (mean 220737.81), master-data references: 1, co-occurring entities: 111
  Edges: capitalized_to=18, references=110 | none: approved_by, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, sold_to, to_account
Party asset:040000-000196
  Roles in this entry: references (in, record:ba0c9f7f-dc71-5028-9340-26885df9eab9)
  Dossier-wide: 3 record(s), 2025-11-20 to 2025-11-20, total 39000.00 (mean 19500.00), master-data references: 0, co-occurring entities: 3
  Edges: references=3 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, sold_to, to_account

Not present
No record in this entry supplies a date; 0 of 197 entries with this shape do.
No record in this entry supplies an amount; 0 of 197 entries with this shape do.
No record in this entry supplies a named counterparty; 0 of 197 entries with this shape do.
No record in this entry supplies a source-document reference; 0 of 197 entries with this shape do.
```

_Diagnosis: fill in by hand which of agents/PROMPTS.md SS2's observation directions (dates, documents present/absent, roles, amounts, classification, text) should have surfaced this from the excerpt above, and whether the brief actually carried the fact needed._

### F2 - asset 060000-000193 - entry `PG-427ae1d8573d530c` (0/3 runs proposed anything)

Relevant brief excerpt (Entry/Not present/Parties sections):

```
Entry PG-427ae1d8573d530c
Record types: asset_record
Subtotals by record type: none
Date span: n/a to n/a
Shape frequency: this combination of record types occurs 197 times of 4902 entries in the dossier

Parties
Party account:060000
  Roles in this entry: references (in, record:6312fd98-773e-52a5-8682-2288030d9ab3)
  Dossier-wide: 82 record(s), 2025-01-01 to 2025-12-31, total 3518140.88 (mean 121315.20), master-data references: 1, co-occurring entities: 69
  Edges: capitalized_to=14, references=68 | none: approved_by, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, sold_to, to_account
Party asset:060000-000193
  Roles in this entry: references (in, record:6312fd98-773e-52a5-8682-2288030d9ab3)
  Dossier-wide: 3 record(s), 2025-03-13 to 2025-03-13, total 31000.00 (mean 15500.00), master-data references: 0, co-occurring entities: 3
  Edges: references=3 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, sold_to, to_account

Not present
No record in this entry supplies a date; 0 of 197 entries with this shape do.
No record in this entry supplies an amount; 0 of 197 entries with this shape do.
No record in this entry supplies a named counterparty; 0 of 197 entries with this shape do.
No record in this entry supplies a source-document reference; 0 of 197 entries with this shape do.
```

_Diagnosis: fill in by hand which of agents/PROMPTS.md SS2's observation directions (dates, documents present/absent, roles, amounts, classification, text) should have surfaced this from the excerpt above, and whether the brief actually carried the fact needed._

### F2 - asset 060000-000195 - entry `PG-4969046e14c95ee6` (0/3 runs proposed anything)

Relevant brief excerpt (Entry/Not present/Parties sections):

```
Entry PG-4969046e14c95ee6
Record types: asset_record
Subtotals by record type: none
Date span: n/a to n/a
Shape frequency: this combination of record types occurs 197 times of 4902 entries in the dossier

Parties
Party account:060000
  Roles in this entry: references (in, record:0a326524-12d6-5dd7-94f3-eb713cb924ec)
  Dossier-wide: 82 record(s), 2025-01-01 to 2025-12-31, total 3518140.88 (mean 121315.20), master-data references: 1, co-occurring entities: 69
  Edges: capitalized_to=14, references=68 | none: approved_by, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, sold_to, to_account
Party asset:060000-000195
  Roles in this entry: references (in, record:0a326524-12d6-5dd7-94f3-eb713cb924ec)
  Dossier-wide: 3 record(s), 2025-05-23 to 2025-05-23, total 25600.00 (mean 12800.00), master-data references: 0, co-occurring entities: 3
  Edges: references=3 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, received_from, sold_to, to_account

Not present
No record in this entry supplies a date; 0 of 197 entries with this shape do.
No record in this entry supplies an amount; 0 of 197 entries with this shape do.
No record in this entry supplies a named counterparty; 0 of 197 entries with this shape do.
No record in this entry supplies a source-document reference; 0 of 197 entries with this shape do.
```

_Diagnosis: fill in by hand which of agents/PROMPTS.md SS2's observation directions (dates, documents present/absent, roles, amounts, classification, text) should have surfaced this from the excerpt above, and whether the brief actually carried the fact needed._

### F3 - Jan-2026 invoice, Dec-2025 delivery - entry `PG-394a1a3cecbf5db6` (0/3 runs proposed anything)

Relevant brief excerpt (Entry/Not present/Parties sections):

```
Entry PG-394a1a3cecbf5db6
Record types: invoice
Subtotals by record type: invoice EUR positive=13000.00
Date span: 2026-01-06 to 2026-01-06
Shape frequency: this combination of record types occurs 8 times of 4902 entries in the dossier

Parties
Party vendor:209137
  Roles in this entry: received_from (in, record:8d518dda-630f-53b4-b1ad-c01cc164a1c1)
  Dossier-wide: 3 record(s), 2025-12-20 to 2026-01-06, total 26000.00 (mean 13000.00), master-data references: 1, co-occurring entities: 0
  Edges: received_from=2, references=1 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, sold_to, to_account

Not present
This entry's shape (invoice) occurs 8 time(s) in the dossier. A related shape adding customer_posting, journal_entry occurs 1 time(s).
```

_Diagnosis: fill in by hand which of agents/PROMPTS.md SS2's observation directions (dates, documents present/absent, roles, amounts, classification, text) should have surfaced this from the excerpt above, and whether the brief actually carried the fact needed._

### F3 - Jan-2026 invoice, Dec-2025 delivery - entry `PG-973fb7048d375558` (2/3 runs proposed anything)

Relevant brief excerpt (Entry/Not present/Parties sections):

```
Entry PG-973fb7048d375558
Record types: invoice
Subtotals by record type: invoice EUR positive=14500.00
Date span: 2026-01-13 to 2026-01-13
Shape frequency: this combination of record types occurs 8 times of 4902 entries in the dossier

Parties
Party vendor:209135
  Roles in this entry: received_from (in, record:21e62bb9-be35-5f89-b701-44fb6937eb24)
  Dossier-wide: 3 record(s), 2025-12-19 to 2026-01-13, total 29000.00 (mean 14500.00), master-data references: 1, co-occurring entities: 0
  Edges: received_from=2, references=1 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, sold_to, to_account

Not present
This entry's shape (invoice) occurs 8 time(s) in the dossier. A related shape adding customer_posting, journal_entry occurs 1 time(s).
```

_Diagnosis: fill in by hand which of agents/PROMPTS.md SS2's observation directions (dates, documents present/absent, roles, amounts, classification, text) should have surfaced this from the excerpt above, and whether the brief actually carried the fact needed._

### F3 - Jan-2026 invoice, Dec-2025 delivery - entry `PG-99da448b5cdd5756` (2/3 runs proposed anything)

Relevant brief excerpt (Entry/Not present/Parties sections):

```
Entry PG-99da448b5cdd5756
Record types: invoice
Subtotals by record type: invoice EUR positive=26000.00
Date span: 2026-01-13 to 2026-01-13
Shape frequency: this combination of record types occurs 8 times of 4902 entries in the dossier

Parties
Party vendor:209134
  Roles in this entry: received_from (in, record:65c5c532-8155-5a7a-ba57-b9c944e82be4)
  Dossier-wide: 3 record(s), 2025-12-25 to 2026-01-13, total 52000.00 (mean 26000.00), master-data references: 1, co-occurring entities: 0
  Edges: received_from=2, references=1 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, sold_to, to_account

Not present
This entry's shape (invoice) occurs 8 time(s) in the dossier. A related shape adding customer_posting, journal_entry occurs 1 time(s).
```

_Diagnosis: fill in by hand which of agents/PROMPTS.md SS2's observation directions (dates, documents present/absent, roles, amounts, classification, text) should have surfaced this from the excerpt above, and whether the brief actually carried the fact needed._

### F3 - Jan-2026 invoice, Dec-2025 delivery - entry `PG-b4a07a4a52835383` (2/3 runs proposed anything)

Relevant brief excerpt (Entry/Not present/Parties sections):

```
Entry PG-b4a07a4a52835383
Record types: invoice
Subtotals by record type: invoice EUR positive=17000.00
Date span: 2026-01-11 to 2026-01-11
Shape frequency: this combination of record types occurs 8 times of 4902 entries in the dossier

Parties
Party vendor:209133
  Roles in this entry: received_from (in, record:60e7e4db-9d97-5f24-bdfc-c5d245043853)
  Dossier-wide: 3 record(s), 2025-12-22 to 2026-01-11, total 34000.00 (mean 17000.00), master-data references: 1, co-occurring entities: 0
  Edges: received_from=2, references=1 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, sold_to, to_account

Not present
This entry's shape (invoice) occurs 8 time(s) in the dossier. A related shape adding customer_posting, journal_entry occurs 1 time(s).
```

_Diagnosis: fill in by hand which of agents/PROMPTS.md SS2's observation directions (dates, documents present/absent, roles, amounts, classification, text) should have surfaced this from the excerpt above, and whether the brief actually carried the fact needed._

### F3 - Jan-2026 invoice, Dec-2025 delivery - entry `PG-cf7b803ede4c5820` (2/3 runs proposed anything)

Relevant brief excerpt (Entry/Not present/Parties sections):

```
Entry PG-cf7b803ede4c5820
Record types: invoice
Subtotals by record type: invoice EUR positive=22000.00
Date span: 2026-01-15 to 2026-01-15
Shape frequency: this combination of record types occurs 8 times of 4902 entries in the dossier

Parties
Party vendor:209130
  Roles in this entry: received_from (in, record:d750c9a8-92c3-533f-818e-0679cbea8609)
  Dossier-wide: 3 record(s), 2025-12-21 to 2026-01-15, total 44000.00 (mean 22000.00), master-data references: 1, co-occurring entities: 0
  Edges: received_from=2, references=1 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, sold_to, to_account

Not present
This entry's shape (invoice) occurs 8 time(s) in the dossier. A related shape adding customer_posting, journal_entry occurs 1 time(s).
```

_Diagnosis: fill in by hand which of agents/PROMPTS.md SS2's observation directions (dates, documents present/absent, roles, amounts, classification, text) should have surfaced this from the excerpt above, and whether the brief actually carried the fact needed._

### F3 - Jan-2026 invoice, Dec-2025 delivery - entry `PG-e7b5b9a2ca02564c` (2/3 runs proposed anything)

Relevant brief excerpt (Entry/Not present/Parties sections):

```
Entry PG-e7b5b9a2ca02564c
Record types: invoice
Subtotals by record type: invoice EUR positive=41000.00
Date span: 2026-01-15 to 2026-01-15
Shape frequency: this combination of record types occurs 8 times of 4902 entries in the dossier

Parties
Party vendor:209132
  Roles in this entry: received_from (in, record:d89a17d5-9ccc-52dd-9631-e50205a5b500)
  Dossier-wide: 3 record(s), 2025-12-22 to 2026-01-15, total 82000.00 (mean 41000.00), master-data references: 1, co-occurring entities: 0
  Edges: received_from=2, references=1 | none: approved_by, capitalized_to, changed_by, counter_account, created_by, document_join, has_receipt, paid_to, posted_by, processed_by, sold_to, to_account

Not present
This entry's shape (invoice) occurs 8 time(s) in the dossier. A related shape adding customer_posting, journal_entry occurs 1 time(s).
```

_Diagnosis: fill in by hand which of agents/PROMPTS.md SS2's observation directions (dates, documents present/absent, roles, amounts, classification, text) should have surfaced this from the excerpt above, and whether the brief actually carried the fact needed._
