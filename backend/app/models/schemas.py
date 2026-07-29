from enum import Enum
from typing import Optional

from pydantic import BaseModel


class ProcessingStatus(str, Enum):
    uploading = "uploading"
    validating = "validating"
    extracting = "extracting"
    building_inventory = "building_inventory"
    normalizing = "normalizing"
    analyzing = "analyzing"
    analysis_incomplete = "analysis_incomplete"
    complete = "complete"
    error = "error"


class FileClassification(str, Enum):
    evidence = "evidence"
    supporting = "supporting"
    technical_metadata = "technical_metadata"


class ParseStatus(str, Enum):
    pending = "pending"
    parsed = "parsed"
    skipped = "skipped"
    error = "error"


class Severity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


class FindingStatus(str, Enum):
    demo = "demo"
    needs_review = "needs_review"
    supported = "supported"
    dismissed = "dismissed"


class SourceLocation(BaseModel):
    relative_path: str
    sheet: Optional[str] = None
    page: Optional[int] = None
    row_start: Optional[int] = None
    row_end: Optional[int] = None
    columns: Optional[list[str]] = None
    paragraph: Optional[int] = None


class Evidence(BaseModel):
    evidence_id: str
    finding_id: str
    record_id: str
    document_id: str
    label: str
    excerpt: str
    source_location: SourceLocation
    original_language: str = "de"
    explanation_en: str


class Finding(BaseModel):
    finding_id: str
    title: str
    severity: Severity
    category: str
    amount_at_risk: Optional[float] = None
    currency: Optional[str] = None
    explanation: str
    reasoning: str
    evidence_count: int
    confidence: str
    status: FindingStatus
    evidence: list[Evidence] = []


class DossierFile(BaseModel):
    file_id: str
    relative_path: str
    original_name: str
    extension: str
    mime_type: str
    size_bytes: int
    classification: FileClassification
    parse_status: ParseStatus
    normalized_record_count: int = 0


class Dossier(BaseModel):
    id: str
    name: str
    status: ProcessingStatus
    file_count: int = 0
    record_count: int = 0
    finding_count: int = 0
    created_at: str
