---
name: entry-brief
description: Render and inspect the real entry brief the analyst model sees for a sample-dossier entry, vendor, or asset. Use when the analyst missed or over-reported something, when changing entry_brief.py or profile.py, or to measure brief size and token cost.
---

# Inspecting what the analyst actually sees

The analyst gets one ledger entry as one text brief and nothing else - no tools, no follow-up. So the
first question about any miss is **was the fact in the brief at all**, and the only way to answer it is
to render the brief and read it.

```bash
cd backend
python ../.claude/skills/entry-brief/scripts/render_brief.py                        # size + token stats
python ../.claude/skills/entry-brief/scripts/render_brief.py --entity vendor:209101 # every entry touching it
python ../.claude/skills/entry-brief/scripts/render_brief.py --largest --section Parties
python ../.claude/skills/entry-brief/scripts/render_brief.py --graph PG-8134acaac5b855c4 --summary
```

First run normalizes the real ZIP and caches it (~20s). Pass `--rebuild` after changing a parser, the
graph builder, or anything upstream of the graph - otherwise you are reading a stale dossier and will
draw the wrong conclusion.

`--section` takes a section name: `Entry`, `Timeline`, `Records`, `Parties`, `Relationships`,
`Not present`, `Conventions`. `--summary` renders the ~400-token gate summary instead of the full brief.

## Why this matters more than it looks

Three rounds of diagnosis on one missed finding went: the model ignored the self-approval -> the brief
renders the vendor as counts instead of records -> the record was attached to a phantom `account` node
because `KONTO` on a `Kreditor` row was typed as an account. Only the third was true, and rendering the
brief is what settled it. A hypothesis about model behaviour is worthless until you have read the input.

## What to check

- **Is the fact present?** Grep the rendered text for the column name (`GEAENDERT_VON`) or the value
  (`MV-U05`). If it is absent, the problem is upstream - the parser, the graph, or the renderer - not
  the prompt.
- **Truncation.** No real entry may be truncated; `test_no_real_entry_in_the_sample_dossier_is_truncated_or_incomplete`
  is the contract. The script prints the marker count per entry. If a change introduces truncation,
  remove waste before raising a budget.
- **Size.** Median ~14.6k chars (~3.7k tokens), max ~21k. The whole dossier at one call per entry is
  ~15M input tokens. A change that grows the brief 20% costs 3M tokens per run.
- **Noise.** Text that is information-free by construction crowds out signal - an absence line whose
  peer count is zero says only "this is normal for this shape". Both the glossary and those lines were
  removed for this reason.

## Useful sample-dossier entities

See the `sample-dossier` skill for the full identifier table. Most used here:

| | |
|---|---|
| `vendor:209101` | shell vendor - no goods receipt; its `master_change` row has changer == approver == `MV-U05` |
| `vendor:209112` | the honest twin - also new mid-year, different approver, real receipts |
| `asset:040000-000191` | repair-worded asset, capitalized |

Note `209101` is a **vendor** node, not an account - it was an account node before PR #15.
