---
name: sample-dossier
description: Facts about the sample GDPdU dossier - its file inventory, the identifiers tests assert against, and how to measure end-to-end pipeline timings. Use when writing tests against sample_data, briefing a subagent that needs known identifiers, or profiling ingest/normalize/graph/analysis performance.
---

# The sample dossier

`sample_data/Uebungsdaten_Muster_Verpackungen.zip` is a synthetic German audit export used by the
test suite. Drive tests from this real archive rather than invented fixtures - most bugs found on
this project were things fixtures would have hidden.

## Shape

Four GDPdU accounting folders - `Sachkonten`, `Kreditoren`, `Debitoren`, `AV` (plus an empty
`Steuercodes`) - each holding an `index.xml` defining table columns, a shared DTD, and
semicolon-delimited `.txt` tables in cp1252 with no header row. German dates (`DD.MM.YYYY`) and
comma decimals. Plus `Begleitdokumente/` with 9 CSV, 6 XLSX, 1 DOCX and 3 PDF.

`index.xml` and the DTD are **schema inputs**: read by the loader for column names, never emitted
as records.

Scale: ~32,800 normalized records → ~42,000 nodes, ~110,000 edges, ~4,900 process graphs.

Two traps this data contains, both of which broke earlier code:

- Every XLSX has a title banner above the real header row - row 1 is never the header.
- `BELEGNUMMER` sometimes holds a batch marker (`AfA`, `AB-2024`) rather than a document number,
  and `ANLAGENNUMMER` on depreciation rows holds a bare asset *group* code rather than an asset.

## Identifiers for tests

Reproduced here so briefs and tests never need the sealed ground-truth file, which is reserved for
evaluation and must not reach the analyzer. All verified directly against the archive.

| what | identifiers |
|---|---|
| shell vendor - 5 round invoices + 5 payments, **no** goods receipt; its master-data row has changer == approver == `MV-U05` | `209101` |
| honest counterpart - also new mid-year, but approved by a different user and **has** real goods receipts | `209112` |
| split payments - four on `14.10.2025` just under €10,000, document `SAMMEL-200007`, composite account `330000-200007` | `200007` |
| repair-worded assets, all with asset-level postings | `040000-000191`, `040000-000192`, `040000-000194`, `040000-000196`, `060000-000193`, `060000-000195` |
| asset *group* codes on depreciation rows - must become `account` nodes, not dangling assets | `021000`, `040000`, `060000`, `062000` |

Assert both directions wherever an absence is the signal: that `209101` has no receipt edge **and**
that `209112` does. Checking only the absence proves nothing - it passes just as well when the
matching logic is broken entirely.

## Measuring pipeline timings

Run from `backend/`. Machine variance is significant, so compare before/after in one sitting rather
than against a number recorded earlier.

```python
import zipfile, tempfile, time
from pathlib import Path
from app.ingestion.manifest import build_manifest
from app.normalization.orchestrator import normalize_dossier
from app.graph.builder import build_graph
from app.graph.subgraphs import build_process_graphs
from app.graph.store import save_graph
from app.analysis.prefilter import select_candidate_graphs
from app.analysis.demo_analyzer import DemoAnalyzer

T0 = time.perf_counter()
tmp = Path(tempfile.mkdtemp())
with zipfile.ZipFile("../sample_data/Uebungsdaten_Muster_Verpackungen.zip") as zf:
    zf.extractall(tmp)
root = [p for p in tmp.iterdir() if p.is_dir()][0]
manifest = build_manifest(root, "d1")
ws = tmp / "wk" / "dossiers" / "d1"
ws.mkdir(parents=True)

t = time.perf_counter(); normalize_dossier(root, ws, manifest, "d1"); norm = time.perf_counter() - t
db = ws.parent.parent / "registry.db"
t = time.perf_counter()
graph = build_graph("d1", db)
process_graphs = build_process_graphs("d1", graph)
save_graph(db, "d1", graph, process_graphs)
gr = time.perf_counter() - t
t = time.perf_counter()
candidates = select_candidate_graphs("d1", db, graph=graph, process_graphs=process_graphs)
pf = time.perf_counter() - t
t = time.perf_counter(); findings = DemoAnalyzer().analyze("d1", db); dm = time.perf_counter() - t

print(f"normalize={norm:.1f} graph={gr:.1f} prefilter={pf:.1f} analysis={dm:.1f} "
      f"total={time.perf_counter() - T0:.1f}s")
print(f"graphs={len(process_graphs)} candidates={len(candidates)} findings={len(findings)}")
```

Pass `graph=` and `process_graphs=` to the pre-filter as above. Omitting them makes it reload the
whole graph from SQLite, which measures the wrong thing.

Reference, deterministic path: about 23s total - normalize ~7s, graph ~12s, pre-filter ~2s,
analysis ~2s.

## Test fixtures

`backend/tests/conftest.py` provides session-scoped fixtures that extract, normalize, build and
persist this dossier **once** for the whole suite. Use them. Building your own module-scoped
pipeline fixture adds ~26s per test file - that mistake once took the suite from 217s to 899s.
