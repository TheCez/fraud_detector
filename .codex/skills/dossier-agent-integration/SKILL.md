---
name: dossier-agent-integration
description: Plan or implement Slice 5 of the audit-dossier application: Cognee graph ingestion and an OpenAI-backed fraud-analysis agent. Use when replacing DemoAnalyzer while preserving evidence-backed findings and existing application contracts.
---

# Agent Integration

Read `PROJECT_CONTEXT.md`, `agents/PLAN.md`, and the affected backend modules. Use official OpenAI and Cognee documentation for current APIs.

Keep `Analyzer` as the seam. Do not alter upload, extraction, normalization, evidence, persistence, or dashboard contracts unless the change is required and covered end to end.

1. Ingest `all_records.jsonl`; map `record_type`, `entities`, relationships, and provenance into Cognee.
2. Add a graph-query tool layer that returns provenance-preserving records, never unsupported summaries.
3. Implement `AgentAnalyzer` with structured outputs constrained to the existing finding and evidence schemas.
4. Persist only findings whose every factual claim maps to retrieved evidence.
5. Compare results with the deterministic sample findings using a repeatable evaluation fixture.

Use configuration for credentials and model selection. Provide an explicit unavailable/degraded state when credentials or the graph service are absent. Never fabricate evidence, expose secrets, or let model output access arbitrary filesystem paths.
