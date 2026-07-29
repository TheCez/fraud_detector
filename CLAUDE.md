# Project Guidance

This repository follows the canonical instructions in [AGENTS.md](AGENTS.md). Read it first, then read `PROJECT_CONTEXT.md` and `agents/PLAN.md` before making changes.

Reusable project workflows live in `.codex/skills/`. Use the skill relevant to the active vertical slice.

## Secrets

- Never read, open, copy, print, or pass along `.env`. It holds live Cognee and OpenAI credentials.
- `.env.example` is the secret-free template. Read it when you need variable names.
- Only the backend reads the real values, at runtime, through `backend/app/core/settings.py`.
- `.env` stays gitignored and is never committed. A `PreToolUse` hook in `.claude/settings.json` blocks shell access to it, and `permissions.deny` blocks reading it - do not work around either.
- Do not echo secret values into logs, test fixtures, commit messages, or task descriptions.

## Agent roles

The main session is the orchestrator. It plans, reviews, and integrates - it does not do the bulk coding itself.

- `agents/PLAN.md` is the shared work queue. Every agent reads it before starting and updates its task on finishing; its "How agents share this file" section is binding.
- Delegate implementation and test-writing to `sonnet` subagents, one self-contained vertical slice per subagent.
- Give each subagent the slice's acceptance criteria, the files it may touch, and the tests it must make pass - all three are already recorded per task in `agents/PLAN.md`.
- Run subagents concurrently only when their file sets do not overlap; otherwise sequence them.
- The orchestrator stays responsible for reviewing every diff, running the full test suite, and updating `PROJECT_CONTEXT.md`.
- Escalate a slice to the orchestrator model when it spans module boundaries or changes an interface in `backend/app/analysis/interface.py`.
