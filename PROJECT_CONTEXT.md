# Project Context

## Current state

Slices 1-5 are implemented. The application safely processes one ZIP dossier end to end: inventory, normalization, SQLite persistence, evidence-backed findings, source previews, and background analysis.

Analysis defaults to the deterministic `DemoAnalyzer`. When `FRAUD_AGENT_ENABLED` is enabled and Cognee Cloud plus OpenAI credentials are configured, it instead ingests the dossier's `normalized/all_records.jsonl` into a dossier-scoped Cognee dataset and runs the LangGraph-backed `AgentAnalyzer`. Agent proposals are rehydrated from dossier-scoped SQLite records before findings are persisted, so model-provided evidence text is never trusted. Cloud or optional-dependency failures leave the dossier in the explicit `analysis_incomplete` state instead of presenting a false report; analysis can be retried through `POST /api/dossiers/{dossier_id}/analysis`.

## Stable seams

- `backend/app/analysis/interface.py` defines the analyzer contract.
- `runtime/dossiers/<id>/normalized/all_records.jsonl` is the graph-ingestion source.
- Normalized records retain stable IDs and source provenance.
- `backend/app/analysis/graph.py` keeps Cognee retrieval separate from the authoritative local `EvidenceRecordStore`.

## Next

Evaluate the optional agent against the sealed sample-dossier ground truth and complete production configuration and operational validation. See `agents/PLAN.md` and `.codex/skills/dossier-agent-integration/`.

## Commands

- Backend: `cd backend && uvicorn app.main:app --reload`
- Frontend: `cd frontend && npm run dev`
- Tests: `cd backend && pytest`; `cd frontend && npx vitest run`
- Agent mode: set `FRAUD_AGENT_ENABLED=true`, `COGNEE_API_KEY`, `COGNEE_SERVICE_URL`, and `OPENAI_API_KEY`; optionally set `OPENAI_MODEL` (default: `gpt-5.4`).
