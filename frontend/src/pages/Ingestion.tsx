import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import clsx from 'clsx'
import {
  ArrowLeft, ArrowRight, CheckCircle2, CircleDashed, Clock, FileText, Fingerprint, Lock, RotateCcw, Send, ShieldCheck, Trash2, UploadCloud, Zap,
} from 'lucide-react'
import { ApiError, api } from '../api/client'
import type { Answers, DocumentSpec, Question, Questionnaire, TxnDetail } from '../api/types'
import { Card, Hash, PageHeader, Skeleton, useToast } from '../components/ui'
import { fmtBytes, fmtDateTime, fmtHours, humanize } from '../lib/format'
import { sha256File } from '../lib/hash'
import { evaluate, isRequired, isVisible, validateQuestion, visibleAnswers } from '../lib/rules'

type Staged = { key: string; file: File; sha256?: string }
type FilesByCode = Record<string, Staged[]>

const DRAFT_KEY = 'cip-intake-draft'

function useDebounced<T>(value: T, ms: number) {
  const [v, setV] = useState(value)
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t) }, [value, ms])
  return v
}

export default function Ingestion() {
  const { data: qn, isLoading } = useQuery({ queryKey: ['questionnaire'], queryFn: api.questionnaire, staleTime: Infinity })
  if (isLoading || !qn) return <Skeleton h={480} />
  return <Wizard qn={qn} />
}

function Wizard({ qn }: { qn: Questionnaire }) {
  const steps = [...qn.sections.map((s) => ({ id: s.id, title: s.title, description: s.description })), { id: 'review', title: 'Review & submit', description: 'Confirm before the DCN is issued and the SLA clock starts.' }]
  const [step, setStep] = useState(0)
  const [answers, setAnswers] = useState<Answers>(() => {
    try { return JSON.parse(sessionStorage.getItem(DRAFT_KEY) ?? '{}') } catch { return {} }
  })
  const [files, setFiles] = useState<FilesByCode>({})
  const [touched, setTouched] = useState<Set<string>>(new Set())
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({})
  const [progress, setProgress] = useState(0)
  const [receipt, setReceipt] = useState<TxnDetail | null>(null)
  const toast = useToast()
  const qc = useQueryClient()

  // Persist non-PHI draft only (PHI never touches browser storage)
  useEffect(() => {
    const safe = Object.fromEntries(Object.entries(answers).filter(([k]) => !qn.questions.find((q) => q.id === k)?.phi))
    try { sessionStorage.setItem(DRAFT_KEY, JSON.stringify(safe)) } catch { /* storage may be unavailable */ }
  }, [answers, qn.questions])

  const debounced = useDebounced(answers, 350)
  const preview = useQuery({ queryKey: ['evaluate', debounced], queryFn: () => api.evaluate(debounced), enabled: !!debounced.request_type })

  const docs = useMemo(() => qn.documents.filter((d) => isVisible(d, answers)), [qn.documents, answers])
  const sectionQs = (sid: string) => qn.questions.filter((q) => q.section === sid && isVisible(q, answers))
  const errorsFor = (qs: Question[]) => Object.fromEntries(qs.map((q) => [q.id, validateQuestion(q, answers)]).filter(([, e]) => e)) as Record<string, string>
  const docErrors = useMemo(() => {
    const e: Record<string, string> = {}
    for (const d of docs) {
      const n = files[d.code]?.length ?? 0
      if (isRequired(d, answers) && n === 0) e[d.code] = `${d.label} is required`
      if (files[d.code]?.some((f) => !f.sha256)) e[d.code] = 'Computing fingerprint…'
    }
    return e
  }, [docs, files, answers])

  const set = (id: string, v: Answers[string]) => {
    setAnswers((a) => ({ ...a, [id]: v }))
    setServerErrors((e) => { const { [id]: _, ...rest } = e; return rest })
  }

  const current = steps[step]
  const currentErrors = current.id === 'review' ? {} : errorsFor(sectionQs(current.id))
  const stepValid = Object.keys(currentErrors).length === 0 && (current.id !== 'clinical' || Object.keys(docErrors).length === 0)

  const next = () => {
    const qs = sectionQs(current.id)
    setTouched((t) => new Set([...t, ...qs.map((q) => q.id), ...(current.id === 'clinical' ? docs.map((d) => `doc:${d.code}`) : [])]))
    if (stepValid) setStep((s) => Math.min(s + 1, steps.length - 1))
  }

  const submit = useMutation({
    mutationFn: () => {
      // drop files for documents that are no longer applicable
      const uploads = docs.flatMap((d) => (files[d.code] ?? []).map((f) => ({ field: `doc__${d.code}`, file: f.file, sha256: f.sha256! })))
      return api.submit(visibleAnswers(qn.questions, answers), uploads, setProgress)
    },
    onSuccess: (t) => {
      setReceipt(t)
      sessionStorage.removeItem(DRAFT_KEY)
      qc.invalidateQueries({ queryKey: ['dashboard'] })
      toast('ok', `${t.dcn} created — SLA clock started`)
    },
    onError: (e: ApiError) => {
      setServerErrors(e.fieldErrors)
      toast('err', e.message)
      const firstField = Object.keys(e.fieldErrors)[0]
      if (firstField) {
        const sec = firstField.startsWith('doc:') ? 'clinical' : qn.questions.find((q) => q.id === firstField)?.section
        const idx = steps.findIndex((s) => s.id === sec)
        if (idx >= 0) setStep(idx)
      }
    },
  })

  const reset = () => { setAnswers({}); setFiles({}); setTouched(new Set()); setReceipt(null); setStep(0); setProgress(0); submit.reset() }

  if (receipt) return <Receipt t={receipt} onAnother={reset} />

  const requiredDocs = docs.filter((d) => isRequired(d, answers))
  const answeredCount = qn.questions.filter((q) => isVisible(q, answers) && answers[q.id] !== undefined && answers[q.id] !== '').length
  const visibleCount = qn.questions.filter((q) => isVisible(q, answers)).length

  return (
    <>
      <PageHeader title="Document ingestion"
        subtitle="Answer the questions — the form adapts to the request type, service and urgency, and asks for exactly the documents that apply. Every file is fingerprinted in your browser and again on the server."
        reqs={['CIP-PA-003', 'CIP-PA-005', 'CIP-PA-025', 'CIP-PA-274']} />

      <div className="stepper">
        {steps.map((s, i) => (
          <button key={s.id} className={clsx('step', i === step && 'active', i < step && 'done')} style={{ cursor: i < step ? 'pointer' : 'default' }} onClick={() => i < step && setStep(i)}>
            <span className="n">{i < step ? <CheckCircle2 size={15} /> : i + 1}</span>{s.title}
          </button>
        ))}
      </div>

      <div className="grid g-main" style={{ alignItems: 'start' }}>
        <Card title={current.title} sub={current.description}>
          <div className="stack" style={{ gap: 18 }}>
            {current.id !== 'review' && sectionQs(current.id).map((q) => (
              <FieldRenderer key={q.id} q={q} value={answers[q.id]} onChange={(v) => set(q.id, v)}
                required={isRequired(q, answers)} error={serverErrors[q.id] ?? (touched.has(q.id) ? currentErrors[q.id] : undefined)}
                onBlur={() => setTouched((t) => new Set(t).add(q.id))} />
            ))}

            {current.id === 'clinical' && (
              <div className="stack reveal" style={{ gap: 12 }}>
                <div className="spread"><b>Documents for this request</b><span className="faint" style={{ fontSize: 12 }}>PDF, PNG, JPEG or TXT · max 25 MB each · type verified by file signature</span></div>
                {docs.map((d) => (
                  <DropZone key={d.code} spec={d} required={isRequired(d, answers)} staged={files[d.code] ?? []}
                    error={serverErrors[`doc:${d.code}`] ?? (touched.has(`doc:${d.code}`) ? docErrors[d.code] : undefined)}
                    onChange={(fs) => { setFiles((all) => ({ ...all, [d.code]: fs })); setServerErrors((e) => { const { [`doc:${d.code}`]: _, ...r } = e; return r }) }} />
                ))}
              </div>
            )}

            {current.id === 'review' && <Review qn={qn} answers={answers} docs={docs} files={files} />}

            {submit.isPending && (
              <div>
                <div className="spread" style={{ fontSize: 12.5, marginBottom: 6 }}><span>{progress < 100 ? 'Uploading…' : 'Server verifying SHA-256 and writing DCN…'}</span><span className="num">{progress}%</span></div>
                <div className="bar"><span style={{ width: `${progress}%`, background: 'var(--brand)' }} /></div>
              </div>
            )}

            <div className="spread" style={{ borderTop: '1px solid var(--border)', paddingTop: 16 }}>
              <button className="btn" disabled={step === 0} onClick={() => setStep((s) => s - 1)}><ArrowLeft size={15} />Back</button>
              {current.id === 'review'
                ? <button className="btn primary" disabled={submit.isPending} onClick={() => submit.mutate()}><Send size={15} />Submit & issue DCN</button>
                : <button className="btn primary" onClick={next}>Continue<ArrowRight size={15} /></button>}
            </div>
          </div>
        </Card>

        <div className="stack" style={{ position: 'sticky', top: 80 }}>
          <Card title="Live intake assessment" sub="Evaluated by the same rules engine on client and server">
            <div className="stack" style={{ gap: 14 }}>
              <div>
                <div className="spread" style={{ fontSize: 12.5, marginBottom: 6 }}><span className="muted">Questions answered</span><b className="num">{answeredCount}/{visibleCount}</b></div>
                <div className="bar"><span style={{ width: `${(100 * answeredCount) / Math.max(1, visibleCount)}%`, background: 'var(--indigo)' }} /></div>
              </div>
              <div className="row" style={{ gap: 12 }}>
                <div className="kpi-icon" style={{ background: answers.is_urgent ? 'var(--amber-soft)' : 'var(--slate-soft)', color: answers.is_urgent ? 'var(--amber)' : 'var(--text-2)' }}>{answers.is_urgent ? <Zap size={16} /> : <Clock size={16} />}</div>
                <div>
                  <div style={{ fontWeight: 650 }}>{preview.data?.priority ?? (answers.is_urgent ? 'EXPEDITED' : 'STANDARD')} review</div>
                  <div className="faint" style={{ fontSize: 12 }}>
                    {preview.data?.sla_policy ? <>Policy {preview.data.sla_policy.code} v{preview.data.sla_policy.version} · {fmtHours(preview.data.sla_policy.target_hours)} target{preview.data.sla_policy.pausable ? '' : ' · non-pausable'}</> : 'Choose a request type to resolve the SLA policy'}
                  </div>
                </div>
              </div>
              {preview.data?.sla_policy && <div className="alert info" style={{ fontSize: 12.5 }}><Clock size={15} />If submitted now, due by <b>{fmtDateTime(preview.data.sla_policy.projected_due_at)}</b></div>}
              <div>
                <div className="muted" style={{ fontSize: 12.5, marginBottom: 8 }}>Document checklist</div>
                {requiredDocs.length === 0 && <div className="faint" style={{ fontSize: 12.5 }}>No mandatory documents yet.</div>}
                {requiredDocs.map((d) => {
                  const ok = (files[d.code]?.length ?? 0) > 0
                  return <div key={d.code} className="row" style={{ fontSize: 13, padding: '4px 0' }}>{ok ? <CheckCircle2 size={16} color="var(--green)" /> : <CircleDashed size={16} color="var(--text-3)" />}<span className={ok ? '' : 'muted'}>{d.label}</span></div>
                })}
              </div>
              <div className="alert ok" style={{ fontSize: 12 }}><Lock size={15} /><span>Name, DOB, member ID and address are encrypted with pgcrypto and never written to events, logs or browser storage.</span></div>
            </div>
          </Card>
        </div>
      </div>
    </>
  )
}

// ------------------------------------------------------------------------------ fields
export function FieldRenderer({ q, value, onChange, required, error, onBlur }: {
  q: Question; value: Answers[string]; onChange: (v: Answers[string]) => void; required: boolean; error?: string; onBlur: () => void
}) {
  const conditional = !!q.visible_when
  return (
    <div className={clsx('field', conditional && 'reveal', error && 'invalid')}>
      <label htmlFor={q.id}>
        {q.label}{required && <span className="req">*</span>}
        {q.phi && <span className="phi-tag">PHI · ENCRYPTED</span>}
        {conditional && <span className="badge b-indigo" style={{ fontSize: 10.5 }}>conditional</span>}
      </label>
      {q.type === 'select' && q.options && q.options.length <= 5 && (
        <div className="choice-grid">
          {q.options.map((o) => (
            <button type="button" key={o.value} className={clsx('choice', value === o.value && 'on')} onClick={() => { onChange(o.value); onBlur() }}>
              <span className="radio" />{o.label}
            </button>
          ))}
        </div>
      )}
      {q.type === 'select' && q.options && q.options.length > 5 && (
        <select id={q.id} className="select" value={String(value ?? '')} onChange={(e) => onChange(e.target.value || undefined)} onBlur={onBlur}>
          <option value="">Select…</option>
          {q.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      )}
      {q.type === 'boolean' && (
        <div className="toggle">
          <button type="button" className={clsx(value === true && 'on')} onClick={() => { onChange(true); onBlur() }}>Yes</button>
          <button type="button" className={clsx(value === false && 'on')} onClick={() => { onChange(false); onBlur() }}>No</button>
        </div>
      )}
      {q.type === 'textarea' && <textarea id={q.id} className="textarea" value={String(value ?? '')} placeholder={q.placeholder} onChange={(e) => onChange(e.target.value)} onBlur={onBlur} />}
      {(q.type === 'text' || q.type === 'date' || q.type === 'number') && (
        <input id={q.id} className="input" type={q.type} value={value === undefined ? '' : String(value)} placeholder={q.placeholder} autoComplete="off"
          onChange={(e) => onChange(q.type === 'number' ? (e.target.value === '' ? undefined : Number(e.target.value)) : q.id.endsWith('_codes') || q.id === 'member_id' || q.id === 'original_auth_number' ? e.target.value.toUpperCase() : e.target.value)}
          onBlur={onBlur} />
      )}
      {q.help && <div className="help">{q.help}</div>}
      {error && <div className="err">{error}</div>}
    </div>
  )
}

// ------------------------------------------------------------------------------ upload
function DropZone({ spec, required, staged, error, onChange }: { spec: DocumentSpec; required: boolean; staged: Staged[]; error?: string; onChange: (s: Staged[]) => void }) {
  const [drag, setDrag] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const latest = useRef(staged)
  latest.current = staged
  const max = spec.max_files ?? 5

  const add = async (list: FileList | null) => {
    if (!list) return
    const incoming = Array.from(list).slice(0, Math.max(0, max - staged.length)).map((file) => ({ key: `${file.name}-${file.size}-${Math.random()}`, file }))
    let current = [...latest.current, ...incoming]
    onChange(current)
    // Hash in the browser (Web Crypto); update each row as its digest resolves
    for (const s of incoming) {
      const sha = await sha256File(s.file)
      current = latest.current.map((x) => (x.key === s.key ? { ...x, sha256: sha } : x))
      onChange(current)
    }
  }

  return (
    <div className={clsx('dropzone reveal', drag && 'drag', error && !staged.length && 'missing')}
      onDragOver={(e) => { e.preventDefault(); setDrag(true) }} onDragLeave={() => setDrag(false)}
      onDrop={(e) => { e.preventDefault(); setDrag(false); add(e.dataTransfer.files) }}>
      <div className="spread">
        <div>
          <div className="row"><b>{spec.label}</b>{required ? <span className="badge b-red">required</span> : <span className="badge b-slate">optional</span>}{spec.visible_when && <span className="badge b-indigo">conditional</span>}</div>
          <div className="faint" style={{ fontSize: 12, marginTop: 3 }}>{spec.description} · up to {max} file{max > 1 ? 's' : ''}</div>
        </div>
        <button type="button" className="btn sm" disabled={staged.length >= max} onClick={() => input.current?.click()}><UploadCloud size={14} />Browse</button>
        <input ref={input} type="file" hidden multiple={max > 1} accept=".pdf,.png,.jpg,.jpeg,.txt" onChange={(e) => { add(e.target.files); e.target.value = '' }} />
      </div>
      {staged.length === 0 && <div className="faint" style={{ fontSize: 12.5, marginTop: 10 }}>Drag & drop files here</div>}
      {staged.map((s) => (
        <div key={s.key} className="file-row">
          <FileText size={16} color="var(--indigo)" />
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ fontWeight: 600, fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.file.name}</div>
            <div className="row" style={{ gap: 6, marginTop: 2 }}>
              <span className="faint" style={{ fontSize: 11.5 }}>{fmtBytes(s.file.size)}</span>
              {s.sha256 ? <Hash value={s.sha256} /> : <span className="faint" style={{ fontSize: 11.5 }}>fingerprinting…</span>}
            </div>
          </div>
          <button type="button" className="btn ghost sm" aria-label="Remove" onClick={() => onChange(staged.filter((x) => x.key !== s.key))}><Trash2 size={14} /></button>
        </div>
      ))}
      {error && <div className="err" style={{ marginTop: 8, fontSize: 12, color: 'var(--red)' }}>{error}</div>}
    </div>
  )
}

// ------------------------------------------------------------------------------ review & receipt
function Review({ qn, answers, docs, files }: { qn: Questionnaire; answers: Answers; docs: DocumentSpec[]; files: FilesByCode }) {
  const mask = (v: string) => (v.length <= 3 ? '•••' : v.slice(0, 1) + '•'.repeat(Math.min(10, v.length - 2)) + v.slice(-1))
  return (
    <div className="stack reveal">
      {qn.sections.map((s) => (
        <div key={s.id}>
          <div className="faint" style={{ fontSize: 11.5, textTransform: 'uppercase', letterSpacing: '.6px', marginBottom: 8 }}>{s.title}</div>
          <dl className="kv">
            {qn.questions.filter((q) => q.section === s.id && isVisible(q, answers) && answers[q.id] !== undefined).map((q) => (
              <div key={q.id} style={{ display: 'contents' }}>
                <dt>{q.label.length > 46 ? q.label.slice(0, 44) + '…' : q.label}</dt>
                <dd>{q.phi ? <span className="row"><span className="mono">{mask(String(answers[q.id]))}</span><Lock size={12} color="var(--indigo)" /></span>
                  : q.options ? q.options.find((o) => o.value === answers[q.id])?.label : typeof answers[q.id] === 'boolean' ? (answers[q.id] ? 'Yes' : 'No') : String(answers[q.id])}</dd>
              </div>
            ))}
          </dl>
        </div>
      ))}
      <div>
        <div className="faint" style={{ fontSize: 11.5, textTransform: 'uppercase', letterSpacing: '.6px', marginBottom: 8 }}>Documents & fingerprints</div>
        {docs.flatMap((d) => (files[d.code] ?? []).map((f) => (
          <div key={f.key} className="file-row"><Fingerprint size={16} color="var(--brand)" /><div style={{ flex: 1 }}><b style={{ fontSize: 13 }}>{f.file.name}</b><div className="faint" style={{ fontSize: 11.5 }}>{d.label}</div></div><Hash value={f.sha256} /></div>
        )))}
      </div>
      {evaluate({ q: 'is_urgent', op: 'truthy' }, answers) && <div className="alert warn"><Zap size={16} />Expedited: this request will use a non-pausable 72-hour SLA policy.</div>}
    </div>
  )
}

function Receipt({ t, onAnother }: { t: TxnDetail; onAnother: () => void }) {
  const nav = useNavigate()
  const due = t.sla.projected_due_at
  return (
    <>
      <PageHeader title="Intake accepted" subtitle="The master transaction record is durable, intake timestamps are immutable and the SLA clock is running." />
      <div className="grid g-main" style={{ alignItems: 'start' }}>
        <div className="stack">
          <div className="receipt">
            <div className="row" style={{ color: '#7ee8d9', fontWeight: 600 }}><ShieldCheck size={18} />Document Control Number issued</div>
            <div className="dcn" style={{ marginTop: 10 }}>{t.dcn}</div>
            <div className="row wrap" style={{ marginTop: 16, gap: 24, position: 'relative', zIndex: 1 }}>
              <div><div style={{ opacity: .6, fontSize: 12 }}>SLA policy</div><b>{t.sla.policy_code} v{t.sla.policy_version}</b></div>
              <div><div style={{ opacity: .6, fontSize: 12 }}>Due</div><b>{fmtDateTime(due)}</b></div>
              <div><div style={{ opacity: .6, fontSize: 12 }}>Priority</div><b>{t.priority}</b></div>
              <div><div style={{ opacity: .6, fontSize: 12 }}>Member token</div><b className="mono">{t.member_token}</b></div>
            </div>
          </div>
          <Card title="Integrity verification" sub="Browser SHA-256 vs. server SHA-256 (64KB streaming) vs. blob metadata">
            <table className="t">
              <thead><tr><th>File</th><th>Slot</th><th>Server SHA-256</th><th>Match</th></tr></thead>
              <tbody>
                {t.documents.filter((d) => !d.parent_document_id).map((d) => (
                  <tr key={d.id}><td><b>{d.filename}</b><div className="faint" style={{ fontSize: 12 }}>{fmtBytes(d.size_bytes)} · {d.content_type}</div></td><td>{humanize(d.requirement_code)}</td><td><Hash value={d.sha256} /></td>
                    <td>{d.client_sha256 === d.sha256 ? <span className="badge b-green"><CheckCircle2 size={12} />identical</span> : <span className="badge b-slate">server only</span>}</td></tr>
                ))}
              </tbody>
            </table>
          </Card>
        </div>
        <Card title="What happens next">
          <div className="timeline">
            {['Outbox event DocumentIngested committed with the DCN (no PHI)', 'Relay publishes it (SKIP LOCKED, at-least-once)', 'Classifier re-verifies hash → classifies (step 4)', 'Extractor re-verifies → derivative text + codes (step 5)', 'Reviewer decision re-verifies at steps 13 and 22'].map((s, i) => (
              <div key={i} className="tl-item neutral" style={{ fontSize: 13 }}>{s}</div>
            ))}
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <button className="btn primary" onClick={() => nav(`/pipeline?q=${t.dcn}`)}>Track in pipeline<ArrowRight size={15} /></button>
            <button className="btn" onClick={onAnother}><RotateCcw size={15} />New intake</button>
          </div>
        </Card>
      </div>
    </>
  )
}
