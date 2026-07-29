"""Dossier-scoped Cognee Cloud adapter and authoritative evidence store."""

from __future__ import annotations

import hashlib
import csv
import io
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.core.settings import AgentSettings
from app.persistence import get_record_by_id


RECORD_ID_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
COGNEE_SOURCE_EXTENSIONS = {".csv", ".xlsx"}


class GraphUnavailableError(RuntimeError):
    """Raised when a requested graph operation cannot safely be completed."""


@dataclass(frozen=True)
class GraphIngestionResult:
    dataset_name: str
    normalized_sha256: str


class EvidenceRecordStore:
    """Rehydrates Cognee candidates from local authoritative normalized records."""

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


class CogneeCloudGraph:
    """Minimal Cognee Cloud client using one dataset for one dossier upload."""

    def __init__(self, settings: AgentSettings) -> None:
        self.settings = settings

    @staticmethod
    def dataset_name(dossier_id: str) -> str:
        return f"fraud-dossier-{dossier_id}"

    @staticmethod
    def normalized_sha256(jsonl_path: Path) -> str:
        return hashlib.sha256(CogneeCloudGraph._normalized_cloud_payload(jsonl_path)).hexdigest()

    def ingest(self, dossier_id: str, jsonl_path: Path) -> GraphIngestionResult:
        if not self.settings.is_configured:
            raise GraphUnavailableError("Cognee Cloud or OpenAI configuration is unavailable.")
        if not jsonl_path.is_file():
            raise GraphUnavailableError("Normalized dossier data is unavailable.")

        digest = self.normalized_sha256(jsonl_path)
        dataset_name = self.dataset_name(dossier_id)
        self._remember(self._normalized_cloud_payload(jsonl_path), dataset_name)
        return GraphIngestionResult(dataset_name=dataset_name, normalized_sha256=digest)

    @staticmethod
    def _normalized_cloud_payload(jsonl_path: Path) -> bytes:
        """Export selected normalized records as Cognee-ready CSV, without provenance."""
        fields = ["record_id", "record_type", "entities", "relationships", "date", "period", "amount", "currency", "attributes", "text"]
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            source = json.loads(line)
            if not CogneeCloudGraph._include_in_cloud_payload(source):
                continue
            record: dict[str, Any] = {
                "record_id": source["record_id"],
                "record_type": source["record_type"],
            }
            for field in ("date", "period", "amount", "currency"):
                if source.get(field) is not None:
                    record[field] = source[field]
            entities = [
                {key: entity[key] for key in ("entity_type", "entity_id", "label") if entity.get(key)}
                for entity in source.get("entities", [])
            ]
            if entities:
                record["entities"] = json.dumps(entities, ensure_ascii=False)
            if source.get("relationships"):
                record["relationships"] = json.dumps(source["relationships"], ensure_ascii=False)
            attributes = {key: value for key, value in source.get("data", {}).items() if value is not None}
            if attributes:
                record["attributes"] = json.dumps(attributes, ensure_ascii=False)
            if source.get("text_content"):
                record["text"] = source["text_content"]
            writer.writerow(record)
        return output.getvalue().encode("utf-8")

    @staticmethod
    def _include_in_cloud_payload(record: dict[str, Any]) -> bool:
        source_path = record.get("source", {}).get("relative_path", "")
        extension = Path(source_path).suffix.lower()
        if extension in COGNEE_SOURCE_EXTENSIONS:
            return True
        if extension != ".txt":
            return False
        record_type = record.get("record_type")
        if record_type in {"vendor_posting", "asset_record", "asset_posting"}:
            return True
        if record_type == "master_data":
            return any(segment in source_path for segment in ("Kreditoren/", "Sachkonten/"))
        if record_type != "journal_entry":
            return False
        data = record.get("data", {})
        account = str(data.get("SACHKONTONUMMER") or "")
        date = str(record.get("date") or data.get("BUCHUNGSDATUM") or "")
        text = str(data.get("BUCHUNGSTEXT") or "").lower()
        payment_type = str(data.get("BUCHUNGSTYP") or "").lower()
        amount = abs(float(record.get("amount") or 0))
        return (
            account.startswith(("040", "060", "670"))
            or date.startswith(("2025-12", "2026-01"))
            or any(word in text for word in ("rückstellung", "rueckstellung", "abgrenzung", "verbindlichkeit"))
            or (("zahlung" in payment_type or "zahlung" in text) and 9000 <= amount < 10000)
        )

    def _remember(self, normalized_payload: bytes, dataset_name: str) -> None:
        boundary = f"----fraud-detector-{uuid.uuid4().hex}"
        body = b"".join(
            (
                f"--{boundary}\r\n".encode(),
                b'Content-Disposition: form-data; name="data"; filename="normalized-dossier.csv"\r\n',
                b"Content-Type: text/csv; charset=utf-8\r\n\r\n",
                normalized_payload,
                f"\r\n--{boundary}\r\n".encode(),
                b'Content-Disposition: form-data; name="datasetName"\r\n\r\n',
                dataset_name.encode(),
                f"\r\n--{boundary}--\r\n".encode(),
            )
        )
        self._request(
            "/api/v1/remember",
            body,
            f"multipart/form-data; boundary={boundary}",
        )

    def recall_record_ids(self, dossier_id: str, query: str) -> list[str]:
        if not self.settings.is_configured:
            raise GraphUnavailableError("Cognee Cloud or OpenAI configuration is unavailable.")
        response = self._recall(self.dataset_name(dossier_id), query)
        serialized = json.dumps(response, default=str)
        return list(dict.fromkeys(RECORD_ID_PATTERN.findall(serialized)))

    def forget_dataset(self, dataset_name: str) -> None:
        """Delete the temporary Cloud dataset after dashboard findings are persisted."""
        self._request(
            "/api/v1/forget",
            json.dumps({"dataset": dataset_name}).encode(),
            "application/json",
        )

    def _recall(self, dataset_name: str, query: str) -> Any:
        return self._request(
            "/api/v1/recall",
            json.dumps(
                {
                    "query": query,
                    "datasets": [dataset_name],
                    "searchType": "GRAPH_COMPLETION",
                    "topK": 50,
                    "includeReferences": True,
                    "scope": "graph",
                }
            ).encode(),
            "application/json",
        )

    def _request(self, path: str, body: bytes, content_type: str) -> Any:
        """Call the remote Cognee Cloud API without retaining graph data locally."""
        assert self.settings.cognee_service_url is not None
        assert self.settings.cognee_api_key is not None
        try:
            request = Request(
                f"{self.settings.cognee_service_url.rstrip('/')}{path}",
                data=body,
                method="POST",
                headers={
                    "Content-Type": content_type,
                    "X-Api-Key": self.settings.cognee_api_key,
                },
            )
            with urlopen(request, timeout=120) as response:
                return json.load(response)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise GraphUnavailableError("Cognee Cloud request failed.") from exc
