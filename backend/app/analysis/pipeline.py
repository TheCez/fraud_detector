"""The analysis pipeline - implements `app.analysis.interface.Analyzer`.

Replaces the deleted `graph_analyzer.py` (bounded LangGraph traversal over a
cheap pre-filter's candidates) with the shape `agents/PLAN.md`'s Slice 7
architectural decision requires: the graph assembles one ledger entry's
complete context up front (`app/analysis/profile.py`, `entry_brief.py` - T5),
and the model is called exactly once per entry
(`app/analysis/analyst.py` - T6) to judge it. There is no traversal here and
nothing is filtered out before a model ever sees it.

For this task the pipeline is one stage - build the profile once, rank
entries, then per entry: render the brief, call the analyst, rehydrate and
validate. T7 (a data-quality gate) inserts before the brief is rendered for
an entry, and T8 (a refutation-biased verifier) inserts after a proposal is
validated - both slot into `_analyze_one_entry`'s body, which is why it stays
one linear per-entry function today rather than a pre-built chain of stages
that do not exist yet.

Ordering, cap, and coverage. `prefilter.py`'s ranking died with it - there is
no candidate list to rank, every entry is analysed unless the per-run model-
call cap forces a choice. `_rank_entries_for_analysis` below decides only the
*order* entries are analysed in, derived entirely from `profile.py`'s already-
computed facts (an entry's amount relative to the dossier's own quantiles,
how rare its record-type shape is, how many identity dimensions or peer-
carried companion edges it lacks) so that a capped run drops the least
material entries rather than an arbitrary slice of a uuid5-ordered list. This
function must never exclude an entry - it returns every entry the dossier
has, in an order - must never assert anything about fraud, and must never
assign a severity. `analyze()` is the only place that turns "ranked lowest"
into "not analysed this run", and it always records that as an explicit cap
hit, never a silent drop.

The trust boundary, per-entry failure isolation, the model-call cap, and
deterministic finding ids all carry over from `graph_analyzer.py` unchanged
in behaviour - see `_build_findings_from_proposals`, `_is_systemic_error`,
and `_analyze_concurrently`'s docstring below for what each preserves and
why.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import networkx as nx

from app.analysis.analyst import ProposedFinding, ProposedFindingBatch, analyze_entry
from app.analysis.entry_brief import render_entry_brief
from app.analysis.errors import GraphUnavailableError
from app.analysis.profile import COMPLETENESS_DIMENSIONS, DossierProfile, build_profile
from app.core.settings import AgentSettings
from app.evidence import EvidenceRecordStore
from app.graph.store import load_graph, load_process_graphs
from app.graph.subgraphs import ProcessGraph
from app.models.schemas import Evidence, Finding, FindingStatus, SourceLocation
from app.persistence.database import iter_records_by_dossier

logger = logging.getLogger(__name__)

# Log a progress line every N completed entries, so a long run (thousands of
# entries, each one model call) is observable rather than silent until it
# finishes or times out.
_PROGRESS_LOG_INTERVAL = 25

# Evidence excerpts are a debugging/traceability aid, not the finding itself -
# capped so one oversized record field can never blow up a Finding's payload.
# Named (see this module's docstring on why entry_brief.py's own section
# budgets are named the same way) so the fraud-scenario guard can allowlist it
# by name instead of mistaking a structural cap for a domain threshold.
EVIDENCE_EXCERPT_MAX_CHARS = 600


def _is_systemic_error(exc: BaseException) -> bool:
    """True for a failure that affects every entry identically, not just the
    one that happened to surface it first - carried over unchanged from
    `graph_analyzer.py`'s function of the same name and purpose.

    ``GraphUnavailableError`` here can only come from ``_build_analyst_model``
    (a missing optional dependency) - a configuration problem, not a per-entry
    one. An authentication/permission error from the model provider is the
    same: the credentials are bad for every request, not just this one.
    Everything else (evidence rehydration failing, the model producing
    something odd) is specific to the entry that hit it.
    """
    if isinstance(exc, GraphUnavailableError):
        return True
    try:
        from openai import AuthenticationError, PermissionDeniedError
    except ImportError:
        return False
    return isinstance(exc, (AuthenticationError, PermissionDeniedError))


def _entry_amount_scores(db_path: Path, dossier_id: str, process_graphs: list[ProcessGraph]) -> dict[str, float]:
    """One entry -> the largest absolute amount any of its own records carry.

    A single streamed pass over every normalized record (same technique
    `profile.py`'s own pass 1 uses), separate from `build_profile` because
    `DossierProfile` records amounts per entity and dossier-wide, never per
    entry - this is the one per-entry number the ranking below needs that
    `profile.py` does not already expose.
    """
    record_to_graph: dict[str, str] = {}
    for process_graph in process_graphs:
        for record_id in process_graph.record_ids:
            record_to_graph[record_id] = process_graph.graph_id

    scores: dict[str, float] = {}
    for row in iter_records_by_dossier(db_path, dossier_id):
        amount = row["amount"]
        if amount is None:
            continue
        graph_id = record_to_graph.get(row["record_id"])
        if graph_id is None:
            continue
        current = scores.get(graph_id, 0.0)
        scores[graph_id] = max(current, abs(amount))
    return scores


def _amount_materiality_rank(profile: DossierProfile, amount: float) -> int:
    """How many of the dossier's own amount-quantile breakpoints this
    entry's amount meets or exceeds - a coarse, dossier-relative bucket, not
    a judgement about any one amount."""
    return sum(1 for threshold in profile.amount_quantiles.values() if amount >= threshold)


def _entry_absence_count(profile: DossierProfile, graph_id: str) -> int:
    """How many identity dimensions this entry lacks, plus how many companion
    edge types its shape's peers carry that this entry does not - both
    counts, never which ones or what they would mean."""
    count = 0
    completeness = profile.entry_completeness.get(graph_id)
    if completeness is not None:
        count += sum(
            0 if getattr(completeness, f"has_{dimension}") else 1 for dimension in COMPLETENESS_DIMENSIONS
        )

    shape = profile.entry_shape.get(graph_id, ())
    shape_profile = profile.shapes.get(shape)
    if shape_profile is not None:
        this_entry_edge_types = set(profile.entry_edge_types.get(graph_id, ()))
        count += sum(
            1
            for edge_type, edge_count in shape_profile.edge_type_counts.items()
            if edge_count > 0 and edge_type not in this_entry_edge_types
        )
    return count


def rank_entries_for_analysis(
    profile: DossierProfile,
    process_graphs: list[ProcessGraph],
    amount_scores: dict[str, float],
) -> list[str]:
    """Every entry in the dossier, ordered most-material-to-analyse-first.

    Deciding *whether* to analyse an entry is not this function's job - it
    always returns every ``graph_id`` the dossier has, exactly once. Only the
    *order* is decided, from three purely descriptive signals: this entry's
    amount relative to the dossier's own quantiles, how rare its record-type
    shape is among this dossier's other entries, and how many identity
    dimensions or peer-carried companion edges it lacks. None of the three is
    a fraud judgement and none assigns a severity - see this module's
    docstring.
    """
    shapes = profile.shapes

    def _priority_key(graph_id: str) -> tuple[int, int, int, str]:
        amount_rank = _amount_materiality_rank(profile, amount_scores.get(graph_id, 0.0))
        shape = profile.entry_shape.get(graph_id, ())
        shape_profile = shapes.get(shape)
        shape_rarity = shape_profile.entry_count if shape_profile is not None else 0
        absence_count = _entry_absence_count(profile, graph_id)
        # Ascending sort: a larger amount rank and a larger absence count
        # should sort first, so both are negated; a rarer shape (smaller
        # entry_count) should also sort first, so it is left as-is.
        return (-amount_rank, shape_rarity, -absence_count, graph_id)

    return sorted((pg.graph_id for pg in process_graphs), key=_priority_key)


def _build_analyst_model(settings: AgentSettings) -> Any:
    """One ``ChatOpenAI`` bound with ``with_structured_output`` and nothing
    else - no ``bind_tools`` call anywhere in this function, matching the
    architectural decision that the analyst does not traverse."""
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise GraphUnavailableError("The optional LangChain OpenAI dependency is not installed.") from exc

    model = ChatOpenAI(
        model=settings.analyst_model,
        api_key=settings.openai_api_key,
        temperature=0,
        # Explicit rather than relying on langchain_openai's own default: with
        # several concurrent workers, 429s are expected, and the underlying
        # OpenAI client already retries 429/5xx with backoff - this only pins
        # the retry count instead of leaving it implicit.
        max_retries=3,
    )
    return model.with_structured_output(ProposedFindingBatch)


class AnalysisPipeline:
    """Builds the dossier profile once, ranks entries, and analyses each with
    one analyst call. Implements ``app.analysis.interface.Analyzer``.

    After ``analyze()`` returns (or raises), these attributes describe what
    happened - ``runner.py`` reads them to record a cap-hit run rather than
    silently presenting a partial analysis as complete:

    - ``total_entries``: every ledger entry (process graph) in the dossier.
    - ``analyzed_entries``: how many actually received a model call (less
      than ``total_entries`` only when the per-run cap truncated the ranked
      order).
    - ``model_call_cap_hit`` / ``cap_message``: set when the cap truncated.
    """

    def __init__(
        self,
        settings: AgentSettings,
        *,
        graph: nx.MultiDiGraph | None = None,
        process_graphs: list[ProcessGraph] | None = None,
    ) -> None:
        self.settings = settings
        # Optionally supplied by the caller (runner.py, which already builds
        # the graph before persisting it) so analyze() need not read it back
        # out of SQLite. Falls back to a fresh load when not supplied.
        self._graph = graph
        self._process_graphs = process_graphs
        self.total_entries = 0
        self.analyzed_entries = 0
        self.model_call_cap_hit = False
        self.cap_message: str | None = None

    def analyze(self, dossier_id: str, db_path: Path) -> list[Finding]:
        if not self.settings.is_configured:
            raise GraphUnavailableError("Agent analysis is disabled or not configured.")

        graph = self._graph if self._graph is not None else load_graph(db_path, dossier_id)
        process_graphs = (
            self._process_graphs if self._process_graphs is not None else load_process_graphs(db_path, dossier_id)
        )
        self.total_entries = len(process_graphs)

        if not process_graphs:
            return []

        profile = build_profile(dossier_id, db_path, graph=graph, process_graphs=process_graphs)
        amount_scores = _entry_amount_scores(db_path, dossier_id, process_graphs)
        ordered_graph_ids = rank_entries_for_analysis(profile, process_graphs, amount_scores)

        cap = self.settings.model_call_cap
        selected = ordered_graph_ids
        if len(ordered_graph_ids) > cap:
            self.model_call_cap_hit = True
            selected = ordered_graph_ids[:cap]
            self.cap_message = (
                f"Model-call cap ({cap}) reached: {len(ordered_graph_ids)} of "
                f"{self.total_entries} ledger entries in dossier {dossier_id} would need a "
                f"model call; the {cap} highest-priority entries (ranked by dossier-relative "
                f"amount, shape rarity, and completeness) were analyzed. Findings from the "
                f"remaining {len(ordered_graph_ids) - cap} entry/entries are not included. "
                f"Re-run with a higher FRAUD_AGENT_MODEL_CALL_CAP for full coverage."
            )
            logger.warning(self.cap_message)
        self.analyzed_entries = len(selected)

        if not selected:
            return []

        return self._analyze_concurrently(dossier_id, db_path, profile, graph, process_graphs, selected)

    def _analyze_concurrently(
        self,
        dossier_id: str,
        db_path: Path,
        profile: DossierProfile,
        graph: nx.MultiDiGraph,
        process_graphs: list[ProcessGraph],
        selected_graph_ids: list[str],
    ) -> list[Finding]:
        """Analyse every selected entry on a bounded thread pool.

        The work is I/O-bound (HTTPS calls to the model provider), so threads
        are the right tool. Each worker thread builds and keeps its own
        ``ChatOpenAI``/structured-output pair (``_thread_model``) rather than
        sharing one across threads, because it is not documented as
        thread-safe and this project does not guess about that; ``store`` is
        safe to share read-only - every underlying call opens its own SQLite
        connection (see ``app/persistence/database.py``).

        Findings must not depend on completion order: this collects every
        entry's findings first and sorts the merged list by ``finding_id``
        (a deterministic uuid5 over the dossier id and sorted record ids -
        see ``_build_findings_from_proposals``) before returning, so a
        concurrent run and a sequential run (``max_workers=1``) produce
        identical output for the same stubbed model.

        A per-entry failure is isolated - logged, counted, the run continues
        - unless it is an authentication/configuration error or every entry
        failed, either of which means the model backend itself is
        unavailable, not that this one entry was odd; that case raises
        ``GraphUnavailableError`` so ``runner.py`` records
        ``analysis_incomplete`` instead of a false "zero findings" report.
        """
        store = EvidenceRecordStore(dossier_id, db_path)
        thread_state = threading.local()

        def _thread_model() -> Any:
            model = getattr(thread_state, "model", None)
            if model is None:
                model = _build_analyst_model(self.settings)
                thread_state.model = model
            return model

        def _run_one(graph_id: str) -> tuple[str, list[Finding] | None, BaseException | None]:
            try:
                model = _thread_model()
                brief = render_entry_brief(
                    dossier_id, db_path, graph_id, profile, graph=graph, process_graphs=process_graphs
                )
                proposals = analyze_entry(model, brief)
                findings = _build_findings_from_proposals(dossier_id, store, graph_id, proposals)
                return graph_id, findings, None
            except BaseException as exc:  # noqa: BLE001 - isolate per-entry, classify below
                return graph_id, None, exc

        max_workers = max(1, min(self.settings.max_workers, len(selected_graph_ids)))
        collected: list[Finding] = []
        failure_count = 0
        systemic_error: BaseException | None = None
        completed = 0

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_run_one, graph_id) for graph_id in selected_graph_ids]
            for future in as_completed(futures):
                graph_id, findings, error = future.result()
                completed += 1
                if completed % _PROGRESS_LOG_INTERVAL == 0 or completed == len(selected_graph_ids):
                    logger.info(
                        "dossier %s: analyzed %d/%d ledger entries",
                        dossier_id,
                        completed,
                        len(selected_graph_ids),
                    )
                if error is not None:
                    failure_count += 1
                    logger.warning(
                        "dossier %s: entry %s failed and was skipped: %s",
                        dossier_id,
                        graph_id,
                        error,
                    )
                    if _is_systemic_error(error) and systemic_error is None:
                        systemic_error = error
                    continue
                collected.extend(findings or [])

        if systemic_error is not None:
            raise GraphUnavailableError(
                f"Analysis for dossier {dossier_id} failed with an authentication/configuration "
                f"error, not a per-entry one: {systemic_error}"
            ) from systemic_error
        if failure_count == len(selected_graph_ids):
            raise GraphUnavailableError(
                f"All {len(selected_graph_ids)} ledger entry analyses failed for dossier "
                f"{dossier_id}; the model backend is unavailable."
            )

        collected.sort(key=lambda finding: finding.finding_id)
        return collected


def _build_findings_from_proposals(
    dossier_id: str,
    store: EvidenceRecordStore,
    graph_id: str,
    proposals: list[ProposedFinding],
) -> list[Finding]:
    """Rehydrate each proposal's evidence from the dossier-scoped store and
    discard the whole proposal if any cited record id fails to resolve -
    carried over unchanged from ``graph_analyzer.py``'s
    ``_validate_and_build_findings``. Model-provided evidence text is never
    trusted: ``ProposedFinding`` has no evidence field, so there is nothing
    here to trust in the first place."""
    findings: list[Finding] = []
    for proposal in proposals:
        records = store.resolve(proposal.record_ids)
        if len(records) != len(set(proposal.record_ids)):
            continue
        finding_id = (
            f"AI-{uuid.uuid5(uuid.NAMESPACE_URL, dossier_id + '|' + '|'.join(sorted(proposal.record_ids))).hex[:12]}"
        )
        evidence = [
            _evidence_from_record(finding_id, index + 1, record) for index, record in enumerate(records)
        ]
        findings.append(
            Finding(
                finding_id=finding_id,
                title=proposal.title,
                severity=proposal.severity,
                category=proposal.category,
                amount_at_risk=proposal.amount_at_risk,
                currency=proposal.currency,
                explanation=proposal.explanation,
                reasoning=proposal.reasoning,
                evidence_count=len(evidence),
                confidence=proposal.confidence,
                status=FindingStatus.needs_review,
                evidence=evidence,
                graph_id=graph_id,
            )
        )
    return findings


def _evidence_from_record(finding_id: str, number: int, record: dict[str, Any]) -> Evidence:
    normalized = json.loads(record["data_json"])
    source = normalized["source"]
    raw_data = normalized.get("data", {})
    excerpt = json.dumps(raw_data, ensure_ascii=False, default=str)[:EVIDENCE_EXCERPT_MAX_CHARS]
    return Evidence(
        evidence_id=f"{finding_id}-E{number}",
        finding_id=finding_id,
        record_id=record["record_id"],
        document_id=record["file_id"],
        label=f"Source {record['record_type']} record",
        excerpt=excerpt,
        source_location=SourceLocation(
            relative_path=source["relative_path"],
            sheet=source.get("sheet"),
            page=source.get("page"),
            row_start=source.get("row_number"),
            row_end=source.get("row_end"),
            columns=source.get("columns"),
            paragraph=source.get("paragraph"),
        ),
        explanation_en="Source record retrieved from the dossier-scoped evidence store.",
    )


__all__ = ["AnalysisPipeline", "EVIDENCE_EXCERPT_MAX_CHARS", "rank_entries_for_analysis"]
