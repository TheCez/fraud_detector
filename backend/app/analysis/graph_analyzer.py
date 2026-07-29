"""Graph-traversing, evidence-gated fraud-analysis workflow.

The project owner chose LLM traversal over deterministic rule-matching: build
graphs of the data's relationships, then have the agent walk each one and,
using the model's own knowledge plus a red-flag briefing, find discrepancies
worth a human's attention. Two things make that affordable and safe:

1. ``app.analysis.prefilter`` decides *whether to look* - cheaply and
   deterministically - before any model call happens. It is deliberately
   generous; judgement is the model's job, never the filter's.
2. Every proposal the model makes is rehydrated from
   ``app.evidence.EvidenceRecordStore`` before it becomes a ``Finding``.
   ``ProposedFinding`` has no evidence field, so the model structurally cannot
   express evidence text - it cites ``record_ids`` only, and a proposal is
   discarded whole if any cited id fails to resolve against this dossier's
   authoritative SQLite records. This is the same trust boundary the previous
   cloud-based analyzer enforced; it carries over unchanged.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any, TypedDict

from pydantic import BaseModel, Field

from app.analysis.errors import GraphUnavailableError
from app.analysis.prefilter import Candidate, select_candidate_graphs
from app.core.settings import AgentSettings
from app.evidence import EvidenceRecordStore
from app.graph.store import load_process_graphs
from app.graph.tools import (
    absence_check,
    get_subgraph,
    list_process_graphs,
    neighbors,
    path_between,
    records_for_node,
)
from app.models.schemas import Evidence, Finding, FindingStatus, Severity, SourceLocation

logger = logging.getLogger(__name__)

# Maximum tool calls a single process-graph traversal may make. Enforced in
# code by _build_traversal_graph's routing, not left to the model's judgement -
# the previous implementation was bounded by having no cycles at all; this one
# needs real cycles for traversal, so the bound has to be explicit.
DEFAULT_STEP_BUDGET = 6

_SYSTEM_PROMPT = """You are an audit analysis assistant. You investigate one \
process graph at a time - a small cluster of related accounting records from a \
German GDPdU/GoBD audit dossier - and decide whether anything in it warrants \
human review.

Red flags worth investigating (these are things to look into, not conclusions \
to assume):
- segregation-of-duties violations, e.g. the same user both changed/created \
and approved a master-data record
- round or suspiciously regular amounts on invoices or postings
- a vendor with postings but no matching goods-receipt record
- period cut-off errors: a service or document date falling in a different \
accounting period than the booking date
- repairs or maintenance capitalized as fixed assets instead of expensed
- payments split into several amounts, each kept just below an approval \
threshold

Source data conventions: amounts use comma decimals (e.g. "9.780,00" is nine \
thousand seven hundred eighty), dates are DD.MM.YYYY on the original documents \
(already normalized to ISO 8601 in the evidence you are given), and column \
names are German - BUCHUNGSBETRAG (posting amount), LIEFERANTENKONTONUMMER \
(vendor account number), GEAENDERT_VON (changed by), GENEHMIGT_VON (approved \
by).

You are given the records in this process graph, plus the reason(s) a cheap \
pre-filter flagged it as worth a look - that reason is a hint, not a verdict; \
it may well turn out to be innocent (e.g. a new vendor with real, on-time \
goods receipts and a different approver than creator is normal business, not \
fraud). You may call the provided tools to investigate further: \
list_process_graphs, get_subgraph, neighbors, records_for_node, path_between, \
absence_check. You have a limited number of tool calls - use them \
purposefully rather than exploring exhaustively.

Only cite record_ids you have actually seen, either in the evidence you were \
given or in a tool result. Never invent a record_id, amount, date, person, or \
fact. If nothing in this graph warrants review, propose no findings."""

_FINALIZE_PROMPT = (
    "Based on the investigation above, propose zero or more findings now. Cite "
    "only record_ids you have directly observed."
)


class ProposedFinding(BaseModel):
    """Untrusted model output. Evidence is rebuilt from local records - this
    type has no evidence field, so the model cannot express evidence text."""

    title: str = Field(min_length=8, max_length=180)
    severity: Severity
    category: str = Field(min_length=3, max_length=80)
    explanation: str = Field(min_length=8, max_length=1200)
    reasoning: str = Field(min_length=8, max_length=2400)
    confidence: str = Field(pattern="^(low|medium|high)$")
    record_ids: list[str] = Field(min_length=1, max_length=12)
    amount_at_risk: float | None = None
    currency: str | None = None


class ProposedFindingBatch(BaseModel):
    findings: list[ProposedFinding] = Field(default_factory=list, max_length=20)


class _TraversalState(TypedDict, total=False):
    messages: list[Any]
    tool_calls_used: int
    proposals: list[ProposedFinding]


def _build_tools(dossier_id: str, db_path: Path) -> list[Any]:
    """Bind the T1 tool API (app.graph.tools) to one dossier for the model to call.

    Every function here is already bounded and dossier-scoped; this only
    curries dossier_id/db_path so the model only ever supplies the meaningful
    arguments (node ids, edge types, limits).
    """
    from langchain_core.tools import StructuredTool

    def _list_process_graphs(limit: int = 20, offset: int = 0) -> list[dict]:
        return list_process_graphs(dossier_id, db_path, limit=limit, offset=offset)

    def _get_subgraph(graph_id: str) -> dict | None:
        return get_subgraph(dossier_id, db_path, graph_id)

    def _neighbors(node_id: str, edge_type: str | None = None, limit: int = 20) -> list[dict]:
        return neighbors(dossier_id, db_path, node_id, edge_type=edge_type, limit=limit)

    def _records_for_node(node_id: str, limit: int = 20) -> list[dict]:
        return records_for_node(dossier_id, db_path, node_id, limit=limit)

    def _path_between(source_node_id: str, target_node_id: str, max_len: int = 6) -> dict | None:
        return path_between(dossier_id, db_path, source_node_id, target_node_id, max_len=max_len)

    def _absence_check(node_id: str, expected_edge_type: str) -> dict:
        return absence_check(dossier_id, db_path, node_id, expected_edge_type)

    specs = [
        (
            _list_process_graphs,
            "list_process_graphs",
            "List other process graphs in this dossier (summaries only: graph_id, "
            "record_count, capped/had_cycle flags).",
        ),
        (
            _get_subgraph,
            "get_subgraph",
            "Get the full node/edge payload for one process graph by graph_id.",
        ),
        (
            _neighbors,
            "neighbors",
            "Bounded one-hop neighbors of a node_id, optionally filtered by "
            "edge_type (e.g. 'has_receipt', 'paid_to', 'approved_by').",
        ),
        (
            _records_for_node,
            "records_for_node",
            "Normalized records backing a node_id.",
        ),
        (
            _path_between,
            "path_between",
            "Bounded shortest path between two node_ids.",
        ),
        (
            _absence_check,
            "absence_check",
            "Check whether node_id has an edge of expected_edge_type anywhere in "
            "the dossier - e.g. confirm a vendor has no 'has_receipt' edge.",
        ),
    ]
    return [StructuredTool.from_function(func=fn, name=name, description=desc) for fn, name, desc in specs]


def _build_traversal_graph(tool_model: Any, proposer_model: Any, tools_by_name: dict[str, Any], step_budget: int):
    """Compile the bounded LangGraph workflow for one process-graph traversal.

    ``tool_model``/``proposer_model`` need only expose ``.invoke(messages)`` -
    production code passes real ``ChatOpenAI`` instances (one bound to tools,
    one bound to structured output); tests pass simple stand-ins so the step
    budget and routing can be verified without a live API call.

    The step budget is enforced structurally: ``tools_node`` never executes
    more than ``step_budget`` tool calls in total, and ``route_after_agent``
    forces "finalize" once that count is reached, regardless of how many more
    tool calls the model asks for.
    """
    try:
        from langgraph.graph import END, START, StateGraph
        from langchain_core.messages import HumanMessage, ToolMessage
    except ImportError as exc:
        raise GraphUnavailableError("The optional LangGraph/LangChain dependency is not installed.") from exc

    def agent_node(state: _TraversalState) -> dict:
        ai_message = tool_model.invoke(state["messages"])
        return {"messages": state["messages"] + [ai_message]}

    def tools_node(state: _TraversalState) -> dict:
        messages = list(state["messages"])
        ai_message = messages[-1]
        tool_calls = getattr(ai_message, "tool_calls", None) or []
        used = state.get("tool_calls_used", 0)

        for call in tool_calls:
            if used >= step_budget:
                messages.append(
                    ToolMessage(
                        content="Step budget reached; no further tool calls will be executed.",
                        tool_call_id=call["id"],
                    )
                )
                continue
            tool = tools_by_name.get(call["name"])
            if tool is None:
                messages.append(ToolMessage(content=f"Unknown tool: {call['name']}", tool_call_id=call["id"]))
                continue
            try:
                result = tool.invoke(call.get("args", {}))
            except Exception as exc:  # tool errors are evidence for the model, not a crash
                result = {"error": str(exc)}
            messages.append(
                ToolMessage(
                    content=json.dumps(result, ensure_ascii=False, default=str)[:4000],
                    tool_call_id=call["id"],
                )
            )
            used += 1

        return {"messages": messages, "tool_calls_used": used}

    def finalize_node(state: _TraversalState) -> dict:
        messages = state["messages"] + [HumanMessage(content=_FINALIZE_PROMPT)]
        result = proposer_model.invoke(messages)
        proposals = result.findings if isinstance(result, ProposedFindingBatch) else []
        return {"proposals": proposals}

    def route_after_agent(state: _TraversalState) -> str:
        ai_message = state["messages"][-1]
        tool_calls = getattr(ai_message, "tool_calls", None) or []
        if not tool_calls or state.get("tool_calls_used", 0) >= step_budget:
            return "finalize"
        return "tools"

    builder = StateGraph(_TraversalState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", tools_node)
    builder.add_node("finalize", finalize_node)
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", route_after_agent, {"tools": "tools", "finalize": "finalize"})
    builder.add_edge("tools", "agent")
    builder.add_edge("finalize", END)
    return builder.compile()


class GraphAnalyzer:
    """Selects worthwhile process graphs and walks each with a bounded agent.

    After ``analyze()`` returns (or raises), these attributes describe what
    happened - ``runner.py`` reads them to log and record a cap-hit run
    rather than silently presenting a partial analysis as complete:

    - ``total_process_graphs``: every process graph the dossier has.
    - ``candidate_graphs``: how many the pre-filter selected.
    - ``analyzed_graphs``: how many actually received a model call (may be
      less than ``candidate_graphs`` if the per-run cap was hit).
    - ``model_call_cap_hit`` / ``cap_message``: set when the cap truncated
      the candidate list.
    """

    def __init__(self, settings: AgentSettings, *, step_budget: int = DEFAULT_STEP_BUDGET) -> None:
        self.settings = settings
        self.step_budget = step_budget
        self.total_process_graphs = 0
        self.candidate_graphs = 0
        self.analyzed_graphs = 0
        self.model_call_cap_hit = False
        self.cap_message: str | None = None

    def analyze(self, dossier_id: str, db_path: Path) -> list[Finding]:
        if not self.settings.is_configured:
            raise GraphUnavailableError("Agent analysis is disabled or not configured.")

        candidates = select_candidate_graphs(dossier_id, db_path)
        self.total_process_graphs = len(load_process_graphs(db_path, dossier_id))
        self.candidate_graphs = len(candidates)

        selected = candidates
        cap = self.settings.model_call_cap
        if len(candidates) > cap:
            self.model_call_cap_hit = True
            dropped_priority = candidates[cap].priority
            self.cap_message = (
                f"Model-call cap ({cap}) reached: {len(candidates)} of "
                f"{self.total_process_graphs} process graphs passed the pre-filter "
                f"for dossier {dossier_id}; the {cap} highest-priority candidates "
                f"were analyzed. Findings from the remaining {len(candidates) - cap} "
                f"candidate graph(s) are not included - those carry weaker signals "
                f"(the strongest dropped candidate scores {dropped_priority}). "
                f"Re-run with a higher FRAUD_AGENT_MODEL_CALL_CAP for full coverage."
            )
            logger.warning(self.cap_message)
            selected = candidates[:cap]
        self.analyzed_graphs = len(selected)

        if not selected:
            return []

        tools = _build_tools(dossier_id, db_path)
        tools_by_name = {tool.name: tool for tool in tools}
        tool_model, proposer_model = self._build_models(tools)
        workflow = _build_traversal_graph(tool_model, proposer_model, tools_by_name, self.step_budget)

        store = EvidenceRecordStore(dossier_id, db_path)
        findings: list[Finding] = []
        for candidate in selected:
            proposals = self._investigate(workflow, store, candidate)
            findings.extend(
                self._validate_and_build_findings(dossier_id, store, candidate.graph.graph_id, proposals)
            )
        return findings

    def _build_models(self, tools: list[Any]) -> tuple[Any, Any]:
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:
            raise GraphUnavailableError("The optional LangChain OpenAI dependency is not installed.") from exc

        model = ChatOpenAI(model=self.settings.openai_model, api_key=self.settings.openai_api_key, temperature=0)
        return model.bind_tools(tools), model.with_structured_output(ProposedFindingBatch)

    def _investigate(self, workflow: Any, store: EvidenceRecordStore, candidate: Candidate) -> list[ProposedFinding]:
        try:
            from langgraph.errors import GraphRecursionError
        except ImportError as exc:
            raise GraphUnavailableError("The optional LangGraph dependency is not installed.") from exc

        records = store.resolve(list(candidate.graph.record_ids))
        evidence_context = [store.evidence_context(record) for record in records]
        initial_message = {
            "role": "user",
            "content": json.dumps(
                {
                    "graph_id": candidate.graph.graph_id,
                    "pre_filter_reasons": list(candidate.reasons),
                    "records": evidence_context,
                },
                ensure_ascii=False,
                default=str,
            ),
        }
        messages = [{"role": "system", "content": _SYSTEM_PROMPT}, initial_message]

        try:
            result = workflow.invoke(
                {"messages": messages, "tool_calls_used": 0},
                config={"recursion_limit": self.step_budget * 3 + 10},
            )
        except GraphRecursionError as exc:
            raise GraphUnavailableError(
                f"Traversal of process graph {candidate.graph.graph_id} exceeded its step budget."
            ) from exc
        return result.get("proposals", [])

    @staticmethod
    def _validate_and_build_findings(
        dossier_id: str,
        store: EvidenceRecordStore,
        graph_id: str,
        proposals: list[ProposedFinding],
    ) -> list[Finding]:
        findings: list[Finding] = []
        for proposal in proposals:
            records = store.resolve(proposal.record_ids)
            if len(records) != len(set(proposal.record_ids)):
                continue
            finding_id = (
                f"AI-{uuid.uuid5(uuid.NAMESPACE_URL, dossier_id + '|' + '|'.join(sorted(proposal.record_ids))).hex[:12]}"
            )
            evidence = [
                GraphAnalyzer._evidence_from_record(finding_id, index + 1, record)
                for index, record in enumerate(records)
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

    @staticmethod
    def _evidence_from_record(finding_id: str, number: int, record: dict[str, Any]) -> Evidence:
        normalized = json.loads(record["data_json"])
        source = normalized["source"]
        raw_data = normalized.get("data", {})
        excerpt = json.dumps(raw_data, ensure_ascii=False, default=str)[:600]
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
