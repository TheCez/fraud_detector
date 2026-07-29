---
name: dossier-engineering
description: Implement or change audit-dossier ingestion, normalization, evidence, API, or dashboard vertical slices. Use for work on the local-first German GDPdU/GoBD review application that must preserve immutable evidence and source provenance.
---

# Audit Dossier Engineering

Read `PROJECT_CONTEXT.md` and `agents/PLAN.md`, then inspect only the affected slice.

- Deliver one usable vertical path through UI, API, processing, persistence, and tests where applicable.
- Preserve original uploaded files. Record every archive entry, including excluded technical metadata.
- Reject unsafe archive paths, links, special files, and resource-exhausting archives. Never execute or trust document content.
- Retain a stable record ID and exact original path plus sheet, page, row, or paragraph provenance on normalized records.
- Keep the shared envelope with type-specific payloads. Preserve raw German fields while adding only confident normalizations.
- Require exact evidence objects for every displayed factual claim, including source location and excerpt.
- Keep the four module boundaries explicit: ingestion, normalization, analysis, presentation.
- Run focused tests and relevant linting. Replace stale notes in `PROJECT_CONTEXT.md` after completion.

Read `references/initial-milestone.md` only when an initial-milestone requirement or sample-dossier detail is needed.
