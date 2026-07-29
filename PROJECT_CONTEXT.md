# Project Context

## Current state

Slices 1-6 are implemented. The application safely processes one ZIP dossier end to end: inventory, normalization, SQLite persistence, a local graph engine, evidence-backed findings, source previews, and background analysis.

Whenever normalization produces records - regardless of analyzer mode - `runner.py` builds a `networkx.MultiDiGraph` from them (`app/graph/builder.py`), enumerates per-transaction process graphs (`app/graph/subgraphs.py`), and persists both to SQLite (`app/graph/store.py`). This backs both today's agent analyzer and the upcoming graph-rendering UI and chat agent.

Analysis defaults to the deterministic `DemoAnalyzer`. When `FRAUD_AGENT_ENABLED` is enabled and `OPENAI_API_KEY` is configured, it instead runs `GraphAnalyzer`: a cheap, deterministic, recall-oriented pre-filter (`app/analysis/prefilter.py`) selects which process graphs are worth a model call - the graph engine can produce thousands per dossier, so calling the model on every one is the same cost wall that killed the previous Cognee integration - and a bounded LangGraph agent walks each selected graph one at a time via the typed tool API in `app/graph/tools.py`, under a hard per-graph step budget and a hard per-run model-call cap (`AgentSettings.model_call_cap`). Agent proposals are rehydrated from dossier-scoped SQLite records via `app/evidence/store.py`'s `EvidenceRecordStore` before findings are persisted, so model-provided evidence text is never trusted and a proposal citing any unresolvable record id is discarded whole. Enabling the agent without a configured `OPENAI_API_KEY` raises `GraphUnavailableError` rather than silently falling back to the demo analyzer. Model or graph-build failures leave the dossier in the explicit `analysis_incomplete` state instead of presenting a false report; analysis can be retried through `POST /api/dossiers/{dossier_id}/analysis`. Hitting the model-call cap does not fail the run, but is logged and recorded on the analysis run rather than silently presenting a partial result as complete.

## Stable seams

- `backend/app/analysis/interface.py` defines the analyzer contract.
- `backend/app/graph/tools.py` is the bounded, dossier-scoped, typed query API shared by the fraud-analysis agent and a future findings chat agent - the only sanctioned way to read the graph.
- Normalized records retain stable IDs and source provenance.
- `backend/app/evidence/store.py`'s `EvidenceRecordStore` is the authoritative local evidence store every analyzer rehydrates findings from.

## Next

Expose the graph over the API for chat/UI consumption (`GET /api/dossiers/{id}/graphs`, `.../graphs/{graph_id}`), then evaluate the agent against the sealed sample-dossier ground truth. See `agents/PLAN.md` and `.codex/skills/dossier-agent-integration/`.

## Commands

- Backend: `cd backend && uvicorn app.main:app --reload`
- Frontend: `cd frontend && npm run dev`
- Tests: `cd backend && pytest`; `cd frontend && npx vitest run`
- Agent mode: set `FRAUD_AGENT_ENABLED=true` and `OPENAI_API_KEY`; optionally set `OPENAI_MODEL` (default: `gpt-5.4`) and `FRAUD_AGENT_MODEL_CALL_CAP` (default: 500).
