"""Evidence-gated LangGraph fraud-analysis workflow."""

from __future__ import annotations

import json
import uuid
from typing import Any, TypedDict

from pydantic import BaseModel, Field

from app.analysis.graph import CogneeCloudGraph, EvidenceRecordStore, GraphUnavailableError
from app.core.settings import AgentSettings
from app.models.schemas import Evidence, Finding, FindingStatus, Severity, SourceLocation


class ProposedFinding(BaseModel):
    """Untrusted model output. Evidence is rebuilt from local records."""

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


class InvestigationState(TypedDict, total=False):
    query: str
    candidate_ids: list[str]
    evidence_context: list[dict[str, Any]]
    proposals: list[ProposedFinding]


class AgentAnalyzer:
    """Runs a bounded, non-repeating LangGraph workflow over one dossier graph."""

    def __init__(self, settings: AgentSettings) -> None:
        self.settings = settings
        self.graph = CogneeCloudGraph(settings)

    def analyze(self, dossier_id: str, db_path) -> list[Finding]:
        if not self.settings.is_configured:
            raise GraphUnavailableError("Agent analysis is disabled or not configured.")

        store = EvidenceRecordStore(dossier_id, db_path)
        workflow = self._build_workflow(store, dossier_id)
        result = workflow.invoke({
            "query": (
                "Identify unusual accounting relationships that may warrant fraud review. "
                "Return only record identifiers present in the graph."
            )
        })
        return self._validate_and_build_findings(dossier_id, store, result.get("proposals", []))

    def _build_workflow(self, store: EvidenceRecordStore, dossier_id: str):
        try:
            from langgraph.graph import END, START, StateGraph
        except ImportError as exc:
            raise GraphUnavailableError("The optional LangGraph dependency is not installed.") from exc

        def retrieve(state: InvestigationState) -> InvestigationState:
            candidate_ids = self.graph.recall_record_ids(dossier_id, state["query"])
            return {"candidate_ids": candidate_ids}

        def hydrate(state: InvestigationState) -> InvestigationState:
            records = store.resolve(state.get("candidate_ids", []))
            return {"evidence_context": [store.evidence_context(record) for record in records]}

        def assess(state: InvestigationState) -> InvestigationState:
            context = state.get("evidence_context", [])
            if not context:
                return {"proposals": []}
            return {"proposals": self._assess(context)}

        builder = StateGraph(InvestigationState)
        builder.add_node("retrieve", retrieve)
        builder.add_node("hydrate", hydrate)
        builder.add_node("assess", assess)
        builder.add_edge(START, "retrieve")
        builder.add_edge("retrieve", "hydrate")
        builder.add_edge("hydrate", "assess")
        builder.add_edge("assess", END)
        return builder.compile()

    def _assess(self, evidence_context: list[dict[str, Any]]) -> list[ProposedFinding]:
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:
            raise GraphUnavailableError("The optional LangChain OpenAI dependency is not installed.") from exc

        model = ChatOpenAI(
            model=self.settings.openai_model,
            api_key=self.settings.openai_api_key,
            temperature=0,
        ).with_structured_output(ProposedFindingBatch)
        prompt = (
            "You are an audit analysis assistant. Analyze only the supplied normalized evidence. "
            "Propose zero or more observations that warrant review. Do not state any fact that is "
            "not in the evidence. Each proposal must cite only supplied record_ids. Do not fabricate "
            "amounts, dates, people, sources, or excerpts.\n\nEVIDENCE:\n"
            + json.dumps(evidence_context, ensure_ascii=False, default=str)
        )
        result = model.invoke(prompt)
        return result.findings if isinstance(result, ProposedFindingBatch) else []

    @staticmethod
    def _validate_and_build_findings(
        dossier_id: str,
        store: EvidenceRecordStore,
        proposals: list[ProposedFinding],
    ) -> list[Finding]:
        findings: list[Finding] = []
        for proposal in proposals:
            records = store.resolve(proposal.record_ids)
            if len(records) != len(set(proposal.record_ids)):
                continue
            finding_id = f"AI-{uuid.uuid5(uuid.NAMESPACE_URL, dossier_id + '|' + '|'.join(sorted(proposal.record_ids))).hex[:12]}"
            evidence = [
                AgentAnalyzer._evidence_from_record(finding_id, index + 1, record)
                for index, record in enumerate(records)
            ]
            findings.append(Finding(
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
            ))
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
