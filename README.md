# fraud_detector

A local-first review tool for German GDPdU/GoBD audit dossiers. Upload one ZIP export, and it inventories the archive, normalizes the ledgers and supporting documents, builds a graph of the business relationships between them, and reports findings that are each traceable to an exact row in an original file.

Everything runs on your machine: SQLite, the local filesystem, and an in-memory graph. Nothing about a dossier leaves the machine unless you explicitly enable the optional model-backed analyzer.

## How it works

```
ZIP  ->  inventory  ->  normalize  ->  graph  ->  analyze  ->  findings + evidence
```

- **Inventory** records every archive entry, including the technical metadata it will not analyze, and rejects unsafe paths, links and resource-exhausting archives. Originals are never modified.
- **Normalize** parses GDPdU delimited tables (using each folder's `index.xml` for column definitions), CSV, XLSX, DOCX and PDF into one record envelope that keeps every raw German field plus exact provenance - file, sheet, page, row.
- **Graph** turns those records into nodes and edges: vendors, customers, accounts, users, assets, and the documents joining them. Records are clustered into per-transaction *process graphs*, each with a start and an end. Every edge stores the record ids that justify it.
- **Analyze** produces findings. The default is deterministic and needs no credentials. Optionally, each process graph - one ledger entry, with its records, parties, relationships and what comparable entries carry that it lacks - is assembled into a single brief and judged by a model in one call. The model is directed at what to compare; no fraud pattern is encoded anywhere.

## Running it

```bash
cd backend && uvicorn app.main:app --reload
cd frontend && npm install && npm run dev
```

Tests: `cd backend && pytest` and `cd frontend && npx vitest run`.

Copy `.env.example` to `.env` for optional agent mode. `.env` is gitignored and holds real credentials - agents working in this repo are blocked from reading it.

## The rule this project is built around

**No claim without evidence.** A finding may only state facts rebuilt from dossier-scoped records in the local database. The analysis model cannot express evidence text at all - it can only cite record ids, and a proposal is discarded whole if any cited id fails to resolve. Graph edges carry their backing records for the same reason.

When something goes wrong - a model is unreachable, a graph fails to build - the dossier lands in an explicit `analysis_incomplete` state and offers a retry. It never presents a partial or empty result as a finished report. An audit tool that quietly under-reports is worse than one that admits it failed.

## Sample data

`sample_data/` holds a synthetic German dossier (~33k records across ledgers, vendors, customers, fixed assets and supporting documents) used by the test suite, plus a sealed ground-truth file describing the fraud patterns seeded into it. The sealed file is reserved for evaluation and is never fed to the analyzer.

## Repository guide

| | |
|---|---|
| `AGENTS.md` | canonical instructions and non-negotiables |
| `CLAUDE.md` | working agreements: secrets, branching, agent roles |
| `PROJECT_CONTEXT.md` | current state and stable seams |
| `agents/PLAN.md` | the shared work queue |
| `agents/PROJECT_SPEC.md` | the immutable initial-milestone specification |
| `.codex/skills/` | domain workflows for dossier engineering |
| `agents/PROMPTS.md` | every prompt in the pipeline, and the doctrine behind them |
| `.claude/skills/` | Claude Code workflows for this repo |

The `cognee` branch preserves an earlier implementation that used a cloud graph service. It is kept for reference and is not merged into `main`.
