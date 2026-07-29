from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


DEFAULT_BASE_DIR = Path("runtime/dossiers")


class WorkspacePaths(BaseModel):
    root: Path
    original: Path
    extracted: Path
    normalized: Path
    previews: Path

    model_config = {"arbitrary_types_allowed": True}


def create_workspace(
    dossier_id: str, base_dir: Path | None = None
) -> WorkspacePaths:
    base = base_dir or DEFAULT_BASE_DIR
    root = base / dossier_id

    paths = WorkspacePaths(
        root=root,
        original=root / "original",
        extracted=root / "extracted",
        normalized=root / "normalized",
        previews=root / "previews",
    )

    for p in (paths.original, paths.extracted, paths.normalized, paths.previews):
        p.mkdir(parents=True, exist_ok=True)

    return paths
