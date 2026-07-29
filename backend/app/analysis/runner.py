"""Background analysis orchestration with explicit degraded behavior."""

from __future__ import annotations

from pathlib import Path

from app.analysis.agent_analyzer import AgentAnalyzer
from app.analysis.demo_analyzer import DemoAnalyzer
from app.analysis.graph import CogneeCloudGraph, GraphUnavailableError
from app.core.settings import AgentSettings
from app.persistence import (
    complete_analysis_run,
    create_analysis_run,
    init_analysis_tables,
    init_findings_table,
    insert_findings,
    get_graph_ingestion,
    save_graph_ingestion,
    update_dossier_status,
)


def run_analysis(dossier_id: str, workspace_root: Path, db_path: Path) -> None:
    """Run after normalization. Never expose cloud/provider failure as a false report."""

    settings = AgentSettings.from_environment()
    init_analysis_tables(db_path)
    init_findings_table(db_path)
    run_id = create_analysis_run(db_path, dossier_id, "agent" if settings.agent_enabled else "demo")

    graph: CogneeCloudGraph | None = None
    dataset_name: str | None = None
    current_hash: str | None = None
    try:
        if settings.is_configured:
            jsonl_path = workspace_root / "normalized" / "all_records.jsonl"
            graph = CogneeCloudGraph(settings)
            current_hash = graph.normalized_sha256(jsonl_path)
            ingestion = get_graph_ingestion(db_path, dossier_id)
            if (
                not ingestion
                or ingestion["normalized_sha256"] != current_hash
                or ingestion["status"] != "complete"
            ):
                result = graph.ingest(dossier_id, jsonl_path)
                save_graph_ingestion(
                    db_path, dossier_id, result.dataset_name, result.normalized_sha256, "complete"
                )
                dataset_name = result.dataset_name
            else:
                dataset_name = ingestion["dataset_name"]
            findings = AgentAnalyzer(settings).analyze(dossier_id, db_path)
            mode = "agent"
        elif settings.agent_enabled:
            raise GraphUnavailableError("Agent analysis is enabled but Cognee Cloud or OpenAI is not configured.")
        else:
            findings = DemoAnalyzer().analyze(dossier_id, db_path)
            mode = "demo"

        insert_findings(db_path, dossier_id, [finding.model_dump(mode="json") for finding in findings])
        if graph is not None and dataset_name is not None and current_hash is not None:
            graph.forget_dataset(dataset_name)
            save_graph_ingestion(db_path, dossier_id, dataset_name, current_hash, "deleted")
        complete_analysis_run(db_path, run_id, "complete", mode=mode)
        update_dossier_status(db_path, dossier_id, "complete", finding_count=len(findings))
    except GraphUnavailableError as exc:
        complete_analysis_run(db_path, run_id, "unavailable", error=str(exc))
        update_dossier_status(db_path, dossier_id, "analysis_incomplete")
    except Exception:
        complete_analysis_run(db_path, run_id, "error", error="Analysis failed. Retry the dossier analysis.")
        update_dossier_status(db_path, dossier_id, "analysis_incomplete")
