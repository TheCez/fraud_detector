# Project Guidance

This repository follows the canonical instructions in [AGENTS.md](AGENTS.md). Read it first, then read `PROJECT_CONTEXT.md` and `agents/PLAN.md` before making changes.

Reusable project workflows live in two places, split by what they are about:

- `.codex/skills/` - **domain** workflows: how to build and change this application. `dossier-engineering` for ingestion, normalization, evidence and dashboard slices; `dossier-agent-integration` for the graph engine and the analysis agent. Read the one matching the active slice before touching it.
- `.claude/skills/` - **working** workflows for this repo: `dispatch-subagent` for briefing and verifying subagents, `sample-dossier` for the sample data's identifiers and performance measurement, `publish-repo` for publishing a repository.

Keep a workflow in one place only. If a domain rule and a working rule seem to conflict, the domain rule wins.

## Secrets

- Never read, open, copy, print, or pass along `.env`. It holds live OpenAI credentials.
- `.env.example` is the secret-free template. Read it when you need variable names.
- Only the backend reads the real values, at runtime, through `backend/app/core/settings.py`.
- `.env` stays gitignored and is never committed. A `PreToolUse` hook in `.claude/settings.json` blocks shell access to it, and `permissions.deny` blocks reading it - do not work around either.
- Do not echo secret values into logs, test fixtures, commit messages, or task descriptions.

## Branching - one worktree and one PR per task

Never commit or push directly to `main`. Every task or slice gets its own git worktree and its own pull request. No exceptions for "small" or "docs-only" changes.

Worktrees live outside the repo, in a sibling directory, so dependency installs and build output never collide between tasks:

```bash
git worktree add -b <type>/<slice-name> ../fd-worktrees/<slice-name> main
```

Use `feat/`, `fix/`, `chore/`, `test/`, or `docs/` for `<type>`, and name the branch after the `agents/PLAN.md` task it implements.

Then, from inside that worktree:

```bash
git push -u origin <type>/<slice-name>
gh pr create --fill --base main
```

Rules:

- One task, one worktree, one branch, one PR. A subagent works only inside its own worktree and never touches another's.
- Branch from current `main`, not from another task's branch, unless the task genuinely depends on unmerged work - say so in the PR body when it does.
- Each worktree needs its own frontend dependency install (`node_modules`) before frontend tests will run. Backend Python dependencies are installed in the machine's global Python 3.12.7 and are importable from any worktree - there is no per-worktree `backend/.venv`.
- The orchestrator reviews the PR and runs the full suite. Only the human merges.
- Remove the worktree once the PR is merged: `git worktree remove ../fd-worktrees/<slice-name>` then `git branch -d <type>/<slice-name>`.
- `cognee` is a preservation snapshot of the Cognee-based implementation. Do not build on it or merge it into `main`.

## Agent roles

The main session is the orchestrator. It plans, reviews, and integrates - it does not do the bulk coding itself.

- `agents/PLAN.md` is the shared work queue. Every agent reads it before starting and updates its task on finishing; its "How agents share this file" section is binding.
- Delegate implementation and test-writing to `sonnet` subagents, one self-contained vertical slice per subagent.
- Give each subagent the slice's acceptance criteria, the files it may touch, and the tests it must make pass - all three are already recorded per task in `agents/PLAN.md`.
- Run subagents concurrently only when their file sets do not overlap; otherwise sequence them.
- The orchestrator stays responsible for reviewing every diff, running the full test suite, and updating `PROJECT_CONTEXT.md`.
- Escalate a slice to the orchestrator model when it spans module boundaries or changes an interface in `backend/app/analysis/interface.py`.
