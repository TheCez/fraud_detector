"""Self-tests for `tests/fraud_scenario_guard.py`.

The guard is the only mechanical enforcement of this project's central rule
(`agents/PLAN.md`: no fraud scenario may be encoded in code or in a prompt),
and it is non-trivial code in its own right. An AST walk or a phrase scan that
silently matched nothing would let every future violation through while still
reporting a green suite - exactly the failure mode the guard exists to
prevent. So the guard is tested against known-bad shapes (each one is the
shape of thing the deleted `prefilter.py`/`graph_analyzer.py` actually
contained) and against clean code/text.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fraud_scenario_guard import (
    check_module_for_fraud_scenario_shape,
    check_text_for_fraud_scenario_shape,
)

# ---------------------------------------------------------------------------
# Module (AST) checks
# ---------------------------------------------------------------------------

_MODULE_GUARD_CASES = [
    pytest.param(
        '_REPAIR_KEYWORDS = ("reparatur",)\n',
        "domain-judgement-shaped name",
        id="rule1-name-shaped-constant",
    ),
    pytest.param(
        '_WORDS = ("renovierung", "sanierung", "umbau")\n',
        "domain-vocabulary shape",
        id="rule3-new-keyword-list-the-old-denylist-would-have-missed",
    ),
    pytest.param(
        "_LIMITS = (2_500.0, 7_500.0)\n",
        "at/above the modest bound",
        id="rule2-threshold-the-old-denylist-would-have-missed",
    ),
]


@pytest.mark.parametrize("source,expected_message", _MODULE_GUARD_CASES)
def test_the_module_guard_fires_on_a_reintroduced_violation(tmp_path: Path, source: str, expected_message: str):
    module_path = tmp_path / "reintroduced.py"
    module_path.write_text(source, encoding="utf-8")
    violations = check_module_for_fraud_scenario_shape(module_path)
    assert violations, f"guard did not fire on:\n{source}"
    assert any(expected_message in violation for violation in violations), (
        f"guard fired but not for the expected reason ({expected_message!r}): {violations}"
    )


def test_the_module_guard_passes_code_with_no_domain_vocabulary(tmp_path: Path):
    module_path = tmp_path / "clean.py"
    module_path.write_text('MAX_ROWS = 3\nLABEL = "record"\n', encoding="utf-8")
    assert check_module_for_fraud_scenario_shape(module_path) == []


# ---------------------------------------------------------------------------
# Prose (text) checks - a system prompt is a string, invisible to the AST
# rules above.
# ---------------------------------------------------------------------------

_TEXT_GUARD_CASES = [
    pytest.param(
        "Watch for payments split into several amounts, each kept just below an "
        "approval threshold of 10,000.",
        "forbidden fraud-scenario phrase",
        id="named-irregularity-payment-splitting",
    ),
    pytest.param(
        "If the same person both changed and approved a record, that is a "
        "segregation of duties violation.",
        "forbidden fraud-scenario phrase",
        id="named-irregularity-segregation-of-duties",
    ),
    pytest.param(
        "For example, an invoice of exactly 9999.00 EUR paid the same day it "
        "was issued is a worked example of a suspicious pattern.",
        "forbidden fraud-scenario phrase",
        id="worked-example-with-suspicious",
    ),
    pytest.param(
        "Treat any posting dated between 01.12.2025 and 31.01.2026 as worth a closer look.",
        "numeric literal",
        id="bare-date-range-threshold",
    ),
]


@pytest.mark.parametrize("text,expected_message", _TEXT_GUARD_CASES)
def test_the_text_guard_fires_on_a_reintroduced_violation(text: str, expected_message: str):
    violations = check_text_for_fraud_scenario_shape(text, label="test prompt")
    assert violations, f"guard did not fire on:\n{text}"
    assert any(expected_message in violation for violation in violations), (
        f"guard fired but not for the expected reason ({expected_message!r}): {violations}"
    )


def test_the_text_guard_passes_prose_with_no_fraud_scenario():
    text = (
        "Observe the dates on each record and consider whether their order is a "
        "plausible way for this kind of business event to have happened. Cite "
        "only record_ids present in this brief."
    )
    assert check_text_for_fraud_scenario_shape(text, label="test prompt") == []
