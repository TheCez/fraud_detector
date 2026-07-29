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

### T1 - Graph foundation

- **Status:** in review
- **Owner:** subagent `graph-engine`
- **Goal:** `backend/app/graph/` - build a `MultiDiGraph` from normalized records, enumerate per-transaction process subgraphs, persist nodes and edges, and expose a bounded typed query API.
- **Files:** `backend/app/graph/` (new), `backend/app/persistence/database.py`, `backend/pyproject.toml`
- **Acceptance:** every edge carries the `record_ids` that justify it; construction is deterministic; no process subgraph holds more than a modest fraction of all records (the hub-node guard); `save`/`load` round-trips losslessly.

### T2 - Graph-traversing analyzer, and Cognee deleted

- **Status:** in review
- **Owner:** subagent `graph-analyzer`
- **Depends on:** T1
- **Goal:** `analysis/graph_analyzer.py` walks one process graph at a time using the T1 tool API, with a red-flag briefing and a hard step budget. Move `EvidenceRecordStore` into the empty `app/evidence/` package - it is Cognee-independent and is the correctness guarantee. Then remove `CogneeCloudGraph`, the cloud payload filter, the `graph_ingestions` flow, and the Cognee settings.
- **Files:** `backend/app/analysis/`, `backend/app/evidence/`, `backend/app/core/settings.py`, `.env.example`, `backend/tests/test_cloud_graph.py`
- **Acceptance:** the model still cannot express evidence text - it cites `record_ids` only, and a proposal is discarded whole if any ID fails to resolve. `GraphUnavailableError` and the `analysis_incomplete` degraded path keep working, now meaning the model or the graph build failed. Cost is bounded per subgraph.
- **What landed:** `app/analysis/prefilter.py` adds a cheap, deterministic, recall-oriented pre-filter (vendor-with-no-receipt, self-approved master-data changes, repair-worded assets, round amounts, near-round-threshold payment clusters, booking/service period mismatches) that narrows the sample dossier's 4,902 process graphs down substantially before any model call - see the PR body for the exact count. `app/analysis/graph_analyzer.py`'s `GraphAnalyzer` walks each selected graph with a bounded LangGraph agent (hard step budget, enforced by the traversal graph's routing) over the T1 tool API, plus a hard per-run model-call cap (`AgentSettings.model_call_cap`) that is logged and recorded - never silently swallowed - when hit. `EvidenceRecordStore` moved to `app/evidence/store.py` unchanged in behavior. `runner.py` now builds and persists the local graph whenever normalization produces records, regardless of analyzer mode. All Cognee code, settings, and the `graph_ingestions` table/flow are deleted; see the PR's grep output.

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

T1 must merge before T2 starts, since T2 consumes the tool API. T3 and T4 can run in parallel after T2 - their file sets are disjoint.

## Invariants for every task

- Preserve the analyzer contract in `backend/app/analysis/interface.py`. Changing it escalates to the orchestrator.
- Never trust model-provided evidence text. Rehydrate from the dossier-scoped `EvidenceRecordStore`.
- Preserve uploaded originals and source provenance. An edge or claim with no backing record is fabricated and unacceptable.
- Add or extend tests in the same change as the behaviour, driven by the real `sample_data` ZIP rather than invented fixtures.
- Never weaken a test to make it pass. A known, stated gap is better than a green suite that proves nothing.
