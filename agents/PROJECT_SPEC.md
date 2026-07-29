# Audit Dossier Demo — Initial Requirements and Planning Specification

## 1. Product objective
Create a convincing local demo for an auditor reviewing a company dossier. The user uploads a ZIP file containing German accounting exports and supporting documents. The application safely extracts the archive, inventories and normalizes the contents, and displays a dashboard containing files, demo findings, reasoning, and traceable evidence.

This milestone validates:
- upload experience;
- dossier preprocessing architecture;
- normalized evidence model;
- dashboard information design;
- future integration point for a fraud-analysis agent.

It does not yet validate LLM quality or autonomous investigation.

## 2. Sample dossier characteristics
The supplied sample archive contains a top-level dossier directory with approximately 42 entries and about 6 MB of uncompressed content.

Important folders:
- `Sachkonten` — general-ledger accounts and postings
- `Kreditoren` — vendor master data and vendor postings
- `Debitoren` — customer master data and customer postings
- `AV` — fixed assets and asset postings
- `Begleitdokumente` — supporting CSV, XLSX, PDF, and DOCX files
- `Steuercodes` — tax-code area, which may be empty

Expected file types:
- GDPdU-style semicolon-delimited `.txt`
- `.csv`
- `.xlsx`
- `.docx`
- `.pdf`
- `index.xml`
- `gdpdu-01-08-2002.dtd`

The XML and DTD files primarily describe export structure. They may be useful for parsing and validation, but should normally be classified as technical metadata rather than displayed as primary audit evidence.

## 3. User journeys

### 3.1 Upload page
Route: `/`

The page must provide:
- clear product title and one-sentence purpose;
- drag-and-drop area and file picker;
- ZIP-only validation;
- selected filename and size;
- upload/start-analysis button;
- progress steps:
  1. Uploading
  2. Validating archive
  3. Extracting files
  4. Building inventory
  5. Normalizing documents
  6. Preparing demo findings
- friendly error states;
- transition to the dashboard after successful processing.

For the demo, one dossier is processed at a time.

### 3.2 Outcome dashboard
Route: `/dossiers/:dossierId`

The dashboard must include:

#### Header
- dossier name;
- processing status;
- number of source files;
- number of normalized records;
- number of demo findings;
- button to return to upload.

#### Findings overview
Cards or rows with:
- finding title;
- severity;
- category;
- amount at risk when applicable;
- concise explanation;
- evidence count;
- confidence label;
- status such as `Demo`, `Needs review`, or `Supported`.

Use four seeded demo findings matching the sample domain:
1. Potential shell vendor / unsupported consulting payments
2. Repairs potentially capitalized as assets
3. December costs potentially posted in January without accrual
4. Payments potentially split below an approval threshold

The demo may use fixture outputs, but each fixture must point to real normalized source records where feasible. Clearly label non-computed conclusions as demo output.

#### Finding detail panel
Selecting a finding must show:
- plain-language reasoning;
- evidence chain in chronological or logical order;
- exact values used in the reasoning;
- source file for every evidence item;
- row, sheet, page, or paragraph reference;
- original German text and optional English explanation;
- links/actions to open the source preview.

#### File explorer
Show a searchable and filterable inventory grouped by folder/type:
- source filename and relative path;
- type;
- size;
- classification (`evidence`, `supporting`, `technical_metadata`);
- parse status;
- normalized record count;
- preview action.

#### Source preview
Support a practical demo preview:
- tables for TXT/CSV/XLSX;
- extracted text for DOCX/PDF;
- technical summary for XML/DTD;
- highlighted record or passage when opened from evidence.

## 4. Processing pipeline

### 4.1 Upload and dossier workspace
For each upload create a generated dossier ID and workspace:

```text
runtime/dossiers/<dossier_id>/
  original/upload.zip
  extracted/
  normalized/
  previews/
  manifest.json
  dossier.db
```

The runtime directory must not be committed.

### 4.2 Safe ZIP extraction
Required controls:
- reject non-ZIP input;
- reject absolute paths and `..` traversal;
- reject symlinks and special files;
- configurable maximum compressed size;
- configurable maximum uncompressed size;
- configurable maximum entry count;
- configurable compression-ratio threshold;
- never execute archive contents;
- preserve relative paths and Unicode filenames.

Return a useful error rather than a stack trace.

### 4.3 Manifest
Create one manifest record for every archive entry.

Minimum fields:
```json
{
  "file_id": "stable-id",
  "relative_path": "Kreditoren/Lieferantenbuchungen.txt",
  "original_name": "Lieferantenbuchungen.txt",
  "extension": ".txt",
  "mime_type": "text/plain",
  "size_bytes": 301166,
  "sha256": "...",
  "classification": "evidence",
  "parse_status": "parsed",
  "parser": "gdpdu_delimited_v1",
  "normalized_record_count": 1234,
  "excluded_from_analysis": false,
  "exclusion_reason": null
}
```

### 4.4 File classification
Default rules:
- accounting TXT/CSV/XLSX: `evidence`
- PDF/DOCX business documents: `supporting`
- `index.xml` and GDPdU DTD: `technical_metadata`
- unknown supported documents: `supporting`
- unsupported or unsafe files: recorded, not parsed, and marked with a reason

Do not physically delete technical metadata in the initial milestone. Exclude it from analysis by default but retain it for reproducibility.

### 4.5 Normalization strategy
The goal is a common representation that both deterministic checks and future agents can understand.

Produce two complementary outputs:
1. **Canonical JSONL records** for evidence and future agent consumption.
2. **SQLite tables/indexes** for filtering, joining, and UI queries.

Never flatten everything into one giant schema. Use a shared envelope plus type-specific payload.

Canonical envelope:
```json
{
  "record_id": "stable-id",
  "dossier_id": "...",
  "document_id": "file-id",
  "record_type": "vendor_posting",
  "source": {
    "relative_path": "Kreditoren/Lieferantenbuchungen.txt",
    "sheet": null,
    "page": null,
    "row_start": 42,
    "row_end": 42,
    "paragraph": null
  },
  "original": {},
  "normalized": {},
  "search_text": "..."
}
```

Normalization rules:
- preserve every original field and value;
- add normalized English field names without destroying German names;
- normalize dates to ISO 8601 where confidently parseable;
- normalize decimal comma values into numeric decimals while preserving the raw string;
- detect delimiter and encoding safely;
- store currency separately;
- preserve document, vendor, customer, account, asset, and user identifiers as strings;
- do not translate names, IDs, account numbers, or legal terms destructively;
- create concise bilingual search text where useful.

### 4.6 Parser expectations

#### GDPdU TXT
- use `index.xml` definitions when practical;
- otherwise detect semicolon-delimited quoted records;
- support German dates and decimal commas;
- retain source row numbers.

#### CSV
- detect delimiter/encoding;
- preserve row numbers and column names;
- support German-formatted numbers and dates.

#### XLSX
- list sheets;
- extract tables cell-by-cell;
- retain sheet name, row, and column references;
- do not evaluate macros or external links.

#### DOCX
- extract paragraphs and tables;
- retain paragraph/table indexes;
- do not execute embedded objects.

#### PDF
- extract text per page with PyMuPDF;
- retain page numbers;
- no OCR in the first milestone unless explicitly requested.

#### XML/DTD
- parse only for safe structural metadata;
- never resolve external entities or network resources;
- classify as technical metadata.

## 5. Evidence model
An evidence object is the bridge between a finding and a source.

Required fields:
```json
{
  "evidence_id": "...",
  "finding_id": "...",
  "record_id": "...",
  "document_id": "...",
  "label": "Vendor created and approved by the same user",
  "excerpt": "GEAENDERT_VON=MV-U05; GENEHMIGT_VON=MV-U05",
  "source_location": {
    "relative_path": "Begleitdokumente/Stammdatenaenderungen_2025.csv",
    "sheet": null,
    "page": null,
    "row_start": 12,
    "row_end": 12,
    "columns": ["GEAENDERT_VON", "GENEHMIGT_VON"]
  },
  "original_language": "de",
  "explanation_en": "The same user created and approved the vendor."
}
```

Evidence requirements:
- exact source path;
- exact source location;
- concise excerpt or selected fields;
- no unsupported paraphrase;
- stable IDs so UI links survive reprocessing when input is unchanged.

## 6. Analysis interface
Define a stable backend interface now, even though the first implementation is a demo.

Conceptual contract:
```python
class Analyzer(Protocol):
    def analyze(self, dossier: NormalizedDossier) -> AnalysisResult: ...
```

Implement:
- `DemoAnalyzer`: deterministic fixture/rule-backed findings for the sample dossier;
- no external API calls;
- no prompt orchestration;
- no vector database.

Future implementations can include an agent without changing upload, evidence, or dashboard contracts.

## 7. API requirements
Suggested endpoints:

```text
POST   /api/dossiers
GET    /api/dossiers/{id}
GET    /api/dossiers/{id}/status
GET    /api/dossiers/{id}/files
GET    /api/dossiers/{id}/files/{file_id}
GET    /api/dossiers/{id}/files/{file_id}/preview
GET    /api/dossiers/{id}/findings
GET    /api/dossiers/{id}/findings/{finding_id}
```

For the demo, upload processing may run synchronously if it remains responsive for the sample archive. The API should still expose explicit processing states so background execution can be introduced later.

Use typed request/response models and consistent error envelopes.

## 8. Frontend design direction
Use Tailwind to create a restrained professional audit interface.

Guidelines:
- desktop-first but responsive;
- neutral palette with restrained severity accents;
- high information density without clutter;
- readable tables and sticky headers;
- no decorative gradients or excessive animations;
- clear loading, empty, and error states;
- keyboard-accessible controls and visible focus states;
- badges for severity, file type, and parse status;
- evidence should look clickable and traceable.

Suggested dashboard layout:
- top summary bar;
- left column or upper section for findings;
- right/detail panel for reasoning and evidence;
- lower section or side drawer for file explorer and previews.

## 9. Demo finding fixtures
The initial dashboard should demonstrate these categories without pretending an LLM discovered them.

### F1 — Potential shell vendor
- Vendor `209101`, Ratio Consulting GmbH
- Five consulting invoices and five payments totalling €248,000
- Same user creates and approves vendor
- Conflicting create/post/pay permissions
- Missing independent service evidence

### F2 — Repairs capitalized
- Six repair-like asset records
- Net amount €150,800
- Posted to fixed-asset accounts rather than repair expense account 670000

### F3 — Cut-off issue
- Eight January 2026 invoices with December 2025 service dates
- Matching December goods receipts marked invoice open
- Missing 2025 accrual
- Net amount €192,000

### F4 — Split payments
- Vendor `200007`
- Four payments on 14.10.2025
- Each just below €10,000
- Combined €39,040
- Approval threshold documented as €10,000

The UI should also be capable of showing a finding as dismissed/clean later, but decoy analysis is not required for the first screen implementation.

## 10. Suggested repository structure

```text
/
  AGENTS.md
  CLAUDE.md
  PROJECT_SPEC.md
  PROJECT_CONTEXT.md
  frontend/
    src/
      api/
      components/
      features/upload/
      features/dashboard/
      pages/
      types/
  backend/
    app/
      api/
      core/
      ingestion/
      normalization/
      evidence/
      analysis/
      persistence/
      models/
    tests/
  runtime/                 # gitignored
  sample-data/             # optional, gitignored unless sanitized
```

## 11. Initial vertical slices

### Slice 1 — Static product shell
- Scaffold frontend and backend.
- Create upload page and dashboard page with mocked API data.
- Establish shared visual components and routes.
- No file processing yet.

Acceptance:
- both pages render;
- dashboard looks credible with four demo findings;
- frontend tests cover basic rendering.

### Slice 2 — Real ZIP upload and safe extraction
- Implement upload endpoint and safe extractor.
- Create dossier workspace and manifest.
- Connect upload progress states to the UI.

Acceptance:
- supplied sample ZIP uploads and extracts successfully;
- malicious traversal archives are rejected by tests;
- dashboard can show the real file inventory.

### Slice 3 — Normalization and previews
- Parse TXT/CSV/XLSX/DOCX/PDF.
- Write JSONL and SQLite records.
- Add source previews with locations.

Acceptance:
- all supported files receive a parse status;
- normalized records preserve provenance;
- dashboard previews representative files.

### Slice 4 — Demo findings linked to evidence
- Implement `DemoAnalyzer`.
- Resolve fixture findings to normalized records.
- Render finding details and evidence links.

Acceptance:
- every displayed factual claim has at least one clickable evidence item;
- clicking evidence opens the correct file and location;
- unsupported fixture evidence is visibly marked rather than fabricated.

## 12. Out of scope for the initial milestone
- real autonomous agent or LLM calls;
- production fraud conclusions;
- multi-user accounts;
- cloud deployment;
- long-term object storage;
- OCR at scale;
- email notifications;
- background queues unless sample processing proves too slow;
- vector databases;
- organization-level permissions;
- editing source documents;
- automatic deletion of source or metadata files.

## 13. Quality bar
The demo is complete when:
- the sample ZIP can be uploaded safely;
- the archive is inventoried and normalized;
- original evidence remains untouched;
- the dashboard shows files and four polished demo findings;
- each evidence link reaches an exact source location;
- errors are understandable;
- core parser, extractor, and API behaviors are tested;
- the architecture can replace `DemoAnalyzer` with a real agent later.

## 14. Planning-mode instruction
Before implementation, produce a plan based on the vertical slices above. The plan must:
- identify the smallest end-to-end slice;
- list files/modules to create or change;
- state API and data contracts before coding;
- call out security and provenance risks;
- avoid planning agent/LLM work for the current milestone;
- include testable acceptance criteria.

Do not start broad refactors or build every parser at once. Complete one vertical slice and validate it before expanding.


# PROJECT_CONTEXT.md

## Purpose
Compact dynamic project state. Coding agents update this file after each completed task so future prompts need minimal repository inspection.

## Current phase
Planning and boilerplate design for a two-page local demo. No implementation has been completed yet.

## Current decisions
- Frontend: React, TypeScript, Vite, Tailwind CSS.
- Backend: Python 3.12+, FastAPI, Pydantic.
- Processing: pandas, openpyxl, python-docx, PyMuPDF.
- Persistence: local filesystem plus SQLite.
- Initial analysis: deterministic `DemoAnalyzer`; no LLM or autonomous agent.
- Originals are immutable; derived normalized data is stored separately.
- Canonical normalized output is JSONL plus SQLite.
- XML/DTD files are retained and classified as technical metadata rather than deleted.
- Work proceeds in vertical slices.

## Sample dossier observed
- ZIP contains roughly 42 entries and about 6 MB uncompressed.
- Main folders: `Sachkonten`, `Kreditoren`, `Debitoren`, `AV`, `Begleitdokumente`, `Steuercodes`.
- Formats: TXT, CSV, XLSX, DOCX, PDF, XML, DTD.
- Largest file is `Sachkonten/Sachkontobuchungen.txt` at roughly 4.6 MB.

## Next recommended task
Implement Slice 1 from `PROJECT_SPEC.md`: scaffold frontend/backend and build the static upload and outcome dashboard using mocked API responses.

## Open issues
- Confirm package manager for frontend if the repository does not already choose one.
- Confirm whether the user wants a single-process developer command or separate frontend/backend commands.

## Maintenance rules
After each completed task:
1. Replace stale current-state notes rather than appending a diary.
2. Keep only durable decisions, completed capabilities, unresolved issues, and the next step.
3. Remove command transcripts, failed attempts, and resolved debugging details.
4. Keep this file concise enough to load on every prompt.
5. Do not modify `CLAUDE.md`, `AGENTS.md`, or immutable requirements in `PROJECT_SPEC.md` unless the user explicitly changes project-level requirements.
