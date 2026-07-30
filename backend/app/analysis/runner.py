"""Background analysis orchestration with explicit degraded behavior."""

from __future__ import annotations

import logging
from pathlib import Path

from app.analysis.demo_analyzer import DemoAnalyzer
from app.analysis.errors import GraphUnavailableError
from app.analysis.graph_analyzer import GraphAnalyzer
from app.core.settings import AgentSettings
from app.graph.builder import build_graph
from app.graph.store import save_graph
from app.graph.subgraphs import build_process_graphs
from app.persistence import (
    complete_analysis_run,
    create_analysis_run,
    get_record_count,
    init_analysis_tables,
    init_findings_table,
    insert_findings,
    update_dossier_status,
)

logger = logging.getLogger(__name__)


def run_analysis(dossier_id: str, workspace_root: Path, db_path: Path) -> None:
    """Run after normalization. Never expose a model/graph failure as a false report.

    Builds and persists the local process-graph engine's output whenever
    normalization produced records - regardless of which analyzer runs. The
    graph backs the upcoming UI rendering and chat agent, so it must exist
    even when the model happens not to be configured (the default, deterministic
    ``DemoAnalyzer`` path).

    ``workspace_root`` is accepted but unused: the graph builder and every
    analyzer read exclusively from the dossier-scoped SQLite tables now, not
    from workspace files. It stays in the signature because ``api/routes.py``
    calls this function positionally and is outside this change's scope.
    """

    settings = AgentSettings.from_environment()
    init_analysis_tables(db_path)
    init_findings_table(db_path)
    run_id = create_analysis_run(db_path, dossier_id, "agent" if settings.agent_enabled else "demo")

    try:
        graph = None
        process_graphs = None
        if get_record_count(db_path, dossier_id) > 0:
            graph = build_graph(dossier_id, db_path)
            process_graphs = build_process_graphs(dossier_id, graph)
            save_graph(db_path, dossier_id, graph, process_graphs)

        graph_analyzer: GraphAnalyzer | None = None
        if settings.is_configured:
            # Pass the graph just built (and persisted) above so the analyzer
            # does not immediately read the whole thing back out of SQLite.
            # GraphAnalyzer falls back to loading it itself when record_count
            # was 0 above and graph/process_graphs are still None.
            graph_analyzer = GraphAnalyzer(settings, graph=graph, process_graphs=process_graphs)
            findings = graph_analyzer.analyze(dossier_id, db_path)
            mode = "agent"
        elif settings.agent_enabled:
            raise GraphUnavailableError(
                "Agent analysis is enabled but not configured (OPENAI_API_KEY is missing)."
            )
        else:
            findings = DemoAnalyzer().analyze(dossier_id, db_path)
            mode = "demo"

        insert_findings(db_path, dossier_id, [finding.model_dump(mode="json") for finding in findings])

        cap_message = graph_analyzer.cap_message if graph_analyzer and graph_analyzer.model_call_cap_hit else None
        complete_analysis_run(db_path, run_id, "complete", mode=mode, error=cap_message)
        update_dossier_status(db_path, dossier_id, "complete", finding_count=len(findings))
    except GraphUnavailableError as exc:
        complete_analysis_run(db_path, run_id, "unavailable", error=str(exc))
        update_dossier_status(db_path, dossier_id, "analysis_incomplete")
    except Exception:
        logger.exception("Analysis failed for dossier %s", dossier_id)
        complete_analysis_run(db_path, run_id, "error", error="Analysis failed. Retry the dossier analysis.")
        update_dossier_status(db_path, dossier_id, "analysis_incomplete")
