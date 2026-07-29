# Implementation Plan

Shared coordination artifact. The orchestrator and every subagent read this file before starting and update it when finishing. It is the single source of truth for what is claimed, in flight, and done.

`PROJECT_CONTEXT.md` records durable current state. This file records the work queue. Keep them consistent.

## How agents share this file

- **Claim before working.** Set the task's `Status` to `in progress` and fill `Owner` before touching code. One owner per task.
- **One task per subagent.** A subagent works only inside its task's `Files` list. If it needs a file outside that list, it stops and reports back rather than widening scope.
- **Concurrency rule.** Tasks may run in parallel only when their `Files` lists are disjoint. Overlapping tasks are sequenced by the orchestrator.
- **Handoff contract.** A subagent finishes by reporting: what changed, which tests it ran and their result, and anything it deliberately left out. It does not mark a task `done`.
- **Only the orchestrator marks `done`,** after reviewing the diff and running the full suite (`cd backend && pytest`; `cd frontend && npx vitest run`).
- **Never record secrets here.** No keys, tokens, or `.env` contents - see the Secrets section of `CLAUDE.md`.

Status values: `todo` | `in progress` | `blocked` | `in review` | `done`.

## Complete

- Slices 1-4: product shell, safe ZIP ingestion, normalization and previews, deterministic findings with evidence.
- Slice 5: optional agent and knowledge graph behind the analyzer interface. `DemoAnalyzer` remains the default; `AgentAnalyzer` activates on `FRAUD_AGENT_ENABLED` with Cognee and OpenAI configured. Failures land in `analysis_incomplete` rather than presenting a false report.

## Active queue - Slice 6: agent evaluation and production readiness

Read `.codex/skills/dossier-agent-integration/SKILL.md` before planning or implementing any task below.

### T1 - Evaluate AgentAnalyzer against sealed ground truth

- **Status:** todo
- **Owner:** unassigned
- **Goal:** Measure `AgentAnalyzer` output against `sample_data/UEBUNG_GROUND-TRUTH_SEALED_Muster-Verpackungen.md` and record precision/recall per finding category.
- **Files:** `backend/tests/test_agent_analysis.py`, a new evaluation module under `backend/tests/`
- **Acceptance:** A repeatable evaluation reports per-category hits, misses, and false positives. The sealed ground truth is read only by the evaluation, never fed to the agent. Results written to `PROJECT_CONTEXT.md`.

### T2 - Production configuration validation

- **Status:** todo
- **Owner:** unassigned
- **Goal:** Fail fast and legibly on partial or malformed agent configuration instead of at first cloud call.
- **Files:** `backend/app/core/settings.py`, `backend/tests/test_settings.py`
- **Acceptance:** Enabling the agent without a required credential produces a clear startup error naming the missing variable. No secret value is ever logged or echoed. `.env.example` stays the documented template.

### T3 - Operational validation of the failure path

- **Status:** todo
- **Owner:** unassigned
- **Goal:** Prove the `analysis_incomplete` state and retry endpoint behave correctly when Cognee or OpenAI is unavailable.
- **Files:** `backend/app/analysis/runner.py`, `backend/tests/test_cloud_graph.py`, `backend/tests/test_api.py`
- **Acceptance:** Simulated cloud failure leaves the dossier in `analysis_incomplete` with no partial findings persisted. `POST /api/dossiers/{dossier_id}/analysis` recovers cleanly and is safe to call twice.

### T4 - Surface incomplete analysis in the dashboard

- **Status:** todo
- **Owner:** unassigned
- **Goal:** Make `analysis_incomplete` visible and retryable in the UI instead of looking like an empty result.
- **Files:** `frontend/src/pages/DashboardPage.tsx`, `frontend/src/pages/DashboardPage.test.tsx`, `frontend/src/types/models.ts`
- **Acceptance:** The incomplete state is visually distinct from "no findings", explains itself, and offers retry. Every displayed claim still traces to evidence.

T1 and T2 have disjoint file sets and may run in parallel. T3 overlaps T2 on test collection - sequence T3 after T2. T4 is frontend-only and independent of T1-T3.

## Invariants for every task

- Preserve the analyzer contract in `backend/app/analysis/interface.py`. Changing it escalates to the orchestrator.
- Never trust model-provided evidence text. Rehydrate from the dossier-scoped `EvidenceRecordStore`.
- Preserve uploaded originals and source provenance.
- Add or extend tests in the same change as the behaviour.
