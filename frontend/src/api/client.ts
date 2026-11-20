import axios, { AxiosError } from 'axios'
import type {
  AiResult, Answers, Dashboard, DataQuality, DocumentRec, IntegrityCheck, Questionnaire, Requirement,
  TxnDetail, TxnSummary,
} from './types'

// Same origin by default (Vite proxy in dev, FastAPI serving dist/). On Vercel, set
// VITE_API_BASE_URL to the Render URL, e.g. https://cip-api.onrender.com (no trailing slash).
const API_ORIGIN = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '')
export const http = axios.create({ baseURL: `${API_ORIGIN}/api`, timeout: 60_000 })

/** Normalise FastAPI error shapes ({detail: string | {message, errors}}) into one Error type. */
export class ApiError extends Error {
  status: number
  fieldErrors: Record<string, string>
  constructor(status: number, message: string, fieldErrors: Record<string, string> = {}) {
    super(message)
    this.status = status
    this.fieldErrors = fieldErrors
  }
}

http.interceptors.response.use(undefined, (err: AxiosError<{ detail?: unknown }>) => {
  const status = err.response?.status ?? 0
  const detail = err.response?.data?.detail
  if (typeof detail === 'string') return Promise.reject(new ApiError(status, detail))
  if (detail && typeof detail === 'object' && 'message' in detail) {
    const d = detail as { message: string; errors?: Record<string, string> }
    return Promise.reject(new ApiError(status, d.message, d.errors ?? {}))
  }
  return Promise.reject(new ApiError(status, err.message || 'Network error'))
})

const get = <T,>(url: string, params?: object) => http.get<T>(url, { params }).then((r) => r.data)
const post = <T,>(url: string, body?: unknown) => http.post<T>(url, body).then((r) => r.data)

export interface UploadItem { field: string; file: File; sha256: string }

export const api = {
  dashboard: () => get<Dashboard>('/dashboard'),
  questionnaire: () => get<Questionnaire>('/ingestion/questionnaire'),
  evaluate: (answers: Answers) =>
    post<{ errors: Record<string, string>; priority: string; sla_policy: { code: string; version: number; target_hours: number; pausable: boolean; projected_due_at: string } | null }>(
      '/ingestion/evaluate', { answers }),
  submit: (answers: Answers, uploads: UploadItem[], onProgress?: (pct: number) => void) => {
    const fd = new FormData()
    fd.append('answers', JSON.stringify(answers))
    fd.append('manifest', JSON.stringify(uploads.map((u) => ({ field: u.field, filename: u.file.name, sha256: u.sha256 }))))
    uploads.forEach((u) => fd.append(u.field, u.file, u.file.name))
    return http.post<TxnDetail>('/ingestion/submit', fd, {
      onUploadProgress: (e) => onProgress?.(Math.round((100 * e.loaded) / (e.total ?? e.loaded))),
    }).then((r) => r.data)
  },
  transactions: (params: object) => get<{ total: number; items: TxnSummary[] }>('/transactions', params),
  transaction: (id: string) => get<TxnDetail>(`/transactions/${id}`),
  decide: (id: string, decision: 'APPROVED' | 'DENIED') => post<TxnDetail>(`/transactions/${id}/decision`, { decision }),
  slaAction: (id: string, action: 'pause' | 'resume', reason: string) => post<TxnDetail>(`/transactions/${id}/sla/${action}`, { reason }),
  aiSummary: (id: string, reviewer_note: string) => post<AiResult>(`/transactions/${id}/ai-summary`, { reviewer_note }),
  documents: (params: object) => get<DocumentRec[]>('/documents', params),
  document: (id: string) => get<DocumentRec & { children: DocumentRec[]; blob_metadata: Record<string, string> }>(`/documents/${id}`),
  verify: (id: string) => post<IntegrityCheck>(`/documents/${id}/verify`),
  tamper: (id: string) => post<{ message: string }>(`/documents/${id}/simulate-tamper`),
  outbox: () => get<{ broker: string; counts: Record<string, number>; avg_publish_latency_s: number | null; events: OutboxRow[] }>('/pipeline/outbox'),
  runPipeline: () => post<Record<string, number>>('/pipeline/run'),
  slaBoard: () => get<{ items: TxnSummary[]; policies: SlaPolicy[] }>('/pipeline/sla'),
  eventLog: (topic?: string) => get<{ topics: { topic: string; messages: number }[]; messages: BrokerMsg[] }>('/events/log', { topic }),
  consumers: () => get<ConsumerState>('/events/consumers'),
  replay: (offset: number) => post<{ redelivered_event_id: string; consumer_result: Record<string, number> }>(`/events/${offset}/replay`),
  members: () => get<MemberRow[]>('/security/members'),
  reveal: (token: string, role: string, purpose: string) => post<Revealed>(`/security/members/${token}/reveal`, { role, purpose }),
  accessAudit: () => get<AuditRow[]>('/security/audit'),
  phiAllowlist: () => get<{ allowlist: Record<string, string[]>; patterns: string[]; decrypt_roles: string[] }>('/security/phi-allowlist'),
  phiCheck: (event_type: string, payload: object) => post<{ allowed: boolean; problems: string[] }>('/security/phi-check', { event_type, payload }),
  sanitize: (text: string) => post<SanitizeResult>('/ai/sanitize', { text }),
  capabilities: () => get<Record<string, { description: string; allowed_fields: string[]; untrusted_fields: string[] }>>('/ai/capabilities'),
  playground: (capability: string, candidate: object) => post<AiResult>('/ai/playground', { capability, candidate }),
  aiAudit: () => get<AiAuditRow[]>('/ai/audit'),
  dataQuality: () => get<DataQuality>('/data-quality'),
  runDq: () => post<DataQuality>('/data-quality/run'),
  requirements: () => get<Requirement[]>('/requirements'),
  health: () => get<{ status: string; broker: string }>('/health'),
  // ---- Upload Documents (smart intake) — separate workflow, same ingestion engine
  smartAnalyze: (items: { file: File; sha256: string }[], onProgress?: (pct: number) => void) => {
    const fd = new FormData()
    fd.append('manifest', JSON.stringify(items.map((i) => ({ filename: i.file.name, sha256: i.sha256 }))))
    items.forEach((i) => fd.append('files', i.file, i.file.name))
    return http.post<SmartAnalysis>('/smart-intake/analyze', fd, {
      onUploadProgress: (e) => onProgress?.(Math.round((100 * e.loaded) / (e.total ?? e.loaded))),
    }).then((r) => r.data)
  },
  smartSubmit: (batch_id: string, answers: Answers, assignments: { file_id: string; requirement_code: string }[]) =>
    post<TxnDetail>('/smart-intake/submit', { batch_id, answers, assignments }),
  smartDiscard: (batch_id: string) => http.delete(`/smart-intake/${batch_id}`),
}

export interface ExtractedField { value: string | number | boolean; confidence: number; source: string; evidence: string }
export interface SmartFile {
  file_id: string; filename: string; content_type: string; doc_class: string; confidence: number
  suggested_slot: string; suggested_slots?: string[]; characters: number; fields_found: string[]; error: string | null; sha256: string; size_bytes: number
}
export interface SmartAnalysis {
  batch_id: string
  answers: Answers
  fields: Record<string, ExtractedField>
  files: SmartFile[]
  validation_errors: Record<string, string>
  rejected: Record<string, string>
  priority: string
  sla_policy: { code: string; version: number; target_hours: number; pausable: boolean } | null
}

export interface OutboxRow { id: string; event_type: string; topic: string; aggregate_id: string; status: string; attempts: number; last_error: string | null; created_at: string; published_at: string | null }
export interface SlaPolicy { code: string; version: number; target_hours: number; pausable: boolean; description: string }
export interface BrokerMsg { offset: number; topic: string; key: string; event_id: string; event_type: string; payload: Record<string, unknown>; published_at: string }
export interface ConsumerState {
  groups: { group: string; topic: string; log_end_offset: number; committed_offset: number; lag: number; messages: number }[]
  processed: Record<string, number>
  duplicates: { event_id: string; consumer_group: string; detected_at: string }[]
}
export interface MemberRow { member_token: string; created_at: string; dob_ciphertext_prefix_hex: string; dob_ciphertext_bytes: number; transactions: number }
export interface Revealed { role: string; name: string; dob: string; address: string; member_id: string; masked: boolean }
export interface AuditRow { actor_role: string; action: string; resource: string; allowed: boolean; purpose: string | null; at: string }
export interface SanitizeResult { sanitized: string; original_length: number; final_length: number; truncated: boolean; flags: string[]; risk: 'LOW' | 'MEDIUM' | 'HIGH'; removed_invisible: number; phi_detected: string[] }
export interface AiAuditRow { id: number; capability: string; dcn: string | null; blocked: boolean; fields_blocked: string[]; injection_flags: string[]; truncated: boolean; at: string }
