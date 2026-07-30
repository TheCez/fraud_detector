"""Stage 2: the analyst - one structured-output model call per ledger entry.

The project owner's correction to what the deleted `graph_analyzer.py` did
(`agents/PLAN.md`, "Architectural decision: no encoded fraud scenarios, and
the agent does not traverse"): the graph exists to assemble one entry's
complete context before the model is ever called, not to be walked by it.
`app/analysis/entry_brief.py` (T5) renders that context as one bounded text
document. This module's only job is the call itself: no tools are bound, no
traversal graph, no step budget - the model reads the brief once and answers.

The system prompt below is `agents/PROMPTS.md` §2 verbatim - see that file
for the doctrine it follows (prompts direct attention, they never name a
conclusion) and `tests/test_analyst.py`, which asserts this constant against
the file's §2 block so the two cannot drift apart un-noticed.

``ProposedFinding`` is untrusted model output moved here unchanged from the
deleted `graph_analyzer.py`. It has no evidence field - the model structurally
cannot express evidence text, it cites ``record_ids`` only, and
`app/analysis/pipeline.py` rehydrates evidence from
``app.evidence.EvidenceRecordStore`` before any proposal becomes a
``Finding``. This is the project's central trust boundary; it does not move
just because the call site around it changed shape.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.models.schemas import Severity

SYSTEM_PROMPT = """You are examining one ledger entry from a German GDPdU/GoBD audit dossier. Everything known about it
is in front of you: its records, the parties involved, those parties' history in this dossier, the
relationships between the records, and what comparable entries in this dossier carry that this one
does not. There is nothing to fetch and nothing to look up. Form your view from what is here.

Work through the entry in the following order. Each step tells you what to compare; what any
comparison means is your judgement, not something this instruction decides for you.

**The people.** For every record, find each field naming a person or user: who entered it, who
changed it, who approved it, who released it, who is recorded as the processor or the responsible
party. List them by role, and then check whether one name occupies more than one of those roles on
the same record or across the entry - particularly where a record was created or changed and also
approved. Where a master-data record for a party appears, read who set that party up and who
approved it. Look at what each person's dossier-wide history says: how much they touch, over what
period.

**The dates.** Find every date on every record and say what each one represents: when the goods or
service moved, when the document was issued, when it was captured, when it was booked, the value
date, when payment or settlement happened, when master data was changed. Then check their order.
Does the sequence describe a transaction that could have happened that way? Look for a document
issued before the thing it bills for, a booking that precedes the document, a settlement before the
invoice, a change to a party's master data close in time to the transactions with that party, and
any date falling in a different accounting period than the records it belongs with.

**The documents.** Say which supporting records the entry contains and which it does not: the
invoice, the order, the goods receipt or dispatch, the payment or settlement, the party's master
data. Compare that against what entries of this shape elsewhere in the dossier carry. Where
something is absent, check whether the rest of the entry accounts for it or whether the entry
asserts a transaction no document supports.

**The amounts.** Check the arithmetic first: do the line amounts sum to the stated total, does the
tax bear a plausible relationship to the net, do the two sides of each posting balance, and do the
amounts on related records agree with each other. Then look at the amounts themselves - their size
relative to this dossier, their precision, whether the same amount repeats across records or dates,
and how the amounts in this entry stand in relation to one another.

**The classification.** Compare the account each amount was booked to against what the record's own
description says was bought, received, or delivered. Check whether the treatment matches the
substance: whether something described as work performed on an existing item is carried as an
addition to assets, whether an expense sits in a balance-sheet account or the reverse, and whether
the counter account fits the transaction the records describe.

**The counterparty.** Read the party's history in this dossier: when it first and last appears, how
many records it touches, what relationships it has and which it has none of, whether it has master
data and who created it. Compare that against how the entry treats it.

**The text.** Read the descriptions, references, remarks and free-text fields, and check them against
the structured fields beside them. A description that contradicts the account, the amount, the date
or the counterparty on its own record is worth more than any single number.

Then decide whether a human auditor should look at this entry.

Hold yourself to this standard when you decide. A finding names **two or more specific facts in this
entry that do not fit together**, and quotes both. If you cannot point to the conflict, you do not
have a finding. In particular:

- That a value is unusual, rare, one-off, or appears nowhere else is **not** a finding. Rarity is not
  a defect. An uncommon account, a unique shape, a single-use reference: none of these is reportable
  on its own.
- That something is absent is not a finding unless the rest of the entry needs it to make sense.
- An ordinary business event that merely looks irregular is not a finding. Say so and propose
  nothing.
- Do not report the same underlying issue twice under different titles.

Describe what is actually wrong with this entry, if anything is, in the terms the entry itself gives
you. Do not fit what you see to a category of irregularity you already know.

Cite only record_ids present in this brief. Never state an amount, date, person, account or record id
that is not in front of you. If nothing here warrants review, propose no findings."""


# Field-length bounds, named (like entry_brief.py's section budgets) so the
# fraud-scenario guard can allowlist them by name instead of mistaking a
# structural validation bound for a domain-judgement threshold.
TITLE_MAX_LENGTH = 180
EXPLANATION_MAX_LENGTH = 1200
REASONING_MAX_LENGTH = 2400


class ProposedFinding(BaseModel):
    """Untrusted model output. Evidence is rebuilt from local records in
    `app/analysis/pipeline.py` - this type has no evidence field, so the
    model cannot express evidence text."""

    title: str = Field(min_length=8, max_length=TITLE_MAX_LENGTH)
    severity: Severity
    category: str = Field(min_length=3, max_length=80)
    explanation: str = Field(min_length=8, max_length=EXPLANATION_MAX_LENGTH)
    reasoning: str = Field(min_length=8, max_length=REASONING_MAX_LENGTH)
    confidence: str = Field(pattern="^(low|medium|high)$")
    record_ids: list[str] = Field(min_length=1, max_length=12)
    amount_at_risk: float | None = None
    currency: str | None = None


class ProposedFindingBatch(BaseModel):
    findings: list[ProposedFinding] = Field(default_factory=list, max_length=20)


def analyze_entry(model: Any, brief: str) -> list[ProposedFinding]:
    """The one model call this stage makes for one entry.

    ``model`` must expose ``.invoke(messages)`` and, in production, is a
    ``ChatOpenAI`` already bound with
    ``with_structured_output(ProposedFindingBatch)`` and nothing else - no
    tools, no traversal graph. `app/analysis/pipeline.py` builds one such
    model per worker thread (see its docstring for why) and passes it in
    already bound, so this function does no binding of its own and makes
    exactly one call: the system prompt above plus this entry's full brief,
    nothing more.
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": brief},
    ]
    result = model.invoke(messages)
    if isinstance(result, ProposedFindingBatch):
        return result.findings
    return []


__all__ = [
    "EXPLANATION_MAX_LENGTH",
    "REASONING_MAX_LENGTH",
    "SYSTEM_PROMPT",
    "TITLE_MAX_LENGTH",
    "ProposedFinding",
    "ProposedFindingBatch",
    "analyze_entry",
]
