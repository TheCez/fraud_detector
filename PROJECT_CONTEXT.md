# Project Context

## Current state

The application processes one ZIP dossier end to end: inventory, normalization, SQLite persistence, a local graph, evidence-backed findings, source previews, and background analysis.

`runner.py` builds a `networkx.MultiDiGraph` from the normalized records (`app/graph/builder.py`), enumerates per-transaction **process graphs** (`app/graph/subgraphs.py`), and persists both (`app/graph/store.py`) whenever normalization produced records - regardless of analyzer mode.

Analysis defaults to the deterministic `DemoAnalyzer`. With `FRAUD_AGENT_ENABLED=true` and an `OPENAI_API_KEY`, `app/analysis/pipeline.py`'s `AnalysisPipeline` runs instead:

1. `app/analysis/profile.py` computes a `DossierProfile` in one pass over records and one over edges - per-entity counts (including a zero count per edge type), per-shape statistics, per-entry completeness facts, dossier-wide amount quantiles.
2. `app/analysis/entry_brief.py` renders one process graph as a bounded **text** brief: the entry, a chronological timeline, every record with its fields and provenance, the parties with their dossier-wide profiles and their own master-data records, the relationships, and what peer entries of the same shape carry that this one lacks.
3. `app/analysis/analyst.py` makes **exactly one** structured-output model call per entry over that brief. No tools are bound; there is no traversal and no step budget.
4. Proposals are rehydrated through `app/evidence/store.py` before becoming findings.

One process graph is one ledger entry - median 8 records on the sample dossier. The graph exists to assemble that entry's full context *before* the model is called, not to be walked by it. No fraud scenario is encoded anywhere in code or in a prompt; the prompts live in `agents/PROMPTS.md` and a test guard enforces this.

Entry order for the per-run model-call cap comes from `pipeline.rank_entries_for_analysis` (amount percentile, shape rarity, absence count). It orders and never excludes. A cap hit is recorded on the analysis run with achieved coverage.

Enabling the agent without credentials raises `GraphUnavailableError`. Model or graph-build failures leave the dossier in `analysis_incomplete` rather than presenting a false report; retry via `POST /api/dossiers/{dossier_id}/analysis`.

## Stable seams

- `backend/app/analysis/interface.py` - the analyzer contract. `AnalysisPipeline` implements it.
- `backend/app/graph/tools.py` - the bounded, dossier-scoped, typed query API. The analyzer no longer uses it; it remains the sanctioned read path for the coming graph endpoints and a future chat agent.
- `backend/app/evidence/store.py` - the authoritative evidence store every analyzer rehydrates from.
- Normalized records retain stable IDs and source provenance.

## Measured, on the real sample dossier

32,821 records -> 41,997 nodes, ~110k edges, 4,902 process graphs (median 8 records, max 12).

| | |
|---|---|
| archive to findings, deterministic path | ~23s (normalize 6.6s, graph build and persist 12.0s) |
| profile build | ~4s |
| one entry brief | ~7ms, median ~14.6k chars (~3.7k tokens), max ~21k |
| one call per entry, whole dossier | ~15M input tokens |
| full backend suite | ~6 min, 172 tests |

Live analyst accuracy against the sealed ground truth (`gpt-5.4`, 77 cases x 3 runs - see the `live-eval` skill): repair capitalization and the period cut-off are found for the right reasons. Ordinary control entries are flagged 8% of the time and decoys 48%. Precision is the open problem; T7 and T8 exist to address it.

## Next

See `agents/PLAN.md`. In order: re-measure on current `main`, narrow the evaluation's F3 case mapping, close F1's split evidence, then the gate (T7), the verifier (T8), and the graph endpoints (T3).

## Commands

- Backend: `cd backend && uvicorn app.main:app --reload`
- Frontend: `cd frontend && npm run dev`
- Tests: `cd backend && pytest`; `cd frontend && npx vitest run`
- Agent mode: `FRAUD_AGENT_ENABLED=true` plus `OPENAI_API_KEY`. Optional: `OPENAI_MODEL` (default `gpt-5.4`), the per-stage overrides `FRAUD_AGENT_ANALYST_MODEL` / `FRAUD_AGENT_GATE_MODEL` / `FRAUD_AGENT_VERIFIER_MODEL`, plus `FRAUD_AGENT_MODEL_CALL_CAP` (500) and `FRAUD_AGENT_MAX_WORKERS` (12).
- Live evaluation: spends real money. Read the `live-eval` skill first.
