// ---- Questionnaire ---------------------------------------------------------------
export type Leaf = { q: string; op: 'eq' | 'ne' | 'in' | 'not_in' | 'truthy' | 'falsy' | 'gt' | 'lt'; value?: unknown }
export type Condition = Leaf | { all: Condition[] } | { any: Condition[] } | { not: Condition }

export type Validator = { type: string; value?: number | string; message?: string }
export type Option = { value: string; label: string }

export interface Question {
  id: string
  section: string
  type: 'select' | 'boolean' | 'text' | 'textarea' | 'date' | 'number'
  label: string
  help?: string
  placeholder?: string
  options?: Option[]
  phi?: boolean
  required?: boolean
  visible_when?: Condition
  required_when?: Condition
  validators?: Validator[]
}

export interface DocumentSpec {
  code: string
  label: string
  description: string
  visible_when?: Condition
  required_when?: Condition
  max_files?: number
}

export interface Questionnaire {
  id: string
  version: number
  sections: { id: string; title: string; description: string }[]
  questions: Question[]
  documents: DocumentSpec[]
  accept: { mime: string[]; extensions: string[] }
}

export type Answers = Record<string, string | number | boolean | undefined>

// ---- Domain -------------------------------------------------------------------------
export interface SlaStatus {
  policy_code: string
  policy_version: number
  target_hours: number
  elapsed_hours: number
  remaining_hours: number
  percent_used: number
  state: 'ON_TRACK' | 'AT_RISK' | 'BREACHED' | 'PAUSED' | 'MET' | 'MISSED'
  projected_due_at: string | null
  paused_hours: number
}

export interface IntegrityCheck {
  id: number
  stage: string
  passed: boolean
  detail: string | null
  expected_sha256: string
  actual_sha256: string | null
  blob_meta_sha256: string | null
  checked_at: string
}

export interface DocumentRec {
  id: string
  transaction_id: string
  dcn: string | null
  parent_document_id: string | null
  artifact_kind: 'SOURCE' | 'DERIVATIVE_TEXT'
  requirement_code: string
  filename: string
  content_type: string
  size_bytes: number
  sha256: string
  client_sha256: string | null
  integrity_state: 'VERIFIED' | 'FAILED' | 'UNKNOWN'
  doc_class: string | null
  confidence: number | null
  extracted: { icd10_codes?: string[]; cpt_codes?: string[] }
  created_at: string | null
  integrity_checks?: IntegrityCheck[]
}

export interface TxnSummary {
  id: string
  dcn: string
  request_type: string
  service_category: string
  priority: 'STANDARD' | 'EXPEDITED'
  status: 'RECEIVED' | 'CLASSIFIED' | 'EXTRACTED' | 'IN_REVIEW' | 'COMPLETED' | 'QUARANTINED'
  provider_npi: string
  member_token: string
  source_received_at: string
  document_count: number
  sla: SlaStatus
  quarantine_reason: string | null
}

export interface TxnDetail extends TxnSummary {
  answers: Record<string, unknown>
  timestamps: Record<string, string | null>
  documents: DocumentRec[]
  sla_events: { event_type: string; occurred_at: string; reason: string | null; policy: string }[]
}

export interface Dashboard {
  kpis: Record<string, number>
  daily_intake: { date: string; standard: number; expedited: number }[]
  status_breakdown: { status: string; count: number }[]
  sla_breakdown: { state: string; count: number }[]
  doc_classes: { doc_class: string; count: number }[]
  request_mix: { request_type: string; count: number }[]
  recent: TxnSummary[]
  at_risk: TxnSummary[]
  dq_dimensions: { dimension: string; score: number }[]
}

export interface DqRule {
  code: string
  dimension: string
  description: string
  population: number
  failed: number
  score: number
  threshold: number
  risk: 'HIGH' | 'MEDIUM' | 'LOW'
  passing: boolean
}

export interface DataQuality {
  run_id: string
  run_at: string
  overall_score: number
  dimensions: { dimension: string; score: number }[]
  rules: DqRule[]
  history: { run_id: string; run_at: string; avg_score: number }[]
}

export interface Requirement {
  id: string
  module: string
  priority: number
  route: string
  backlog_status: string
  title: string
  implementation: string[]
  evidence: string
}

export interface AiResult {
  response: string
  context_sent: Record<string, unknown>
  fields_blocked: string[]
  injection_flags: string[]
  blocked: boolean
  truncated: boolean
}
