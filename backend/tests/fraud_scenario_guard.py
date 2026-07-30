"""Shared fraud-scenario guard - the mechanical enforcement of this project's
central rule (`agents/PLAN.md`: no fraud scenario may be encoded in code or in
a prompt).

Originally written once in `test_profile.py` against `profile.py`/
`entry_brief.py`. Moved here, unchanged in its module-shape logic, so every
test module that needs it - `test_profile.py`, `test_entry_brief.py`,
`test_analyst.py`, `test_pipeline.py` - imports one implementation instead of
each carrying its own copy that could silently drift apart.

Two independent checks, because code and prompts fail in different shapes:

- ``check_module_for_fraud_scenario_shape`` walks a module's AST for the shape
  the deleted `prefilter.py`/`graph_analyzer.py` took: a domain-judgement-named
  constant, a threshold-sized numeric literal, or a new keyword-list-shaped
  collection.
- ``check_text_for_fraud_scenario_shape`` scans prose - a system prompt is a
  string constant, not code, so no AST rule above ever looks at its content.
  It denies the specific named-irregularity phrasing the deleted red-flag
  briefing used, and any numeric literal at all - `agents/PROMPTS.md`'s
  doctrine forbids "an amount threshold, a date range" outright, and the
  canonical prompts contain no digit anywhere, so this is exact, not a
  heuristic that happens to work today.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

# --- Rule 1: module-level constant names suggesting domain judgement. ---
_FORBIDDEN_NAME_PATTERN = re.compile(
    r"KEYWORD|THRESHOLD|SIGNAL|PATTERN|SUSPICIOUS|FRAUD|RISK|EXPECTED|REQUIRED", re.IGNORECASE
)

# profile.py's/entry_brief.py's per-section character budgets, and
# pipeline.py's/analyst.py's own budget-shaped constants (see their module
# docstrings) - named module constants by design, not domain vocabulary.
_ALLOWED_LARGE_NUMERIC_CONSTANT_NAMES = {
    "ENTRY_SECTION_BUDGET",
    "TIMELINE_SECTION_BUDGET",
    "RECORDS_SECTION_BUDGET",
    "PARTIES_SECTION_BUDGET",
    "RELATIONSHIPS_SECTION_BUDGET",
    "NOT_PRESENT_SECTION_BUDGET",
    "CONVENTIONS_SECTION_BUDGET",
    "SUMMARY_BUDGET",
    # analyst.py's pydantic Field length bounds and pipeline.py's evidence
    # excerpt cap - structural validation/truncation bounds, not domain
    # thresholds. See those modules' docstrings.
    "TITLE_MAX_LENGTH",
    "EXPLANATION_MAX_LENGTH",
    "REASONING_MAX_LENGTH",
    "EVIDENCE_EXCERPT_MAX_CHARS",
}

# A numeric literal at or above this is treated as potentially threshold-
# shaped. Comfortably above every legitimate small literal these modules use
# today (list indices, quantile points, percentile formatting, worker/log
# intervals) and comfortably below the smallest section budget (400).
_MODEST_NUMERIC_BOUND = 200

# The exact value of every constant collection that exists across the
# analysis modules today and is legitimate structural vocabulary, not a fraud
# scenario. An allowlist keyed by *value*, not name: the point is not to
# remember one removed mistake by name, it is that any new collection of this
# shape fails until someone deliberately adds its exact value here.
_ALLOWED_CONSTANT_COLLECTIONS = {
    ("date", "amount", "counterparty", "document_reference"),  # COMPLETENESS_DIMENSIONS
    ("BELEGNUMMER", "BUCHUNGSNUMMER", "DOKUMENT", "RECHNUNGSNUMMER"),  # _DOCUMENT_REFERENCE_FIELDS
    ("vendor", "customer"),  # _COUNTERPARTY_ENTITY_TYPES
    ("master_data", "master_change"),  # _MASTER_DATA_RECORD_TYPES
    (0.0, 0.25, 0.5, 0.75, 0.9, 0.99, 1.0),  # _QUANTILE_POINTS
}


def check_module_for_fraud_scenario_shape(source_path: Path) -> list[str]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    violations: list[str] = []

    # Rule 1: module-level constant names suggesting domain judgement.
    for stmt in ast.iter_child_nodes(tree):
        target_name = None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            target_name = stmt.targets[0].id
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            target_name = stmt.target.id
        if target_name and _FORBIDDEN_NAME_PATTERN.search(target_name):
            violations.append(f"module-level constant {target_name!r} has a domain-judgement-shaped name")

    # Nodes that belong to an allowed large-numeric-constant's own value
    # subtree are exempt from rule 2.
    exempt_numeric_node_ids: set[int] = set()
    for stmt in ast.iter_child_nodes(tree):
        name = None
        value = None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            name, value = stmt.targets[0].id, stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
            name, value = stmt.target.id, stmt.value
        if name in _ALLOWED_LARGE_NUMERIC_CONSTANT_NAMES:
            exempt_numeric_node_ids.update(id(node) for node in ast.walk(value))

    # Rule 2: no numeric literal at/above the modest bound outside an
    # allowed named budget constant.
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            if id(node) in exempt_numeric_node_ids:
                continue
            if abs(node.value) >= _MODEST_NUMERIC_BOUND:
                violations.append(
                    f"numeric literal {node.value!r} is at/above the modest bound "
                    f"({_MODEST_NUMERIC_BOUND}) outside an allowed budget constant"
                )

    # Rule 3: no new module-level constant bound to a tuple/list/set literal
    # of 2+ same-kind constants. Scoped to module-level assignments only (not
    # any collection literal anywhere, e.g. inside a function body) - genuine
    # domain vocabulary is always given a reusable name, which is exactly what
    # every historical example (prefilter.py's deleted _REPAIR_KEYWORDS, and
    # this module's own allowlisted collections above) does; sweeping in every
    # anonymous literal also catches ordinary structural code - a two-element
    # counter pair like ``[0, 0]``, or a function's own list of output lines -
    # which would make the rule too noisy to keep.
    for stmt in ast.iter_child_nodes(tree):
        target_name, value = None, None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            target_name, value = stmt.targets[0].id, stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
            target_name, value = stmt.target.id, stmt.value
        if value is None or not isinstance(value, (ast.Tuple, ast.List, ast.Set)):
            continue
        elts = value.elts
        if len(elts) < 2 or not all(isinstance(elt, ast.Constant) for elt in elts):
            continue
        values = [elt.value for elt in elts]
        is_all_str = all(isinstance(v, str) for v in values)
        is_all_number = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values)
        if not (is_all_str or is_all_number):
            continue
        if target_name == "__all__":
            continue
        if tuple(values) in _ALLOWED_CONSTANT_COLLECTIONS:
            continue
        violations.append(
            f"module-level constant {target_name!r} is a new "
            f"{type(value).__name__.lower()} literal {tuple(values)!r} with a domain-vocabulary shape"
        )

    return violations


# --- Prose check: a system prompt is a string constant, invisible to the AST
# rules above (none of them inspect the *content* of a string literal). ---

# The specific named-irregularity phrasing the deleted red-flag briefing
# (`graph_analyzer.py`'s old `_SYSTEM_PROMPT`) and pre-filter (`prefilter.py`)
# used. Not a general vocabulary denylist - the doctrine in `agents/PROMPTS.md`
# explicitly allows naming the observables (dates, documents, parties,
# amounts); this list only catches a *named pattern* or a judgement word, the
# two things a directional prompt must never contain.
_FORBIDDEN_PROMPT_PHRASES = (
    "segregation of duties",
    "segregation-of-duties",
    "round amount",
    "round number",
    "goods receipt",
    "goods-receipt",
    "period cut-off",
    "period cutoff",
    "capitalized as a fixed asset",
    "capitalized instead of expensed",
    "payments split",
    "split into several amounts",
    "approval threshold",
    "approval limit",
    "red flag",
    "suspicious",
    "fraud",
)

# Any digit at all is enough to flag: an amount threshold or a date range is
# necessarily written with digits, and the canonical prompts in
# `agents/PROMPTS.md` contain none - this is not a heuristic tuned to pass
# today's text, it is what the doctrine there requires outright.
_PROMPT_NUMERIC_RE = re.compile(r"\d")


def check_text_for_fraud_scenario_shape(text: str, *, label: str) -> list[str]:
    violations: list[str] = []
    lowered = text.lower()
    for phrase in _FORBIDDEN_PROMPT_PHRASES:
        if phrase in lowered:
            violations.append(f"{label}: contains forbidden fraud-scenario phrase {phrase!r}")
    if _PROMPT_NUMERIC_RE.search(text):
        violations.append(f"{label}: contains a numeric literal (a possible amount, date or threshold)")
    return violations


__all__ = [
    "check_module_for_fraud_scenario_shape",
    "check_text_for_fraud_scenario_shape",
]
