"""Analyzer protocol - interface for all analysis implementations."""

from typing import Protocol
from pathlib import Path

from app.models.schemas import Finding


class Analyzer(Protocol):
    def analyze(self, dossier_id: str, db_path: Path) -> list[Finding]:
        """Run analysis and return findings with evidence."""
        ...
