---
name: dispatch-subagent
description: Brief, dispatch and verify a Sonnet subagent for a task in this repo - worktree setup, the standard hard constraints every brief needs, and the verification checklist. Use when handing implementation or test work to a subagent, per the orchestrator/subagent split in CLAUDE.md.
---

# Dispatching a subagent on this repo

The main session orchestrates and reviews; subagents do the implementation. Each gets its own git
worktree and opens its own PR. Dispatch through the `herdr-subagent` skill so the work is visible
live - not the in-process Agent tool.

## 1. Worktree first

```bash
git worktree add -b <type>/<slice-name> ../fd-worktrees/<slice-name> main
git -C ../fd-worktrees/<slice-name> config user.name "Ajay Chodankar"
git -C ../fd-worktrees/<slice-name> config user.email "achodankar28@gmail.com"
```

Set the identity yourself - there is no global git config on this machine, and commits authored
with the wrong address do not link to the GitHub account.

Tasks may run in parallel only when their file sets are disjoint. Overlapping tasks get sequenced;
say so in each brief so a subagent knows which files another one owns.

## 2. Every brief needs these constraints

Subagents do not inherit the session's context, so each of these has to be restated:

- **Never touch the local environment file.** It holds live credentials. `.env.example` is the
  readable template. A `PreToolUse` hook enforces this; prose inside `-m`/`--body` values and
  heredocs is exempt, so commit messages are fine.
- **Never read `sample_data/UEBUNG_GROUND-TRUTH_SEALED_*.md`.** It is the sealed answer key,
  reserved for evaluation. Put any identifiers the task needs directly in the brief instead - see
  the `sample-dossier` skill.
- **Python dependencies are global** (3.12.7). There is no `backend/.venv`. Run
  `python -m pytest` from `backend/`.
- **A green suite inside a worktree proves nothing** about environment-sensitive behaviour:
  `core/settings.py` resolves `PROJECT_ROOT` from its own path, so a worktree has no local settings
  file and always takes the deterministic path.
- **Run the suite in the foreground, and commit and push as the very last action.**
- **Never weaken a test to make it pass.** A stated gap is better than a green suite that proves
  nothing. Say plainly in the PR body what did not work.
- No AI or agent name as commit co-author.

## 3. Scope it hard - this is the orchestrator's job, not the subagent's

A subagent that has to work out *what* to change will read the whole codebase first. That is slow,
expensive, and produces a worse change than one where the approach was already decided.

- **Name the exact files to read and the exact files to modify.** Say plainly: do not read or edit
  anything outside this list; if it seems necessary, stop and report back. That instruction works -
  it has produced a report of a normalization bug instead of an unreviewed fix buried in a diff.
- **Do not hand over a reading list of context documents.** "Read AGENTS.md, CLAUDE.md,
  PROJECT_CONTEXT.md, PLAN.md and these six modules" burns tens of thousands of tokens re-deriving
  what the orchestrator already knows. Put the conclusions in the brief.
- **One narrow change per dispatch.** Several small tasks beat one large one.
- **Decide the approach yourself and state it.** Do not delegate open design questions.
- Give acceptance criteria the subagent can check itself, and ask for measured numbers rather than
  adjectives.

## 4. Dispatch

```powershell
powershell -NoProfile -File <herdr-skill>/scripts/run-subagent.ps1 `
  -Brief <brief.md> -Label <label> -Cwd C:\personal\github\thecez\fd-worktrees\<slice-name> -TimeoutSec 3600
```

## 5. Verify - never trust the completion notification

The dispatcher detects completion via "agent went idle", which it cannot distinguish from *stalled
while waiting*. Three of the first six subagents on this repo finished their code and never
committed, and each was reported as complete.

```bash
git -C ../fd-worktrees/<slice> log --oneline main..HEAD   # did it commit?
git -C ../fd-worktrees/<slice> status --short             # anything uncommitted?
git branch -r | grep <slice>                              # did it push?
```

If work is uncommitted, it is usually intact - review it, run the suite, and commit it yourself
rather than re-dispatching.

Then, always:

1. Read the diff. Do not rely on the transcript summary.
2. Run the full suite yourself.
3. Re-verify any factual claim the subagent made about the data. Claims like "this identifier joins
   to that table" decide whether a feature works, and a passing test does not prove the claim.
4. Check the tests assert both directions where that matters - a test that only proves an absence
   proves nothing.

Merging is the human's call, never the orchestrator's.
