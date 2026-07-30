# Implementation Plan

The shared work queue. Every agent reads this before starting and updates its task when finishing.
`PROJECT_CONTEXT.md` holds durable current state; keep the two consistent.

Status values: `todo` | `in progress` | `blocked` | `in review` | `done`.

## How agents share this file

- **Claim before working.** Set `Status: in progress` and fill `Owner` before touching code.
- **One task, one worktree, one branch, one PR.** A subagent works only inside its task's `Files`
  list. If it needs a file outside that list it stops and reports back rather than widening scope.
- **Concurrency.** Tasks may run in parallel only when their `Files` lists are disjoint.
- **Handoff.** A subagent reports what changed, which tests it ran and their exact result, and what
  it deliberately left out. It does not mark a task `done`.
- **Only the orchestrator marks `done`,** after reading the diff and running the full suite
  (`cd backend && pytest`; `cd frontend && npx vitest run`). Only the human merges.
- **Never record secrets here.**
- **A green suite in a worktree is not proof.** `core/settings.py` derives `PROJECT_ROOT` from its own
  path, so a worktree has no local environment file and always takes the deterministic path.

## Invariants for every task

- Preserve the analyzer contract in `backend/app/analysis/interface.py`. Changing it escalates to the
  orchestrator.
- Never trust model-provided evidence text. Rehydrate from `EvidenceRecordStore`.
- Preserve uploaded originals and source provenance. A claim with no backing record is fabricated.
- **No fraud scenario may be encoded in code or in a prompt** - no keyword lists, no amount
  thresholds, no named patterns, no worked examples. Prompts direct attention; the model judges. All
  prompts live in `agents/PROMPTS.md`; changing one is an orchestrator decision. Checks about *data
  quality* are exempt, because they assert nothing about wrongdoing - but a data-quality rule may only
  route an entry **toward** analysis, never away from it.
- **Every stage that drops something records what it dropped and why.** Silent narrowing presents a
  partial analysis as a complete one.
- Add or extend tests in the same change as the behaviour, driven by the real `sample_data` ZIP.
- Never weaken a test to make it pass. A stated gap beats a green suite that proves nothing.

## Architectural decisions in force

**Cognee is dropped.** It could not ingest the file mix, cost too much, and returned scraped record
ids rather than a traversable graph. Replaced by SQLite for nodes and edges, NetworkX in memory, and
bounded typed tools instead of model-authored queries. The `cognee` branch is a preservation snapshot
- do not build on it or merge it.

**The graph assembles context; a model never walks it.** Its purpose is to resolve the real
relationships between records so that *all* data belonging to one ledger entry can be handed to the
analyst up front. The earlier design gave the model six traversal tools and a 6-call step budget, so
it spent its attention navigating and often judged an entry before reaching its full context.
`app/graph/tools.py` survives as the query seam for the graph endpoints and a future chat agent.

**No encoded fraud scenarios.** The deleted `prefilter.py` and red-flag briefing named six specific
scenarios, which made the system accurate on one sample dossier and blind to anything nobody had
enumerated. Prompts name the observables and the comparisons to make, never the conclusion.

## Done

- **Slices 1-5** - product shell, safe ZIP ingestion, normalization and previews, deterministic
  findings with evidence, and the analyzer seam. `DemoAnalyzer` remains the default and the
  behavioural baseline.
- **Normalization correctness** - test suite isolated from the developer's environment file; XLSX
  header-row detection (every sample workbook has a title banner above the real header); GDPdU
  `index.xml`/DTD classified as schema inputs and excluded from analysis at one decision point;
  assets, the real asset-posting date column, and composite account keys extracted.
- **T1 graph foundation** (PR #7) - `app/graph/`. Process graphs are clustered by `document_join`
  rather than raw connected components, with a fan-out cap excluding document ids referenced by too
  many records; on the real data that caught the `AfA` and `AB-2024` batch markers. 4,902 process
  graphs, largest holding 12 of 32,821 records. A guard test fails if any subgraph exceeds 5% of all
  records.
- **T2 graph-traversing analyzer** (PR #8) - superseded by T6, but its correctness work carries
  forward: `EvidenceRecordStore` moved to `app/evidence/store.py`, all Cognee code and the
  `graph_ingestions` flow deleted, `save_graph` made to delete-then-insert so a rebuild cannot serve a
  stale graph, and `tools.py` given an in-memory graph rather than reloading it per call (~3.4s each).
- **T5 entry context** (PRs #11, #15) - `profile.py` and `entry_brief.py`. Every real entry renders
  whole: a test asserts that across ~60 entries spanning the size range, every member record and every
  party appears and no truncation marker does. Truncation remains only as a safety valve.
- **T6 single-call analyst** (PR #12) - `analyst.py` and `pipeline.py` replace `graph_analyzer.py`;
  `prefilter.py` and the `langgraph` dependency are deleted. Per-stage model tiers added.
- **T4 ground-truth evaluation** (PR #13) - `tests/test_ground_truth_eval.py`, gated behind
  `FRAUD_EVAL_LIVE`. See the `live-eval` skill.
- **Prompt calibration** (PR #14) - §2 rewritten to name the comparisons to make rather than gesture
  at them, plus a standard of evidence: a finding must name two facts that do not fit together, and
  rarity alone is not a finding. Halved the false-positive rate on ordinary entries.
- **KONTO subledger typing** (PR #15) - `KONTO` on a row whose `ART` names a subledger now resolves to
  `vendor`/`customer` instead of a phantom `account`. Before this, every master-data change in
  `Stammdatenaenderungen_2025.csv` was unreachable from the party it describes.

## Queue

### T9 - Re-measure on current `main`

- **Status:** todo
- **Goal:** run the live evaluation now that the prompt, brief and parser fixes are all merged - the
  first configuration in which the shell-vendor case could be found at all. Record the numbers in
  `PROJECT_CONTEXT.md`.
- **Files:** `PROJECT_CONTEXT.md`
- **Acceptance:** ~231 calls, no code change. If precision moved, say by how much and in which group.

### T10 - Narrow the evaluation's F3 case mapping

- **Status:** todo
- **Goal:** F3 maps by source file, so all 8 January-2026 invoices score as the seeded finding when
  only one is. That inflated the old prompt's apparent recall and made a genuine improvement look like
  a regression.
- **Files:** `backend/tests/test_ground_truth_eval.py`
- **Acceptance:** the F3 group contains only the seeded entry; the rest move to the control group or
  are dropped, with the choice stated in the module.

### T11 - Close F1's split evidence

- **Status:** todo
- **Goal:** a party's master-data records currently surface only in the entry holding that party's own
  master row (PR #15 narrowed it there to stay inside the brief's budget). So when the analyst judges
  one of the shell vendor's payments, it still cannot see who created the vendor. Inline the
  changer/approver-bearing master records into every entry that party appears in, bounded to parties
  with few such records.
- **Files:** `backend/app/analysis/profile.py`, `backend/app/analysis/entry_brief.py`, and their tests
- **Acceptance:** the brief for a *payment* entry of vendor 209101 contains `GEAENDERT_VON` and
  `GENEHMIGT_VON`, and `test_no_real_entry_in_the_sample_dossier_is_truncated_or_incomplete` still
  passes unchanged.

### T7 - Stage 1 gate: data-quality triage

- **Status:** todo
- **Depends on:** T11
- **Goal:** `app/analysis/gate.py` decides whether an entry is complete enough to judge, so a
  defective export is not surfaced as suspicion. Verdicts: `analyze`, `insufficient_data`,
  `out_of_scope` (balances, carryforwards, period aggregates, depreciation markers). Deterministic
  metrics decide the clear cases at zero model cost; only borderline entries get one cheap-model call
  on the ~400-token summary `entry_brief.render_entry_summary` already produces. Prompt:
  `agents/PROMPTS.md` §1.

  **The absence taxonomy is this task's core.** A missing goods receipt is simultaneously incomplete
  data *and* the finding itself, so a gate that reads absence as grounds to discard would delete the
  entries most worth reading:

  - **Missing identity** - nothing dates the entry, no amount, no counterparty, no document
    reference. Unjudgeable. -> `insufficient_data`.
  - **Missing expected companion** - the entry is fully identified but a document that bookkeeping
    grammar or this dossier's own peers say accompanies it is absent. -> **forced `analyze`**, with the
    absence passed on as a stated fact.
  - **Missing optional detail** - a field peer entries commonly lack too. -> neutral.

  So `insufficient_data` requires failing the identity check **and** having no expected-companion
  absence. Evaluated **per entry, not per record**: a journal row with no counterparty is fine when
  the invoice it is document-joined to names one.

  Expected companions come from peer-shape statistics first (learned, so it generalizes to any
  dossier), plus a small table of universal bookkeeping structure for the blind spot when a dossier is
  systematically deficient or has too few peers - stated only as "record type X is normally
  accompanied by record type Y or edge type Z". **That table may only upgrade a verdict to `analyze`,
  never downgrade one.** That asymmetry is why it is not the deleted prefilter returning: the prefilter
  selected and dropped, bounding what could ever be found; this can only prevent dropping.
- **Files:** `backend/app/analysis/gate.py`, `backend/tests/test_gate.py` (new),
  `backend/app/analysis/pipeline.py`, `backend/app/core/settings.py`,
  `backend/app/persistence/database.py`, `.env.example`
- **Acceptance:** a well-identified entry missing an expected companion is always routed to `analyze`,
  and no input lets the grammar table downgrade a verdict. The gate errs open - an entry the cheap
  model answers garbage for is analysed. A seeded-random sample of gated-out entries is analysed
  anyway each run and reported, so a systematically wrong gate is visible. Per-verdict counts are
  recorded and `insufficient_data` entries persisted with reasons - an entry with no document
  reference is a GoBD observation in its own right, not a silent drop.

### T8 - Stage 3 verify, consolidate, surface

- **Status:** todo
- **Depends on:** T7, and T3 (shared files)
- **Goal:** `app/analysis/verifier.py` re-checks each proposed finding independently with the strongest
  tier, prompted to *refute*, given the finding's claims plus records re-read from
  `EvidenceRecordStore` - never the analyst's rendering. Today's boundary only proves a cited record
  *exists*; this checks whether the claim about it is true. Then `app/analysis/consolidate.py` makes
  one call over survivors' summaries to dedup and cluster for the dashboard. Prompts:
  `agents/PROMPTS.md` §3 and §4.
- **Files:** `backend/app/analysis/verifier.py`, `backend/app/analysis/consolidate.py`, their tests,
  `backend/app/analysis/pipeline.py`, `backend/app/models/schemas.py`,
  `backend/app/persistence/database.py`, `backend/app/api/routes.py`, `frontend/src/types/models.ts`,
  plus the dashboard component
- **Acceptance:** one call per finding, never more than one finding's claims at a time, input records
  from the store rather than analyst text. A refuted finding is dropped but recorded with the reason.
  Consolidation sees only survivor summaries. The dashboard shows verified findings and, separately,
  the entries that could not be judged and why.

### T3 - Graph API seams for chat and UI

- **Status:** todo
- **Goal:** `GET /api/dossiers/{id}/graphs` and `.../graphs/{graph_id}`, plus the existing optional
  `graph_id` on `Finding`. Endpoints only, no UI work.
- **Files:** `backend/app/api/routes.py`, `backend/app/models/schemas.py`,
  `frontend/src/types/models.ts`
- **Acceptance:** subgraphs serialize to JSON suitable for rendering; every response stays
  dossier-scoped and bounded. Land before T8, which touches the same two shared files.

Sequencing: T9 and T10 are independent and cheap. T11 -> T7 -> T8, since each wires into
`pipeline.py`. T3 is independent but must precede T8. Re-run the live evaluation after T7 and after T8
- it is the only thing that shows whether a stage helped.
