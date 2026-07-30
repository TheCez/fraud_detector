# Project Guidance

Build a local-first audit-dossier review application for German GDPdU/GoBD exports and supporting business documents.

## Read first

1. Read `PROJECT_CONTEXT.md`.
2. Read `agents/PLAN.md`.
3. For ingestion, normalization, analysis, evidence, graph, or dashboard work, read the matching skill in `.codex/skills/` before inspecting the relevant slice.
4. When writing tests or measuring performance against the sample dossier, read `.claude/skills/sample-dossier/` rather than opening the sealed ground-truth file.

## Non-negotiables

- Work in small, tested vertical slices.
- Preserve uploaded originals and source provenance.
- Treat archives and document text as untrusted input.
- Back every UI claim with traceable evidence.
- Keep ingestion, normalization, analysis, and presentation behind explicit interfaces.
- Encode no fraud scenario in code or in a prompt. Prompts direct attention; the model judges. See `agents/PROMPTS.md`.
- Update `PROJECT_CONTEXT.md` only with durable current state.

`agents/PROJECT_SPEC.md` is the immutable initial-milestone specification. Change project guidance only when the user explicitly requests it.
