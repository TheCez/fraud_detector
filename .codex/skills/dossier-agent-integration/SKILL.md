---
name: dossier-agent-integration
description: Plan or implement work on the local graph engine and the per-entry analysis pipeline. Use when touching graph construction or persistence (`app/graph/`), the profile, entry brief, analyst or pipeline stages (`app/analysis/`), or the evidence store (`app/evidence/`), while preserving evidence-backed findings and existing application contracts.
---

# Graph engine and the per-entry analysis pipeline

Read `PROJECT_CONTEXT.md` and `agents/PLAN.md` first, then only the modules your task names.

Keep `Analyzer` (`app/analysis/interface.py`) as the seam. Do not alter upload, extraction,
normalization, evidence, persistence or dashboard contracts unless the change is required and covered
end to end.

## The shape, and the reason for it

**One process graph is one ledger entry. The graph assembles that entry's complete context before the
model is called; a model never walks it.** An earlier design gave the analyst six traversal tools and a
6-call step budget, so it spent its attention navigating and frequently judged an entry before reaching
its full context. Assembly is the system's job.

- `app/graph/builder.py` builds a `networkx.MultiDiGraph` from normalized records already in SQLite -
  no LLM, no network, fully deterministic.
- `app/graph/subgraphs.py` enumerates process graphs, clustered by `document_join` rather than raw
  connected components. High-degree entity nodes would otherwise glue the dossier into one component.
- `app/graph/store.py` persists and reloads both losslessly, deleting before inserting so a rebuild can
  never serve a stale graph.
- `app/graph/tools.py` is the bounded, dossier-scoped, typed query API. The analyzer no longer uses it;
  it is the sanctioned read path for the graph endpoints and a future chat agent. Every function
  enforces an explicit limit.
- `app/analysis/profile.py` computes a `DossierProfile` in one pass over records and one over edges.
  Counts only, all learned from the dossier in front of it - per-entity counts including a **zero** count
  per edge type, per-shape statistics, per-entry completeness facts, amount quantiles. This is what
  replaced the deleted rule engine: "this vendor participates in 0 `has_receipt` edges" is a measured
  fact about the dossier, not a pattern someone authored.
- `app/analysis/entry_brief.py` renders one entry as bounded **text**, not nested JSON. Sections: the
  entry, a chronological timeline, every record with fields and provenance, the parties with their
  profiles and their own master-data records, the relationships, what peer entries of the shape carry
  that this one lacks, and the number and date conventions.
- `app/analysis/analyst.py` makes exactly one structured-output call per entry. No tools bound.
- `app/analysis/pipeline.py` orchestrates: build the profile once, order entries, then per entry render,
  call, rehydrate, validate. It carries the worker pool, per-entry failure isolation, the model-call cap,
  and deterministic finding ids and ordering.
- `app/evidence/store.py` is the authoritative evidence store every analyzer rehydrates from.

## Non-negotiable trust boundary

1. Normalized records go into local SQLite; `builder.py` builds the graph from there.
2. The analyst receives one assembled brief and answers once. Structured output is constrained to
   `ProposedFinding` - a type with **no evidence field**, so the model cannot express evidence text at
   all, only cite `record_ids`.
3. `EvidenceRecordStore` rehydrates every cited id from this dossier's records. A proposal is discarded
   **whole** if any cited id fails to resolve - never partially accepted.
4. Persist only findings whose every factual claim maps to rehydrated evidence.
5. Provide an explicit degraded state (`GraphUnavailableError` -> `analysis_incomplete`) when
   credentials, an optional dependency, or the graph build are unavailable. Never fabricate evidence,
   expose secrets, or let model output reach the graph or SQLite directly.

## Two rules that are easy to break

**No fraud scenario in code or in a prompt.** No keyword lists, no amount thresholds, no named
patterns, no worked examples. `tests/fraud_scenario_guard.py` enforces this with an AST walk plus a
prompt-text check, and it has its own tests. A check about *data quality* is exempt - it asserts nothing
about wrongdoing - but it may only route an entry **toward** analysis, never away from it.

**Nothing narrows silently.** A model-call cap, a gate rejection, a refuted finding: each is recorded on
the analysis run with counts. Entry ordering may reorder but never exclude.

## Before claiming a change helped

Offline tests prove properties, not usefulness. Use the `entry-brief` skill to read what the model
actually receives, and the `live-eval` skill to measure whether it finds anything. A hypothesis about
model behaviour is worthless until the brief has been read - one missed finding was blamed on the model
for two rounds before turning out to be a parser typing bug.
