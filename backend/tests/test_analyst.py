"""Tests for the analyst (`app/analysis/analyst.py`).

Never makes a live model call - the model is a stand-in exposing only
``.invoke(messages)``, exactly as ``test_pipeline.py`` (and, before it,
``test_graph_analyzer.py``) does.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.analysis.analyst import (
    SYSTEM_PROMPT,
    ProposedFinding,
    ProposedFindingBatch,
    analyze_entry,
)
from tests.fraud_scenario_guard import (
    check_module_for_fraud_scenario_shape,
    check_text_for_fraud_scenario_shape,
)

_ANALYST_SOURCE = Path(__file__).resolve().parent.parent / "app" / "analysis" / "analyst.py"
_PROMPTS_MD = Path(__file__).resolve().parent.parent.parent / "agents" / "PROMPTS.md"


# ---------------------------------------------------------------------------
# One call, no tools
# ---------------------------------------------------------------------------


class _RecordingModel:
    """Stand-in for a `ChatOpenAI` already bound with `with_structured_output`
    - exposes only `.invoke(messages)`, nothing tool-shaped, matching the
    architectural decision that the analyst does not traverse."""

    def __init__(self, batch: ProposedFindingBatch) -> None:
        self.batch = batch
        self.invocations = 0
        self.received_messages: list[list[dict]] = []

    def invoke(self, messages):
        self.invocations += 1
        self.received_messages.append(messages)
        return self.batch


def test_analyze_entry_makes_exactly_one_model_call():
    model = _RecordingModel(ProposedFindingBatch(findings=[]))

    analyze_entry(model, "Entry PG-1\nsome brief text")

    assert model.invocations == 1


def test_analyze_entry_sends_the_system_prompt_and_the_full_brief_verbatim():
    model = _RecordingModel(ProposedFindingBatch(findings=[]))
    brief = "Entry PG-1\nRecords\nRecord record-a (vendor_posting)\n  BETRAG: 100"

    analyze_entry(model, brief)

    [messages] = model.received_messages
    assert messages[0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert messages[1] == {"role": "user", "content": brief}


def test_analyze_entry_returns_the_findings_from_a_structured_batch():
    proposal = ProposedFinding(
        title="Something worth a human look",
        severity="medium",
        category="review",
        explanation="Explanation text long enough to pass validation.",
        reasoning="Reasoning text long enough to pass validation.",
        confidence="low",
        record_ids=["record-a"],
    )
    model = _RecordingModel(ProposedFindingBatch(findings=[proposal]))

    findings = analyze_entry(model, "Entry PG-1")

    assert findings == [proposal]


def test_analyze_entry_returns_no_findings_when_the_model_returns_something_unstructured():
    class _UnstructuredModel:
        def invoke(self, messages):
            return "not a ProposedFindingBatch"

    assert analyze_entry(_UnstructuredModel(), "Entry PG-1") == []


def test_proposed_finding_has_no_evidence_field():
    """The central trust boundary: the model structurally cannot express
    evidence text, only record_ids - `app/analysis/pipeline.py` rehydrates
    evidence from the authoritative store."""
    assert "evidence" not in ProposedFinding.model_fields


# ---------------------------------------------------------------------------
# System prompt: PROMPTS.md §2 verbatim
# ---------------------------------------------------------------------------


def _extract_prompt_section(markdown_text: str, heading_prefix: str) -> str:
    lines = markdown_text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(heading_prefix))
    fence_start = next(i for i in range(start, len(lines)) if lines[i].strip() == "```text")
    fence_end = next(i for i in range(fence_start + 1, len(lines)) if lines[i].strip() == "```")
    return "\n".join(lines[fence_start + 1 : fence_end])


def test_system_prompt_matches_prompts_md_section_2_verbatim():
    """Asserts the module constant against the file's own §2 block so the two
    cannot silently drift apart - a hand-transcribed copy would look right
    and then quietly diverge the next time PROMPTS.md is edited."""
    markdown_text = _PROMPTS_MD.read_text(encoding="utf-8")
    expected = _extract_prompt_section(markdown_text, "## §2 - Stage 2: the analyst")
    assert SYSTEM_PROMPT == expected


# ---------------------------------------------------------------------------
# No encoded fraud scenario - module shape and prompt prose, both.
# ---------------------------------------------------------------------------


def test_analyst_module_has_no_encoded_fraud_scenario():
    violations = check_module_for_fraud_scenario_shape(_ANALYST_SOURCE)
    assert not violations, f"analyst.py has fraud-scenario-shaped code:\n" + "\n".join(violations)


def test_analyst_system_prompt_text_has_no_encoded_fraud_scenario():
    violations = check_text_for_fraud_scenario_shape(SYSTEM_PROMPT, label="analyst SYSTEM_PROMPT")
    assert not violations, f"analyst.py's prompt has fraud-scenario-shaped text:\n" + "\n".join(violations)
