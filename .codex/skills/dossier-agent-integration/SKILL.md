---
name: dossier-agent-integration
description: Plan or implement work on the local graph engine and the graph-traversing fraud-analysis agent. Use when touching graph construction/persistence (`app/graph/`), the pre-filter or `GraphAnalyzer` (`app/analysis/`), or the evidence store (`app/evidence/`), while preserving evidence-backed findings and existing application contracts.
---

# Graph Engine and Graph-Traversing Agent

Read `PROJECT_CONTEXT.md`, `agents/PLAN.md`, and the affected backend modules first.

Keep `Analyzer` (`app/analysis/interface.py`) as the seam. Do not alter upload, extraction, normalization, evidence, persistence, or dashboard contracts unless the change is required and covered end to end.

## Architecture

- `app/graph/builder.py` builds a `networkx.MultiDiGraph` from normalized records already in SQLite - no LLM, no network calls, fully deterministic.
- `app/graph/subgraphs.py` enumerates per-transaction process graphs (clusters of related records) from that graph.
- `app/graph/store.py` persists both to SQLite and loads them back losslessly.
- `app/graph/tools.py` is the bounded, dossier-scoped, typed query API - the only way anything (the fraud-analysis agent today, a findings chat agent later) is allowed to read the graph. Every function returns plain serializable data and enforces an explicit limit; nothing pulls an entire dossier into a prompt through this API.
- `app/analysis/prefilter.py` is a cheap, deterministic, recall-oriented filter that decides which process graphs are worth a model call, given the graph engine can produce thousands of them per dossier. It must stay generous - narrowing it until only genuinely fraudulent graphs pass would quietly turn it into the rule engine this project's owner explicitly rejected in favor of LLM traversal.
- `app/analysis/graph_analyzer.py`'s `GraphAnalyzer` walks each pre-filter-selected process graph with a bounded LangGraph agent, using only the `app/graph/tools.py` API, under a hard per-graph step budget and a hard per-run model-call cap (`AgentSettings.model_call_cap`).
- `app/evidence/store.py`'s `EvidenceRecordStore` is the authoritative evidence store both `DemoAnalyzer` and `GraphAnalyzer` rehydrate findings from. It is the correctness guarantee: findings are rebuilt from dossier-scoped SQLite records, never from model output.

## Non-negotiable trust boundary

1. Ingest normalized records into local SQLite; `app/graph/builder.py` builds the graph from there.
2. The pre-filter decides *whether to look*, never *whether it is fraud* - judgement stays with the model.
3. `GraphAnalyzer` walks a selected process graph with structured outputs constrained to `ProposedFinding` - a type with **no evidence field**, so the model cannot express evidence text, only cite `record_ids`.
4. `EvidenceRecordStore` rehydrates every cited `record_id` from this dossier's SQLite records. A proposal is discarded **whole** if any cited id fails to resolve - never partially accepted.
5. Persist only findings whose every factual claim maps to rehydrated evidence.
6. Provide an explicit unavailable/degraded state (`GraphUnavailableError` -> `analysis_incomplete`) when credentials, the optional LangGraph/LangChain dependencies, or the graph build are unavailable. Never fabricate evidence, expose secrets, or let model output touch the graph or SQLite directly instead of through `app/graph/tools.py`.
