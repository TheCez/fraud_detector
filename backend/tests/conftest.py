from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app.graph.builder import build_graph
from app.graph.store import save_graph
from app.graph.subgraphs import build_process_graphs
from app.ingestion.manifest import build_manifest
from app.normalization.models import NormalizedRecord
from app.normalization.orchestrator import normalize_dossier

_AGENT_ENV_VARS = (
    "FRAUD_AGENT_ENABLED",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "FRAUD_AGENT_MODEL_CALL_CAP",
)


@pytest.fixture(autouse=True)
def isolated_agent_environment(monkeypatch):
    """Keep the suite deterministic regardless of a developer's local .env.

    settings.py loads .env at import time (override=False), so whatever is on
    disk ends up in os.environ before pytest ever runs. AgentSettings reads
    os.environ lazily at call time, so clearing these here still controls
    behaviour and lets tests opt back in explicitly with monkeypatch.setenv.
    """
    for var in _AGENT_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# Shared real-sample-dossier pipeline, session-scoped.
#
# test_gdpdu_normalization.py, test_graph_engine.py and test_prefilter.py each
# used to rebuild the same extract -> normalize -> build_graph ->
# build_process_graphs -> save_graph pipeline (~50s) with their own
# module-scoped fixtures and their own dossier id - pytest fixtures don't
# share across files, so that ~50s ran three times. Building it here once,
# session-scoped, and having the three modules consume it (see their thin
# `extracted_dir`/`db_path`/`graph`/... wrapper fixtures) cuts the suite back
# to one real run of the pipeline. All three modules now share
# ``SAMPLE_DOSSIER_ID`` so a fixture built under one dossier id is valid
# evidence for a test written against another.
#
# The shared SQLite file is mutable session state: `sample_saved_db_path`
# persists the graph onto `sample_db_path` in place. Every consumer of that
# fixture only reads from it - tests that need to write to a graph db of
# their own use a fresh `tmp_path` file, never this shared one (see e.g.
# test_graph_engine.py's round-trip/idempotency tests).
# ---------------------------------------------------------------------------

SAMPLE_ZIP = Path(__file__).resolve().parent.parent.parent / "sample_data" / "Uebungsdaten_Muster_Verpackungen.zip"

requires_sample_zip = pytest.mark.skipif(not SAMPLE_ZIP.exists(), reason="sample ZIP not available")

SAMPLE_DOSSIER_ID = "sample-dossier"


@pytest.fixture(scope="session")
def sample_extracted_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    target = tmp_path_factory.mktemp("sample_extract")
    with zipfile.ZipFile(SAMPLE_ZIP) as zf:
        zf.extractall(target)
    (root,) = [p for p in target.iterdir() if p.is_dir()]
    return root


@pytest.fixture(scope="session")
def sample_manifest_and_records(tmp_path_factory: pytest.TempPathFactory, sample_extracted_dir: Path):
    """Run the real manifest + normalization pipeline once. Returns
    (manifest, records, db_path) - db_path is where normalize_dossier put the
    SQLite registry (workspace_root.parent.parent / "registry.db")."""
    manifest = build_manifest(sample_extracted_dir, SAMPLE_DOSSIER_ID)

    workspace_root = tmp_path_factory.mktemp("sample_workspace") / "dossiers" / SAMPLE_DOSSIER_ID
    workspace_root.mkdir(parents=True)

    manifest = normalize_dossier(sample_extracted_dir, workspace_root, manifest, SAMPLE_DOSSIER_ID)

    records: list[NormalizedRecord] = []
    all_records_path = workspace_root / "normalized" / "all_records.jsonl"
    if all_records_path.exists():
        for line in all_records_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(NormalizedRecord.model_validate_json(line))

    db_path = workspace_root.parent.parent / "registry.db"
    return manifest, records, db_path


@pytest.fixture(scope="session")
def sample_db_path(sample_manifest_and_records) -> Path:
    return sample_manifest_and_records[2]


@pytest.fixture(scope="session")
def sample_graph(sample_db_path: Path):
    return build_graph(SAMPLE_DOSSIER_ID, sample_db_path)


@pytest.fixture(scope="session")
def sample_process_graphs(sample_graph):
    return build_process_graphs(SAMPLE_DOSSIER_ID, sample_graph)


@pytest.fixture(scope="session")
def sample_saved_db_path(sample_db_path: Path, sample_graph, sample_process_graphs) -> Path:
    """``sample_db_path`` with the graph persisted onto it - tools.py reads
    back from storage, it never takes an in-memory graph directly."""
    save_graph(sample_db_path, SAMPLE_DOSSIER_ID, sample_graph, sample_process_graphs)
    return sample_db_path
