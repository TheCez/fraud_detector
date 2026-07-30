---
name: live-eval
description: Run and read the live ground-truth evaluation of the analyst against the sealed sample-dossier answer key - the only measurement of whether the pipeline actually finds fraud. Use before and after any change to a prompt, the entry brief, the profile, or a pipeline stage. Spends real money.
---

# Measuring the analyst against ground truth

`backend/tests/test_ground_truth_eval.py` calls the real analyst on the entries that hold the sample
dossier's four seeded findings, its seven decoys, and a seeded-random sample of ordinary entries, then
scores what came back against the sealed answer key. It is the only thing in this repo that measures
whether the pipeline works. Offline tests prove properties; this proves usefulness.

## Run it

```bash
cd backend
FRAUD_EVAL_LIVE=1 python -m pytest tests/test_ground_truth_eval.py -q -s
```

~231 model calls, roughly 70-90 seconds, negligible cost. Writes
`agents/T4_GROUND_TRUTH_EVAL_REPORT.md` - a table per case group plus per-case titles and severities.

**Run it from the orchestrator session, in the background, not from a subagent.** A subagent that
backgrounds this and waits looks *idle* to the dispatcher, its tab gets closed, and the run dies with
it. That has happened.

**A worktree has no `.env`.** `core/settings.py` derives `PROJECT_ROOT` from its own file location, so
in a worktree the agent always reads as unconfigured and every test skips - a green, free, meaningless
run. The module handles this: it locates the main checkout via `git worktree list` and executes *that
checkout's* `settings.py` to populate the process environment. Never read or copy `.env` yourself.

Absent `FRAUD_EVAL_LIVE` or credentials, every test skips before any fixture or network call, so
`python -m pytest -q` stays green and free everywhere.

## Safety properties - keep these

- A hard ceiling asserts planned calls against a limit **before** invoking anything, and a second
  assert checks actual equals planned. Neither a selection bug nor a retry storm can overspend.
- Failed calls are recorded as failures, never scored as "no finding", and the run fails loudly if
  every call errored - so an all-failures run cannot be read as a clean dossier.
- Nothing from the sealed file reaches any prompt, brief or model call. It is read only to choose
  entries and to judge answers.

## Reading the result honestly

Look at the **titles**, not the flag counts. The scoring criterion is "did it propose anything for this
entry", which for entries this small is close to "did it cite the seeded records" - but it counts a
proposal about something else in the same entry as a hit. Two real cases of that:

- The split-payment entry scored 3/3 while every title was about posting classification. Nothing
  mentioned same-day payments below a threshold. That is a miss the number hides.
- The F3 group maps by *source file*, so all 8 January-2026 invoices score as the seeded finding when
  only one is. A precision improvement there reads as a recall regression until you read the titles.

Also check whether a missed fact was **in the brief at all** before blaming the model - see the
`entry-brief` skill. The shell-vendor miss looked like a model failure for two rounds; it was a
normalization bug that put the changer/approver record on a phantom account node.

## Baseline to compare against

`gpt-5.4`, 77 cases x 3 runs, after PRs #14 and #15:

| | |
|---|---|
| ordinary control entries flagged | 8% (was 16% before the prompt calibration) |
| decoy entries flagged | 48% (was 56%) |
| repair capitalization | found, right reasons, 7 entries at 3/3 |
| period cut-off | found, cites the conflicting dates |
| shell vendor | its own signals not yet found |
| split payments | flagged, wrong reason |

Precision is the open problem: 8% across 4,902 entries extrapolates to ~390 findings on a dossier
whose answer key holds four. Vendor `209112` is the sharpest discriminator - new mid-year like the
shell vendor, but with a different approver and real goods receipts. Flagging it means the prompt
produces false positives on ordinary business.

## The rule

Never tune the prompt, brief, profile or analyst to make this pass. A green evaluation obtained by
adjusting the thing being evaluated is worthless. If recall is poor, that is the finding - report it.
