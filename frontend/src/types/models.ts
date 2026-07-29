export type ProcessingStatus =
  | 'uploading'
  | 'validating'
  | 'extracting'
  | 'building_inventory'
  | 'normalizing'
  | 'analyzing'
  | 'analysis_incomplete'
  | 'complete'
  | 'error'

export type FileClassification = 'evidence' | 'supporting' | 'technical_metadata'

export type ParseStatus = 'pending' | 'parsed' | 'skipped' | 'error'

export type Severity = 'critical' | 'high' | 'medium' | 'low'

export type FindingStatus = 'demo' | 'needs_review' | 'supported' | 'dismissed'

export interface Dossier {
  id: string
  name: string
  status: ProcessingStatus
  file_count: number
  record_count: number
  finding_count: number
  created_at: string
}

export interface DossierFile {
  file_id: string
  relative_path: string
  original_name: string
  extension: string
  mime_type: string
  size_bytes: number
  classification: FileClassification
  parse_status: ParseStatus
  normalized_record_count: number
}

export interface SourceLocation {
  relative_path: string
  sheet: string | null
  page: number | null
  row_start: number | null
  row_end: number | null
  columns: string[] | null
  paragraph: number | null
}

export interface Evidence {
  evidence_id: string
  finding_id: string
  record_id: string
  document_id: string
  label: string
  excerpt: string
  source_location: SourceLocation
  original_language: string
  explanation_en: string
}

export interface Finding {
  finding_id: string
  title: string
  severity: Severity
  category: string
  amount_at_risk: number | null
  currency: string | null
  explanation: string
  reasoning: string
  evidence_count: number
  confidence: string
  status: FindingStatus
  evidence: Evidence[]
}
