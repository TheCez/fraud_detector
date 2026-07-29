from __future__ import annotations

from pathlib import Path

from app.ingestion.workspace import create_workspace


def test_create_workspace_creates_dirs(tmp_path: Path):
    ws = create_workspace("dossier-001", base_dir=tmp_path)

    assert ws.root == tmp_path / "dossier-001"
    assert ws.original.exists()
    assert ws.extracted.exists()
    assert ws.normalized.exists()
    assert ws.previews.exists()


def test_create_workspace_idempotent(tmp_path: Path):
    ws1 = create_workspace("same-id", base_dir=tmp_path)
    ws2 = create_workspace("same-id", base_dir=tmp_path)

    assert ws1.root == ws2.root
    assert ws1.extracted.exists()
