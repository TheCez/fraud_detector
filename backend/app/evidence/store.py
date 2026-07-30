"""Authoritative evidence store - the correctness guarantee for every finding.

Rehydrates candidate record ids (proposed by a deterministic or model-driven
analyzer) from the dossier-scoped ``normalized_records`` SQLite table. Nothing
downstream trusts a record until it has round-tripped through here: an id that
does not resolve to a real record in this dossier simply drops out silently at
this layer, and callers reject the whole proposal it belonged to.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.persistence import get_record_by_id


class EvidenceRecordStore:
    """Resolves candidate record ids against one dossier's authoritative records."""

    def __init__(self, dossier_id: str, db_path: Path) -> None:
        self.dossier_id = dossier_id
        self.db_path = db_path

    def resolve(self, record_ids: list[str]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for record_id in dict.fromkeys(record_ids):
            record = get_record_by_id(self.db_path, self.dossier_id, record_id)
            if record is not None:
                records.append(record)
        return records

    @staticmethod
    def evidence_context(record: dict[str, Any]) -> dict[str, Any]:
        data = json.loads(record["data_json"])
        return {
            "record_id": record["record_id"],
            "record_type": record["record_type"],
            "date": record.get("date"),
            "amount": record.get("amount"),
            "currency": record.get("currency"),
            "source": data.get("source", {}),
            "data": data.get("data", {}),
            "text_content": data.get("text_content"),
        }


__all__ = ["EvidenceRecordStore"]
