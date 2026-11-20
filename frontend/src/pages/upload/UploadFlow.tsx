/**
 * Upload Documents — a separate, multi-page workflow:
 *   /upload            1. Upload & read (PDF / Word)
 *   /upload/request    2. Request details   (pre-filled from the documents)
 *   /upload/member     3. Member & provider (pre-filled, PHI)
 *   /upload/documents  4. Assign documents to required slots
 *   /upload/review     5. Review & submit  -> same ingestion engine as Document Ingestion
 *
 * State lives in a React context (memory only — PHI never touches browser storage) and is
 * shared by the nested routes. The questionnaire rules engine is reused unchanged.
 */
import { createContext, useContext, useMemo, useState, type Dispatch, type ReactNode, type SetStateAction } from 'react'
import { NavLink, Navigate, Outlet, useLocation } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import clsx from 'clsx'
import { CheckCircle2, Sparkles } from 'lucide-react'
import { api, type SmartAnalysis } from '../../api/client'
import type { Answers, Question, Questionnaire, TxnDetail } from '../../api/types'
import { PageHeader, Skeleton } from '../../components/ui'
import { isRequired, isVisible, validateQuestion } from '../../lib/rules'

export const STEPS = [
  { path: '/upload', title: 'Upload & read', short: 'Upload' },
  { path: '/upload/request', title: 'Request details', short: 'Request', section: 'request' },
  { path: '/upload/member', title: 'Member & provider', short: 'Member', section: 'member' },
  { path: '/upload/documents', title: 'Documents', short: 'Documents', section: 'clinical' },
  { path: '/upload/review', title: 'Review & submit', short: 'Review' },
] as const

export type Assignments = Record<string, string[]> // file_id -> requirement codes
export type Picked = { key: string; file: File; sha256?: string }

interface FlowState {
  qn: Questionnaire
  picked: Picked[]
  setPicked: Dispatch<SetStateAction<Picked[]>>
  analysis: SmartAnalysis | null
  setAnalysis: (a: SmartAnalysis | null) => void
  answers: Answers
  setAnswer: (id: string, v: Answers[string]) => void
  edited: Set<string>
  assignments: Assignments
  setAssignments: (a: Assignments) => void
  receipt: TxnDetail | null
  setReceipt: (t: TxnDetail | null) => void
  reset: () => void
  sectionErrors: (section: string) => Record<string, string>
  docErrors: Record<string, string>
}

const Ctx = createContext<FlowState | null>(null)
export const useFlow = () => {
  const c = useContext(Ctx)
  if (!c) throw new Error('useFlow outside UploadFlow')
  return c
}

function Provider({ qn, children }: { qn: Questionnaire; children: ReactNode }) {
  const [analysis, setAnalysisRaw] = useState<SmartAnalysis | null>(null)
  const [picked, setPicked] = useState<Picked[]>([])
  const [answers, setAnswers] = useState<Answers>({})
  const [edited, setEdited] = useState<Set<string>>(new Set())
  const [assignments, setAssignments] = useState<Assignments>({})
  const [receipt, setReceipt] = useState<TxnDetail | null>(null)

  const setAnalysis = (a: SmartAnalysis | null) => {
    if (analysis && analysis.batch_id !== a?.batch_id) api.smartDiscard(analysis.batch_id).catch(() => {})
    setAnalysisRaw(a)
    // re-reading keeps every value the user already edited by hand
    setAnswers((prev) => (a ? { ...a.answers, ...Object.fromEntries([...edited].map((k) => [k, prev[k]])) } : {}))
    if (!a) setEdited(new Set())
    // default slots = every slot the reader found evidence for (one letter can be note + order + attestation)
    setAssignments(a ? Object.fromEntries(a.files.filter((f) => !f.error).map((f) => [f.file_id, f.suggested_slots?.length ? [...f.suggested_slots] : [f.suggested_slot]])) : {})
  }
  const setAnswer = (id: string, v: Answers[string]) => {
    setAnswers((x) => ({ ...x, [id]: v }))
    setEdited((s) => new Set(s).add(id))
  }
  const sectionErrors = (section: string) =>
    Object.fromEntries(qn.questions.filter((q) => q.section === section).map((q) => [q.id, validateQuestion(q, answers)]).filter(([, e]) => e)) as Record<string, string>

  const docErrors = useMemo(() => {
    const used = new Map<string, number>()
    Object.values(assignments).flat().forEach((c) => used.set(c, (used.get(c) ?? 0) + 1))
    const e: Record<string, string> = {}
    for (const d of qn.documents) {
      const n = used.get(d.code) ?? 0
      if (!isVisible(d, answers)) { if (n) e[d.code] = `${d.label} does not apply to this request — reassign the file`; continue }
      if (isRequired(d, answers) && n === 0) e[d.code] = `${d.label} is required`
      if (n > (d.max_files ?? 5)) e[d.code] = `At most ${d.max_files} file(s) for ${d.label}`
    }
    return e
  }, [assignments, answers, qn.documents])

  const reset = () => { setAnalysis(null); setPicked([]); setReceipt(null) }

  return (
    <Ctx.Provider value={{ qn, picked, setPicked, analysis, setAnalysis, answers, setAnswer, edited, assignments, setAssignments, receipt, setReceipt, reset, sectionErrors, docErrors }}>
      {children}
    </Ctx.Provider>
  )
}

export default function UploadFlow() {
  const { data: qn, isLoading } = useQuery({ queryKey: ['questionnaire'], queryFn: api.questionnaire, staleTime: Infinity })
  if (isLoading || !qn) return <Skeleton h={420} />
  return <Provider qn={qn}><Shell /></Provider>
}

function Shell() {
  const { analysis, receipt, sectionErrors, docErrors } = useFlow()
  const loc = useLocation()
  const idx = Math.max(0, STEPS.findIndex((s) => s.path === loc.pathname.replace(/\/$/, '')))
  if (idx > 0 && !analysis && !receipt) return <Navigate to="/upload" replace />

  const stepOk = (i: number) => {
    const s = STEPS[i]
    if (i === 0) return !!analysis
    if ('section' in s && s.section) {
      return Object.keys(sectionErrors(s.section)).length === 0 && (s.section !== 'clinical' || Object.keys(docErrors).length === 0)
    }
    return false
  }

  return (
    <>
      <PageHeader title="Upload documents"
        subtitle={<>Upload a referral, clinical note or denial letter in <b>PDF or Word</b>. CIP reads the documents, fills in the request, member and provider details for you, and suggests where each document belongs. You confirm every value on separate pages before the DCN is issued.</>}
        reqs={['CIP-PA-003', 'CIP-PA-005', 'CIP-PA-025', 'CIP-PA-274']} />
      {!receipt && (
        <nav className="stepper" aria-label="Upload workflow">
          {STEPS.map((s, i) => {
            const enabled = i === 0 || !!analysis
            const done = i < idx && stepOk(i)
            const cls = clsx('step', i === idx && 'active', done && 'done')
            const body = <><span className="n">{done ? <CheckCircle2 size={15} /> : i + 1}</span>{s.title}</>
            return enabled
              ? <NavLink key={s.path} to={s.path} end className={() => cls}>{body}</NavLink>
              : <span key={s.path} className={cls} aria-disabled="true">{body}</span>
          })}
        </nav>
      )}
      <Outlet />
    </>
  )
}

/** Shows where an auto-filled value came from, and whether the user changed it. */
export function Provenance({ id }: { id: string }) {
  const { analysis, edited } = useFlow()
  const f = analysis?.fields[id]
  if (!f) return null
  if (edited.has(id)) return <div className="help" style={{ marginTop: 6 }}>Edited by you · originally read from {f.source}</div>
  return (
    <div className="help" style={{ marginTop: 6 }} title={f.evidence}>
      <Sparkles size={12} color="var(--indigo)" style={{ verticalAlign: '-2px', marginRight: 4 }} />
      Auto-filled from <b>{f.source}</b> · {Math.round(f.confidence * 100)}% confidence · <span className="faint">“{f.evidence.slice(0, 70)}{f.evidence.length > 70 ? '…' : ''}”</span>
    </div>
  )
}

export const sectionQuestions = (qn: Questionnaire, section: string, a: Answers): Question[] =>
  qn.questions.filter((q) => q.section === section && isVisible(q, a))
