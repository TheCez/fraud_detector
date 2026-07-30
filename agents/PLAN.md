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
- **Note:** land this before T8, which also touches `schemas.py` and the frontend types.
- **Goal:** `GET /api/dossiers/{id}/graphs` and `GET /api/dossiers/{id}/graphs/{graph_id}`, plus an optional `graph_id` on `Finding` linking a finding to the subgraph that produced it. Endpoints only - no UI work.
- **Files:** `backend/app/api/routes.py`, `backend/app/models/schemas.py`, `frontend/src/types/models.ts`
- **Acceptance:** subgraphs serialize to JSON suitable for rendering; every response stays dossier-scoped and bounded.

### T4 - Ground-truth evaluation

- **Status:** todo
- **Owner:** unassigned
- **Depends on:** T6 (was T2; the pipeline below replaces what it would have measured)
- **Goal:** measure the pipeline against the sealed sample-dossier ground truth and report precision and recall per category, **attributing every miss to the stage that caused it**.
- **Files:** a new evaluation module under `backend/tests/`
- **Acceptance:** the four seeded findings are reported and **none** of the seven decoys is accused - the mid-year vendor that *does* have four-eyes approval and real deliveries is the sharpest discriminator. The sealed file is read only by the evaluator and never reaches any model. Results vary run to run: report across several runs, not one. A seeded finding lost at the gate (T7), missed by the analyst (T6), and refuted by the verifier (T8) are three different failures and must be reported separately - a single recall number hides which stage to fix.

**T4 will be the first work to spend a real model call.** Nothing has exercised a live call yet, so `OPENAI_MODEL` (default `gpt-5.4`) is unverified and per-entry latency is unmeasured. Expect to tune worker counts and model tiers once T4 produces the first honest number.

## Active queue - Slice 7: per-entry analysis pipeline

### Architectural decision: no encoded fraud scenarios, and the agent does not traverse

Two corrections to what T2 landed, both from the project owner. They supersede T2's design without undoing its correctness work.

**1. The graph exists to assemble context, not to be walked by a model.** Its purpose is to resolve the real relationships between records so that *all* data belonging to one ledger entry can be gathered and handed to the analyst up front. Giving the model traversal tools spends its attention on navigation instead of judgement, and a hard step budget (T2 used 6 calls) means it frequently rendered a verdict without having reached the entry's full context. Traversal is the system's job, done before the model call. `app/graph/tools.py` stays as the bounded query seam for T3's endpoints and the future chat agent - only the analyzer stops using it.

**2. Fraud patterns must not be encoded anywhere - not in code, not in the prompt.** `prefilter.py`'s six signals and `graph_analyzer.py`'s red-flag briefing enumerate specific scenarios (self-approval, round amounts, vendor without goods receipt, period cut-off, capitalized repairs, threshold splitting). That makes the system accurate on one sample dossier and blind everywhere else: it can only re-find frauds someone already wrote down, and it primes the model to confirm the named pattern instead of judging the entry. Real dossiers will contain scenarios nobody has enumerated yet. Prompts therefore direct attention - *observe the billing dates and the payment dates, observe the receipts, observe who appears in which role* - and never name a conclusion.

The distinction that makes the pipeline below legitimate: **a check about data quality is not a check about fraud.** "This record has no date, no amount, and no counterparty" is objectively decidable and asserts nothing about wrongdoing, so it may be deterministic. "This looks like split payments under an approval limit" asserts a crime, so it may not.

Measured on the real sample dossier (32,821 records, 4,902 process graphs): a process graph already *is* one ledger entry - median 8 records, max 12, e.g. `invoice + journal_entry + customer_posting + goods_dispatch` (1,920 of them). Rendering just an entry's own records as JSON is ~2.4k tokens at p50, 3.9k at max, so one call per entry over the whole dossier is ~10M input tokens - affordable, which is why the cost-driven prefilter has no reason to exist. But 1-hop expansion of an entry's entity nodes touches a median of **13,505** edges, so an entry's surroundings can never be enumerated - they must be aggregated. That aggregation is what replaces the rules.

### T5 - Ledger-entry context: dossier profile and entry brief

- **Status:** in progress
- **Owner:** subagent `entry-context`
- **Goal:** assemble everything about one ledger entry into a single bounded document a model can analyse in one call. No model calls anywhere in this task.
  - `app/analysis/profile.py` - one pass over records and graph producing a `DossierProfile`: per entity node (record count, date range, total/mean amount, count per edge type it participates in, how many master-data records name it, distinct counterparties and roles); per entry shape, meaning the sorted record-type tuple (occurrence count, and which record types and edge types entries of that shape usually carry); dossier-wide amount quantiles, period coverage and record-type counts. Counts only, all learned from the dossier in front of it.
  - `app/analysis/entry_brief.py` - renders one process graph as compact **text**, not nested JSON: `Entry` (ids, types present, totals by sign and currency, date span, how often this shape occurs); `Timeline` (every dated fact in one chronological list, labelled with its source column, so date-ordering needs no arithmetic from the model); `Records` (one block each - record_id, type, source file and sheet/row, fields as `GERMAN_COLUMN: value`, plus `text_content` for document records); `Parties` (one block per entity: the roles it plays *here*, then its dossier profile); `Relationships` (the entry's real edges in words, each with the record_ids justifying it); `Not present` (record types and edge types that peer entries of the nearest shape carry and this one lacks, stated with the peer count as an observation); `Conventions` (comma decimals, DD.MM.YYYY, column glossary - data-format facts only).
- **Files:** `backend/app/analysis/profile.py`, `backend/app/analysis/entry_brief.py` (both new), `backend/tests/test_profile.py`, `backend/tests/test_entry_brief.py` (both new)
- **Acceptance:** the profile is built in one pass over records and edges and is deterministic; the brief has a hard char budget per section with explicit truncation markers and stays under ~6k tokens for the dossier's largest entry (12 records, 13 entities) - assert this against the real `sample_data` ZIP, not a fixture; every line in a brief is traceable to a record field or a computed count, and a test proves no section can emit text not derived from either; `Not present` is computed from peer-shape statistics only - a test fails if any German keyword list, amount threshold, or named scenario appears in either module.

### T6 - Stage 2 analyst: one call per entry, traversal and prefilter deleted

- **Status:** todo
- **Owner:** unassigned
- **Depends on:** T5
- **Goal:** replace `graph_analyzer.py` with `app/analysis/analyst.py` (one structured-output call per entry over T5's brief) plus `app/analysis/pipeline.py` (the stage orchestrator implementing the analyzer contract). Delete `_build_tools`, `_build_traversal_graph`, `_TraversalState`, `DEFAULT_STEP_BUDGET`, the red-flag briefing, `prefilter.py`, and the `langgraph` dependency - nothing else in the backend imports it. The new system prompt is directional and names no scenario. Per-stage model tiers replace the single `OPENAI_MODEL`.
- **Files:** `backend/app/analysis/analyst.py`, `backend/app/analysis/pipeline.py` (new), `backend/app/analysis/graph_analyzer.py`, `backend/app/analysis/prefilter.py` (both deleted), `backend/app/analysis/runner.py`, `backend/app/core/settings.py`, `backend/pyproject.toml`, `.env.example`, `backend/tests/test_analyst.py`, `backend/tests/test_pipeline.py` (new), `backend/tests/test_graph_analyzer.py`, `backend/tests/test_prefilter.py` (both deleted), `backend/tests/test_runner.py`, `backend/tests/test_settings.py`
- **Acceptance:** exactly one model call per analysed entry, with no tools bound and no `langgraph` import anywhere in the backend; a test fails if the prompt or module contains an enumerated fraud scenario, keyword list or amount threshold. Every T2 correctness property survives unchanged and keeps its test: `ProposedFinding` has no evidence field, evidence is rehydrated from `EvidenceRecordStore`, a proposal citing any unresolvable record_id is discarded whole, finding ids stay deterministic uuid5 over sorted record_ids, findings sort by finding_id so concurrent and sequential runs agree, per-entry failures stay isolated while authentication/configuration errors raise `GraphUnavailableError`, and the `analysis_incomplete` degraded path still works. Achieved coverage (entries total vs analysed) is recorded on the analysis run, never silently dropped. `app/graph/tools.py` is untouched.

### T7 - Stage 1 gate: data-quality triage

- **Status:** todo
- **Owner:** unassigned
- **Depends on:** T6
- **Goal:** `app/analysis/gate.py` decides, per entry, whether the data is complete enough to judge - so that incomplete exports are not surfaced as suspicion. Three verdicts: `analyze`, `insufficient_data`, `out_of_scope` (balances, carryforwards, `AfA`-style depreciation markers). Deterministic completeness metrics from T5's profile decide the clear cases at zero model cost; only borderline entries get one cheap-model call on a ≤400-token summary (shape, field-completeness counts, party stats) - never the full brief, whose tokens would cost what the analysis costs.
- **Files:** `backend/app/analysis/gate.py`, `backend/tests/test_gate.py` (new), `backend/app/analysis/pipeline.py`, `backend/app/core/settings.py`, `backend/app/persistence/database.py`, `.env.example`
- **Acceptance:** the gate errs open - any doubt routes to `analyze`, and a test proves an entry the cheap model returns garbage for is analysed rather than dropped. A seeded-random sample of gated-out entries is analysed anyway on every run and reported, so a systematically wrong gate is visible instead of invisible; the seed is fixed so the sample is reproducible. Per-verdict counts are recorded on the analysis run and `insufficient_data` entries are persisted with their reasons - they are a GoBD compliance observation in their own right, not a silent drop. The gate never reasons about fraud: a test fails if any scenario, keyword list or amount threshold appears in the module or its prompt.

### T8 - Stage 3 verify, consolidate, and surface

- **Status:** todo
- **Owner:** unassigned
- **Depends on:** T6, and T3 (shared files)
- **Goal:** `app/analysis/verifier.py` re-checks each proposed finding independently with the strongest model tier, prompted to *refute* rather than confirm, and given the finding's claims plus the records re-fetched fresh from `EvidenceRecordStore` - never the analyst's rendering of them. Today's boundary only proves a cited record *exists*; this checks whether the claim made about it is true. Then `app/analysis/consolidate.py` makes one call over the survivors' summaries only, to dedup and cluster for the dashboard (many entries sharing a vendor become one narrative). Surface both the verified findings and the gate's data-quality observations.
- **Files:** `backend/app/analysis/verifier.py`, `backend/app/analysis/consolidate.py`, `backend/tests/test_verifier.py`, `backend/tests/test_consolidate.py` (new), `backend/app/analysis/pipeline.py`, `backend/app/models/schemas.py`, `backend/app/persistence/database.py`, `backend/app/api/routes.py`, `frontend/src/types/models.ts`, plus the dashboard component the data-quality section lands in
- **Acceptance:** verification is per finding and parallel, one call each - a test proves the verifier is never handed more than one finding's claims at a time, and that its input records come from the store rather than from analyst-produced text. A refuted finding is dropped but recorded with the refutation reason, so precision improvements are auditable rather than invisible. Consolidation receives only survivor summaries, never full briefs. The dashboard shows verified findings and, separately, the entries that could not be judged and why.

Sequencing: T5 → T6 → T7 → T8, since each later stage wires into `pipeline.py`. T3 is independent of T5-T7 but must land before T8. T4 runs once T6 is merged and is re-run after T7 and T8 - it is the only thing that can tell you whether removing the encoded scenarios cost recall.

## Invariants for every task

- Preserve the analyzer contract in `backend/app/analysis/interface.py`. Changing it escalates to the orchestrator.
- Never trust model-provided evidence text. Rehydrate from the dossier-scoped `EvidenceRecordStore`.
- Preserve uploaded originals and source provenance. An edge or claim with no backing record is fabricated and unacceptable.
- Add or extend tests in the same change as the behaviour, driven by the real `sample_data` ZIP rather than invented fixtures.
- Never weaken a test to make it pass. A known, stated gap is better than a green suite that proves nothing.
- **No fraud scenario may be encoded in code or in a prompt** - no keyword lists, no amount thresholds, no named patterns, no worked examples of fraud. Prompts direct attention; the model supplies the judgement. Checks about *data quality* are exempt, because they assert nothing about wrongdoing.
- **Every stage that drops something records what it dropped and why.** A gate rejection, a model-call cap, a refuted finding: each is reported on the analysis run. Silent narrowing presents a partial analysis as a complete one.
