"""T4: ground-truth evaluation of the live single-call analyst.

`agents/PLAN.md`'s T4: measure whether `app/analysis/analyst.py`'s single
directional prompt (no encoded fraud scenario, `agents/PROMPTS.md` SS2) still
finds the sample dossier's four seeded findings, and stays quiet on its seven
decoys and a sample of ordinary entries, now that the recall-oriented
pre-filter and red-flag briefing that used to make this dossier easy are
gone.

**This spends real money.** Gated behind ``FRAUD_EVAL_LIVE=1`` *and* a
configured agent (``FRAUD_AGENT_ENABLED`` + ``OPENAI_API_KEY``) - absent
either, every test in this module is skipped before any fixture, model, or
network call happens. ``cd backend && python -m pytest -q`` stays green and
free on every machine, including CI with no credentials.

Matching criterion (stated once here, as T4's acceptance criteria requires).
The analyst is called once per ledger entry (one process graph) and can only
cite ``record_ids`` present in that entry's own rendered brief - a proposal's
record_ids are therefore always a subset of that entry's own
``process_graph.record_ids``. For every case in the seeded group (F1-F4)
below, the entry's own records ARE the seeded issue's evidence chain (the
shell vendor's invoice+payment pair, the mis-capitalised asset's own
posting, the cut-off invoice itself, the split-payment postings) - nothing
else is in a brief this small (median 8 records) for a stray finding to be
about. So "the analyst's proposal cites a record_id belonging to the seeded
finding" and "the analyst proposed anything at all for this entry" are the
same event here, and this module scores recall as the latter, which is exactly
the former restricted to entries this small. Decoys and controls are scored
only for whether anything was proposed at all - there is no seeded finding to
overlap with, only a possible false positive.

Nothing from the sealed ground-truth file
(``sample_data/UEBUNG_GROUND-TRUTH_SEALED_Muster-Verpackungen.md``) reaches
any prompt, brief, or model call. It is read only by whoever authored this
module's case list and whoever reads this file's report afterward, to know
which entries to evaluate and to judge what came back. The identifiers below
(vendor/asset/document numbers) are the minimum needed to name a case; no
prose from that file is reproduced here.

Credentials: this worktree has no local ``.env`` (`agents/PLAN.md`'s
documented trap - ``core/settings.py`` derives its search path from its own
file's location, so a worktree always takes the unconfigured path). Rather
than reading any env file directly - `CLAUDE.md` reserves that to
``backend/app/core/settings.py`` - ``_bootstrap_main_checkout_credentials``
below locates the main checkout via ``git worktree list`` (never a guessed
sibling path) and executes *that checkout's own* ``settings.py``, the same
module, just from the location that actually has a ``.env``. Its only effect
used here is the process environment it populates as a side effect of its own
``load_dotenv`` call; this module never opens, prints, or returns the key
itself.
"""

from __future__ import annotations

import concurrent.futures
import importlib.util
import json
import os
import random
import subprocess
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from app.analysis.analyst import ProposedFinding
from app.analysis.analyst import analyze_entry as real_analyze_entry
from app.analysis.entry_brief import render_entry_brief as real_render_entry_brief
from app.analysis.pipeline import _build_analyst_model
from app.analysis.profile import DossierProfile
from app.core.settings import AgentSettings
from app.graph.subgraphs import ProcessGraph
from app.persistence.database import iter_records_by_dossier
from tests.conftest import SAMPLE_DOSSIER_ID, requires_sample_zip

# ---------------------------------------------------------------------------
# Gate: skip everything, before any fixture runs, unless explicitly opted in.
# ---------------------------------------------------------------------------

_LIVE_ENV_VAR = "FRAUD_EVAL_LIVE"


def _live_flag_set() -> bool:
    return os.getenv(_LIVE_ENV_VAR, "").strip().lower() in {"1", "true", "yes"}


pytestmark = [
    requires_sample_zip,
    pytest.mark.skipif(
        not _live_flag_set(),
        reason=f"set {_LIVE_ENV_VAR}=1 to run the live ground-truth evaluation (spends real model calls)",
    ),
]

# The project's current default model, per agents/PLAN.md T4 - never
# overridden by this module.
ANALYST_MODEL = "gpt-5.4"

NUM_RUNS = 3

# Hard cost guard: refuse to spend a single call if the evaluation set is
# bigger than planned. Checked before any model is built or called.
CALL_CEILING = 400

_CONCURRENCY = 10

_AGENT_ENV_VARS = ("FRAUD_AGENT_ENABLED", "OPENAI_API_KEY", "OPENAI_MODEL")


# ---------------------------------------------------------------------------
# Credential bootstrap - see module docstring.
# ---------------------------------------------------------------------------


def _discover_main_checkout_root() -> Path | None:
    """The repo's main checkout, found by asking git rather than guessing a
    sibling directory name - `git worktree list` always lists the main
    worktree first."""
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    first_line = next((line for line in result.stdout.splitlines() if line.startswith("worktree ")), None)
    if first_line is None:
        return None
    return Path(first_line[len("worktree ") :].strip())


def _bootstrap_main_checkout_credentials() -> None:
    """Load real credentials into this process's environment by executing
    the main checkout's own ``backend/app/core/settings.py`` - see module
    docstring for why this, and not reading any env file here, is the
    sanctioned mechanism. A no-op if the main checkout or its settings
    module cannot be found; the configuration check right after this call
    is what turns that into a clear skip rather than a confusing failure."""
    main_root = _discover_main_checkout_root()
    if main_root is None:
        return
    settings_path = main_root / "backend" / "app" / "core" / "settings.py"
    if not settings_path.exists():
        return
    spec = importlib.util.spec_from_file_location("_t4_eval_main_checkout_settings_bootstrap", settings_path)
    if spec is None or spec.loader is None:
        return
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)  # side effect only: populates os.environ
    except Exception:
        return


@pytest.fixture(scope="session")
def _bootstrapped_agent_env():
    """Runs the bootstrap once per session and hands back a snapshot of the
    agent env vars it found - never the whole environment, never logged.
    Restores whatever was there before once the session-scoped fixture tears
    down, so this module's credential bootstrap does not outlive the run."""
    previous = {name: os.environ.get(name) for name in _AGENT_ENV_VARS}
    _bootstrap_main_checkout_credentials()
    snapshot = {name: os.environ[name] for name in _AGENT_ENV_VARS if os.environ.get(name)}
    yield snapshot
    for name, value in previous.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


@pytest.fixture
def live_agent_settings(monkeypatch, _bootstrapped_agent_env) -> AgentSettings:
    """The suite's autouse `isolated_agent_environment` fixture (tests/conftest.py)
    deletes every agent env var before each test; this fixture is how this
    module explicitly opts back in, exactly as that fixture's own docstring
    invites."""
    for name, value in _bootstrapped_agent_env.items():
        monkeypatch.setenv(name, value)

    enabled = os.getenv("FRAUD_AGENT_ENABLED", "").strip().lower() in {"1", "true", "yes"}
    api_key = os.getenv("OPENAI_API_KEY")
    if not (enabled and api_key):
        pytest.skip(
            "agent not configured: FRAUD_AGENT_ENABLED and OPENAI_API_KEY must both be set "
            "(via the main checkout's .env) to run the live ground-truth evaluation"
        )
    return AgentSettings(
        openai_api_key=api_key,
        analyst_model=ANALYST_MODEL,
        gate_model=ANALYST_MODEL,
        verifier_model=ANALYST_MODEL,
        agent_enabled=True,
        model_call_cap=10_000,
        max_workers=_CONCURRENCY,
    )


# ---------------------------------------------------------------------------
# Case resolution: identifiers -> the process graphs that hold their records.
#
# F1/F2/F4's vendor/asset numbers and the composite account for F4 are the
# non-sealed identifiers `.claude/skills/sample-dossier/SKILL.md` already
# documents for this exact reason. F3's identifier is a source file (every
# invoice in it is one of the eight seeded cut-off invoices). The decoy and
# control identifiers below come from the sealed ground-truth file, read only
# to build this list - see module docstring.
# ---------------------------------------------------------------------------

_F2_ASSET_IDS = (
    "040000-000191",
    "040000-000192",
    "040000-000194",
    "040000-000196",
    "060000-000193",
    "060000-000195",
)
_F3_SOURCE_FILE = "Begleitdokumente/Fakturajournal_Januar_2026_Kreditoren.csv"

_DOCUMENT_FIELDS = ("BELEGNUMMER", "BUCHUNGSNUMMER", "DOKUMENT", "RECHNUNGSNUMMER")
_ACCOUNT_FIELDS = ("SACHKONTONUMMER", "GEGENKONTO")

# Fixed seeds for the two sampled groups below, so the evaluation set is
# reproducible across runs and machines.
_D4_SAMPLE_SEED = 4902
_D4_SAMPLE_SIZE = 5
_CONTROL_SAMPLE_SEED = 4903
_CONTROL_SAMPLE_SIZE = 25


@dataclass(frozen=True)
class _RecordIndexEntry:
    record_id: str
    record_type: str
    graph_id: str | None
    relative_path: str | None
    documents: frozenset
    accounts: frozenset


def _build_record_index(
    db_path: Path, dossier_id: str, process_graphs: list[ProcessGraph]
) -> list[_RecordIndexEntry]:
    record_to_graph: dict[str, str] = {}
    for pg in process_graphs:
        for rid in pg.record_ids:
            record_to_graph[rid] = pg.graph_id

    entries: list[_RecordIndexEntry] = []
    for row in iter_records_by_dossier(db_path, dossier_id):
        parsed = json.loads(row["data_json"])
        data = parsed.get("data") or {}
        entries.append(
            _RecordIndexEntry(
                record_id=row["record_id"],
                record_type=row["record_type"],
                graph_id=record_to_graph.get(row["record_id"]),
                relative_path=(parsed.get("source") or {}).get("relative_path"),
                documents=frozenset(str(data[f]) for f in _DOCUMENT_FIELDS if data.get(f)),
                accounts=frozenset(str(data[f]) for f in _ACCOUNT_FIELDS if data.get(f)),
            )
        )
    return entries


def _graphs_by_entity(process_graphs: list[ProcessGraph], entity_node_id: str) -> list[str]:
    return sorted({pg.graph_id for pg in process_graphs if entity_node_id in pg.entity_node_ids})


def _graphs_by_document(index: list[_RecordIndexEntry], document_id: str) -> list[str]:
    return sorted({e.graph_id for e in index if e.graph_id and document_id in e.documents})


def _graphs_by_source_file(index: list[_RecordIndexEntry], relative_path: str) -> list[str]:
    return sorted({e.graph_id for e in index if e.graph_id and e.relative_path == relative_path})


def _graphs_by_account_journal_entries(index: list[_RecordIndexEntry], account_id: str) -> list[str]:
    return sorted(
        {e.graph_id for e in index if e.graph_id and e.record_type == "journal_entry" and account_id in e.accounts}
    )


@dataclass(frozen=True)
class EvalCase:
    graph_id: str
    group: str
    label: str
    seeded: bool  # True for F1-F4: score against the seeded-finding record set.


def build_cases(
    process_graphs: list[ProcessGraph], db_path: Path, dossier_id: str
) -> tuple[list[EvalCase], dict[str, Any]]:
    """Every case this evaluation will call the analyst on, plus a small
    dict of counts (for the report's own transparency about what was
    sampled). Never excludes a seeded/decoy identifier's graphs - only the
    two explicitly bounded groups (D4, CONTROL) are sampled, both with a
    fixed seed, both reported."""
    index = _build_record_index(db_path, dossier_id, process_graphs)
    all_graph_ids = {pg.graph_id for pg in process_graphs}

    cases: list[EvalCase] = []
    used: set[str] = set()
    counts: dict[str, Any] = {}

    def _add(group: str, label: str, graph_ids: list[str], seeded: bool) -> None:
        added = 0
        for gid in graph_ids:
            if gid in used:
                continue
            used.add(gid)
            cases.append(EvalCase(graph_id=gid, group=group, label=label, seeded=seeded))
            added += 1
        counts[f"{group}:{label}"] = added

    _add("F1", "shell vendor 209101", _graphs_by_entity(process_graphs, "vendor:209101"), True)
    for asset_id in _F2_ASSET_IDS:
        _add("F2", f"asset {asset_id}", _graphs_by_entity(process_graphs, f"asset:{asset_id}"), True)
    _add("F3", "Jan-2026 invoice, Dec-2025 delivery", _graphs_by_source_file(index, _F3_SOURCE_FILE), True)
    _add("F4", "split payments, document SAMMEL-200007", _graphs_by_document(index, "SAMMEL-200007"), True)

    _add("D1", "480k production-line investment (doc ER901435)", _graphs_by_document(index, "ER901435"), False)
    _add("D2", "vendor 209110", _graphs_by_entity(process_graphs, "vendor:209110"), False)
    _add("D2", "vendor 209111", _graphs_by_entity(process_graphs, "vendor:209111"), False)
    _add("D3", "vendor 209112 (honest twin of F1)", _graphs_by_entity(process_graphs, "vendor:209112"), False)

    d4_all = sorted(set(_graphs_by_account_journal_entries(index, "440020")) - used)
    d4_sample = sorted(random.Random(_D4_SAMPLE_SEED).sample(d4_all, k=min(_D4_SAMPLE_SIZE, len(d4_all))))
    _add(
        "D4",
        f"volume-bonus account 440020 ({len(d4_sample)} of {len(d4_all)} entries sampled, seed={_D4_SAMPLE_SEED})",
        d4_sample,
        False,
    )
    counts["D4:total_candidates"] = len(d4_all)

    _add("D5", "vendor 209113 (disclosed related-party charge)", _graphs_by_entity(process_graphs, "vendor:209113"), False)
    _add("D6", "asset 040000-000005 disposal", _graphs_by_entity(process_graphs, "asset:040000-000005"), False)
    _add(
        "D7",
        "invoice AR502040 / credit note SG502041",
        _graphs_by_document(index, "AR502040") + _graphs_by_document(index, "SG502041"),
        False,
    )

    remaining = sorted(all_graph_ids - used)
    control_sample = sorted(
        random.Random(_CONTROL_SAMPLE_SEED).sample(remaining, k=min(_CONTROL_SAMPLE_SIZE, len(remaining)))
    )
    _add(
        "CONTROL",
        f"ordinary entry ({len(control_sample)} of {len(remaining)} eligible sampled, seed={_CONTROL_SAMPLE_SEED})",
        control_sample,
        False,
    )
    counts["CONTROL:total_eligible"] = len(remaining)

    return cases, counts


# ---------------------------------------------------------------------------
# The live calls themselves - real render_entry_brief, real analyze_entry.
# ---------------------------------------------------------------------------


@dataclass
class CallResult:
    case: EvalCase
    run_index: int
    proposals: list[ProposedFinding] = field(default_factory=list)
    error: str | None = None
    brief_excerpt: str = ""


def _brief_diagnostic_excerpt(brief: str) -> str:
    """The sections most relevant to diagnosing a miss - agents/PROMPTS.md
    SS2 directs the analyst to observe documents present/absent and who
    appears in which role, so these are the sections a miss diagnosis reads
    first. Kept alongside every result so a missed seeded finding can be
    diagnosed from what the model actually had in front of it, without
    re-rendering anything."""
    sections = brief.split("\n\n")
    wanted = [s for s in sections if s.startswith(("Entry ", "Not present", "Parties"))]
    return "\n\n".join(wanted)


def _run_one_call(
    case: EvalCase,
    run_index: int,
    settings: AgentSettings,
    dossier_id: str,
    db_path: Path,
    profile: DossierProfile,
    graph: Any,
    process_graphs: list[ProcessGraph],
    thread_state: threading.local,
) -> CallResult:
    model = getattr(thread_state, "model", None)
    if model is None:
        model = _build_analyst_model(settings)
        thread_state.model = model

    brief = real_render_entry_brief(
        dossier_id, db_path, case.graph_id, profile, graph=graph, process_graphs=process_graphs
    )
    excerpt = _brief_diagnostic_excerpt(brief)
    try:
        proposals = real_analyze_entry(model, brief)
        return CallResult(case=case, run_index=run_index, proposals=proposals, brief_excerpt=excerpt)
    except Exception as exc:  # noqa: BLE001 - isolate per-call, report the error string
        return CallResult(case=case, run_index=run_index, error=str(exc), brief_excerpt=excerpt)


# ---------------------------------------------------------------------------
# The test.
# ---------------------------------------------------------------------------


def test_live_ground_truth_evaluation(
    sample_saved_db_path: Path,
    sample_graph,
    sample_process_graphs,
    sample_profile,
    live_agent_settings: AgentSettings,
):
    cases, sample_counts = build_cases(sample_process_graphs, sample_saved_db_path, SAMPLE_DOSSIER_ID)

    planned_calls = len(cases) * NUM_RUNS
    assert planned_calls <= CALL_CEILING, (
        f"planned {planned_calls} model calls ({len(cases)} cases x {NUM_RUNS} runs) exceeds the "
        f"hard ceiling of {CALL_CEILING} - refusing to spend anything. Case-resolution logic "
        f"probably matched far more entries than intended; fix it before raising this ceiling."
    )

    thread_state = threading.local()
    results: list[CallResult] = []
    start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=_CONCURRENCY) as executor:
        futures = [
            executor.submit(
                _run_one_call,
                case,
                run_index,
                live_agent_settings,
                SAMPLE_DOSSIER_ID,
                sample_saved_db_path,
                sample_profile,
                sample_graph,
                sample_process_graphs,
                thread_state,
            )
            for run_index in range(NUM_RUNS)
            for case in cases
        ]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
    elapsed = time.perf_counter() - start

    actual_calls = len(results)
    failure_count = sum(1 for r in results if r.error is not None)

    report_path = Path(__file__).resolve().parent.parent.parent / "agents" / "T4_GROUND_TRUTH_EVAL_REPORT.md"
    report_text = _render_report(cases, results, sample_counts, actual_calls, failure_count, elapsed)
    report_path.write_text(report_text, encoding="utf-8")

    print(f"\n{actual_calls} model calls in {elapsed:.1f}s ({failure_count} failed) - report: {report_path}")

    # Structural sanity only - never a threshold on recall/precision, which
    # would be exactly the "tune the thing being evaluated" agents/PLAN.md
    # forbids. A systemic failure (every call erroring) means the run
    # measured nothing and must be visible as a hard failure, not a quiet
    # empty report.
    assert actual_calls == planned_calls
    assert failure_count < actual_calls, "every call failed - this run measured nothing, see errors in the report"


# ---------------------------------------------------------------------------
# Reporting.
# ---------------------------------------------------------------------------


def _case_key(case: EvalCase) -> tuple[str, str]:
    return (case.group, case.graph_id)


def _render_report(
    cases: list[EvalCase],
    results: list[CallResult],
    sample_counts: dict[str, Any],
    actual_calls: int,
    failure_count: int,
    elapsed: float,
) -> str:
    by_case: dict[tuple[str, str], list[CallResult]] = defaultdict(list)
    for r in results:
        by_case[_case_key(r.case)].append(r)

    lines: list[str] = []
    lines.append("# T4 - Live ground-truth evaluation of the single-call analyst")
    lines.append("")
    lines.append(
        f"Model: `{ANALYST_MODEL}`. {len(cases)} cases x {NUM_RUNS} runs = {actual_calls} model calls "
        f"in {elapsed:.1f}s wall clock ({failure_count} call(s) failed and are marked as such below, "
        f"never scored as 'no finding')."
    )
    lines.append("")
    lines.append("Matching criterion: see this module's docstring. In short, an entry-scoped call means a")
    lines.append("proposal here can only cite records belonging to this entry, so for F1-F4 'proposed anything'")
    lines.append("and 'proposed something citing the seeded finding's own records' are the same event.")
    lines.append("")

    for group in ("F1", "F2", "F3", "F4", "D1", "D2", "D3", "D4", "D5", "D6", "D7", "CONTROL"):
        group_cases = [c for c in cases if c.group == group]
        if not group_cases:
            continue
        seeded = group_cases[0].seeded
        lines.append(f"## {group}{' (seeded finding)' if seeded else ' (decoy)' if group != 'CONTROL' else ' (control sample)'}")
        lines.append("")
        lines.append("| entry | label | proposed (of 3 runs) | severities/titles seen |")
        lines.append("|---|---|---|---|")
        for case in group_cases:
            case_results = by_case[_case_key(case)]
            proposed_runs = sum(1 for r in case_results if r.error is None and r.proposals)
            failed_runs = sum(1 for r in case_results if r.error is not None)
            titles = []
            for r in case_results:
                for p in r.proposals:
                    titles.append(f"{p.severity}: {p.title}")
            titles_text = "; ".join(sorted(set(titles))) or "(none)"
            failed_note = f", {failed_runs} call(s) failed" if failed_runs else ""
            lines.append(f"| {case.graph_id} | {case.label} | {proposed_runs}/{len(case_results)}{failed_note} | {titles_text} |")
        lines.append("")

    lines.append("## Sample counts")
    lines.append("")
    for key, value in sample_counts.items():
        lines.append(f"- {key}: {value}")
    lines.append("")

    lines.append("## Diagnosis of every seeded-finding entry with fewer than 3/3 proposals")
    lines.append("")
    any_miss = False
    for case in cases:
        if not case.seeded:
            continue
        case_results = by_case[_case_key(case)]
        proposed_runs = sum(1 for r in case_results if r.error is None and r.proposals)
        if proposed_runs == len(case_results):
            continue
        any_miss = True
        lines.append(f"### {case.group} - {case.label} - entry `{case.graph_id}` ({proposed_runs}/{len(case_results)} runs proposed anything)")
        lines.append("")
        sample_excerpt = next((r.brief_excerpt for r in case_results if r.brief_excerpt), "")
        lines.append("Relevant brief excerpt (Entry/Not present/Parties sections):")
        lines.append("")
        lines.append("```")
        lines.append(sample_excerpt[:4000])
        lines.append("```")
        lines.append("")
        lines.append(
            "_Diagnosis: fill in by hand which of agents/PROMPTS.md SS2's observation directions "
            "(dates, documents present/absent, roles, amounts, classification, text) should have "
            "surfaced this from the excerpt above, and whether the brief actually carried the fact "
            "needed._"
        )
        lines.append("")
    if not any_miss:
        lines.append("Every seeded-finding entry was proposed on all 3 runs.")
        lines.append("")

    return "\n".join(lines)
