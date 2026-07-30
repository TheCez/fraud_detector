# Implementation Plan

Shared coordination artifact. The orchestrator and every subagent read this file before starting and update it when finishing. It is the single source of truth for what is claimed, in flight, and done.

`PROJECT_CONTEXT.md` records durable current state. This file records the work queue. Keep them consistent.

## How agents share this file

- **Claim before working.** Set the task's `Status` to `in progress` and fill `Owner` before touching code. One owner per task.
- **One task per subagent, one worktree, one PR.** A subagent works only inside its task's `Files` list and only inside its own worktree. If it needs a file outside that list, it stops and reports back rather than widening scope.
- **Concurrency rule.** Tasks may run in parallel only when their `Files` lists are disjoint. Overlapping tasks are sequenced by the orchestrator.
- **Handoff contract.** A subagent finishes by reporting: what changed, which tests it ran and their result, and anything it deliberately left out. It does not mark a task `done`.
- **Only the orchestrator marks `done`,** after reviewing the diff and running the full suite (`cd backend && pytest`; `cd frontend && npx vitest run`). Only the human merges.
- **Never record secrets here.** No keys, tokens, or credential values - see the Secrets section of `CLAUDE.md`.
- **A green suite in a worktree is not proof.** `core/settings.py` derives `PROJECT_ROOT` from its own path, so a worktree has no local environment file and always takes the deterministic path. Verify environment-sensitive behaviour in the main checkout.

Status values: `todo` | `in progress` | `blocked` | `in review` | `done`.

## Architectural decision: Cognee is dropped

Cognee Cloud could not ingest the dossier's file mix, and ingesting the normalized set cost too much. It was also the wrong shape: it returned candidate record IDs scraped out of a serialized response, when what this project needs is a traversable graph of business relationships.

Replacement, in progress below: nodes and edges persisted in **SQLite** alongside the existing tables, **NetworkX** in memory for DAG algorithms, and a **bounded typed tool API** shared by both the fraud-analysis agent and a future findings chat agent. Not Kuzu, not Cypher - one storage engine, and bounded typed tools rather than model-authored queries, consistent with this project's rule that model output is never trusted.

The `cognee` branch is a preservation snapshot of the Cognee-based implementation. Do not build on it or merge it into `main`.

## Complete

- Slices 1-4: product shell, safe ZIP ingestion, normalization and previews, deterministic findings with evidence.
- Slice 5: optional agent and knowledge graph behind the analyzer interface. Superseded by the decision above; `DemoAnalyzer` remains the default and the behavioural baseline.
- Phase A - normalization correctness, the prerequisite for any graph:
  - Backend test suite isolated from the developer's local environment file. It previously inherited live credentials at import time, and 5 API tests failed on any machine with real settings.
  - Credential guard narrowed so it no longer blocks prose that merely describes the protected file.
  - XLSX header-row detection: every sample workbook has a title banner above the real header, which had been destroying the segregation-of-duties rights matrix and the profit reconciliation.
  - GDPdU technical metadata (`index.xml`, `.dtd`) now classified *and* excluded from analysis at one decision point, while remaining readable as a schema input. Folder-level parse failures surface as errors instead of looking like successfully parsed empty tables.
  - Missing graph entities extracted: assets (`ANLAGENNUMMER`), the real asset-posting date column (`WERTSTELLUNG`), and composite account keys decomposed into account plus vendor/customer/asset.

## Active queue - Slice 6: local graph engine

### T1 - Graph foundation - **done** (PR #7)

`backend/app/graph/` builds a `MultiDiGraph` from normalized records, enumerates per-transaction process graphs, persists them, and exposes a bounded typed query API. Every edge carries the `record_ids` that justify it, construction is deterministic, and `save`/`load` round-trips losslessly.

The design risk was hub collapse: high-degree accounts and users would glue the dossier into one component, making "walk each graph one by one" meaningless. Process graphs are therefore clustered by `document_join` rather than raw connected components, with a fan-out cap that excludes document ids referenced by too many records - on the real data that caught `AfA` and `AB-2024`, batch markers sitting in the `BELEGNUMMER` column. Result: 4,902 process graphs, largest holding 12 of 32,821 records. A guard test fails if any single subgraph exceeds 5% of all records.

### T2 - Graph-traversing analyzer, Cognee deleted, and made fast - **done** (PR #8)

`app/analysis/prefilter.py` selects which process graphs are worth a model call; `app/analysis/graph_analyzer.py` walks them with bounded LangGraph agents over the T1 tool API. `EvidenceRecordStore` moved to `app/evidence/store.py`. All Cognee code, settings, tests and the `graph_ingestions` flow are gone; `is_configured` is now `agent_enabled and openai_api_key`.

Four things worth remembering, because each was a real defect rather than a design choice:

- **The cap was a lottery.** Taking `candidates[:cap]` from a list ordered by uuid5 meant an effectively random third was analyzed. Candidates are now ranked by signal specificity so the cap truncates the weakest. Only ~44 of ~1,650 candidates carry a strong signal, so unranked selection could drop a vendor paid with no goods receipt in favour of a graph whose only distinction was a round number.
- **Splitting detection was semantically wrong** - it fired on any two smallish amounts. It now requires a same-day cluster whose combined total crosses the threshold.
- **`save_graph` only upserted**, so rebuilding a smaller graph left orphaned rows and a re-analysis could serve phantom nodes. Found only by requiring a test for the "never serve a stale graph" claim rather than accepting it.
- **Every `tools.py` call reloaded the whole graph** (~3.4s), costing ~22s per candidate traversal before any model latency - and since NetworkX construction is GIL-bound, concurrency alone could not have fixed it.

Performance: archive to findings went from 64.1s to ~23s on the deterministic path, per-candidate graph loading from 22.4s to 0.04s, and traversal now runs on a bounded worker pool with findings sorted deterministically. The test suite went from 899s to ~333s.

The model-call cap was deliberately **not** tuned to where the known findings rank. That would repeat the mistake of the deleted cloud payload filter, whose thresholds had been quietly fitted to the answer key.

### T3 - Graph API seams for chat and UI

- **Status:** todo
- **Owner:** unassigned
- **Depends on:** T1, T2
- **Goal:** `GET /api/dossiers/{id}/graphs` and `GET /api/dossiers/{id}/graphs/{graph_id}`, plus an optional `graph_id` on `Finding` linking a finding to the subgraph that produced it. Endpoints only - no UI work.
- **Files:** `backend/app/api/routes.py`, `backend/app/models/schemas.py`, `frontend/src/types/models.ts`
- **Acceptance:** subgraphs serialize to JSON suitable for rendering; every response stays dossier-scoped and bounded.

### T4 - Ground-truth evaluation

- **Status:** todo
- **Owner:** unassigned
- **Depends on:** T2
- **Goal:** measure the analyzer against the sealed sample-dossier ground truth and report precision and recall per category.
- **Files:** a new evaluation module under `backend/tests/`
- **Acceptance:** the four seeded findings are reported and **none** of the seven decoys is accused - the mid-year vendor that *does* have four-eyes approval and real deliveries is the sharpest discriminator. The sealed file is read only by the evaluator and never reaches the model. Because the analyzer traverses with an LLM, results vary run to run: report across several runs, not one.

T1 and T2 are merged. T3 and T4 can run in parallel - their file sets are disjoint.

**T4 will be the first work to spend a real model call.** Nothing has exercised a live call yet, so `OPENAI_MODEL` (default `gpt-5.4`) is unverified, and per-graph latency is unmeasured - which means the model-call cap has never been chosen against a real run time. Expect to tune it once T4 produces the first honest number.

## Invariants for every task

- Preserve the analyzer contract in `backend/app/analysis/interface.py`. Changing it escalates to the orchestrator.
- Never trust model-provided evidence text. Rehydrate from the dossier-scoped `EvidenceRecordStore`.
- Preserve uploaded originals and source provenance. An edge or claim with no backing record is fabricated and unacceptable.
- Add or extend tests in the same change as the behaviour, driven by the real `sample_data` ZIP rather than invented fixtures.
- Never weaken a test to make it pass. A known, stated gap is better than a green suite that proves nothing.
