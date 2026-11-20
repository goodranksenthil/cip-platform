import { useEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import clsx from 'clsx'
import {
  ArrowLeft, ArrowRight, CheckCircle2, CircleDashed, Clock, FileText, FileType2, Fingerprint, Lock, RotateCcw, ScanText,
  Send, ShieldCheck, Sparkles, Trash2, TriangleAlert, UploadCloud, Zap,
} from 'lucide-react'
import { ApiError, api } from '../../api/client'
import { Card, Hash, useToast } from '../../components/ui'
import { fmtBytes, fmtDateTime, fmtHours, humanize } from '../../lib/format'
import { sha256File } from '../../lib/hash'
import { isRequired, isVisible, visibleAnswers } from '../../lib/rules'
import { FieldRenderer } from '../Ingestion'
import { Provenance, sectionQuestions, STEPS, useFlow } from './UploadFlow'

const ACCEPT = '.pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document'

// =============================================================================== shared bits
function Nav({ back, next, nextLabel = 'Continue', onNext, disabled }: { back?: string; next?: string; nextLabel?: string; onNext?: () => boolean | void; disabled?: boolean }) {
  const nav = useNavigate()
  return (
    <div className="spread" style={{ borderTop: '1px solid var(--border)', paddingTop: 16, marginTop: 18 }}>
      <button className="btn" disabled={!back} onClick={() => back && nav(back)}><ArrowLeft size={15} />Back</button>
      <button className="btn primary" disabled={disabled} onClick={() => { if (onNext?.() === false) return; if (next) nav(next) }}>
        {nextLabel}<ArrowRight size={15} />
      </button>
    </div>
  )
}

function Summary() {
  const { analysis, answers, docErrors, qn } = useFlow()
  if (!analysis) return null
  const found = Object.keys(analysis.fields).length
  const urgent = answers.is_urgent === true
  const required = qn.documents.filter((d) => isVisible(d, answers) && isRequired(d, answers))
  return (
    <div className="stack" style={{ position: 'sticky', top: 80 }}>
      <Card title="What CIP read" sub={`${analysis.files.length} document(s) · ${found} fields pre-filled`}>
        <div className="stack" style={{ gap: 10 }}>
          {analysis.files.map((f) => (
            <div key={f.file_id} className="row" style={{ alignItems: 'flex-start' }}>
              <FileType2 size={16} color={f.error ? 'var(--red)' : 'var(--indigo)'} />
              <div style={{ minWidth: 0, flex: 1, fontSize: 12.5 }}>
                <b style={{ wordBreak: 'break-all' }}>{f.filename}</b>
                <div className="faint">{f.error ? f.error : `${humanize(f.doc_class)} · ${f.fields_found.length} fields · ${f.characters.toLocaleString()} chars`}</div>
              </div>
            </div>
          ))}
        </div>
      </Card>
      <Card title="Live assessment">
        <div className="stack" style={{ gap: 10, fontSize: 13 }}>
          <div className="row">{urgent ? <Zap size={16} color="var(--amber)" /> : <Clock size={16} />}<b>{urgent ? 'EXPEDITED' : 'STANDARD'} review</b></div>
          {required.map((d) => (
            <div key={d.code} className="row">{docErrors[d.code] ? <CircleDashed size={15} color="var(--text-3)" /> : <CheckCircle2 size={15} color="var(--green)" />}<span className={docErrors[d.code] ? 'muted' : ''}>{d.label}</span></div>
          ))}
          <div className="alert ok" style={{ fontSize: 12 }}><Lock size={14} /><span>Extracted PHI is held in memory only and encrypted with pgcrypto on submit.</span></div>
        </div>
      </Card>
    </div>
  )
}

// =============================================================================== 1. upload & read
export function UploadStep() {
  const { analysis, setAnalysis, reset, picked, setPicked, receipt } = useFlow()
  // coming back after a submitted intake starts a fresh one
  useEffect(() => { if (receipt) reset() }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const [drag, setDrag] = useState(false)
  const [progress, setProgress] = useState(0)
  const [rejected, setRejected] = useState<Record<string, string>>({})
  const input = useRef<HTMLInputElement>(null)
  const latest = useRef(picked)
  latest.current = picked
  const nav = useNavigate()
  const toast = useToast()

  const add = async (list: FileList | null) => {
    if (!list) return
    const bad: Record<string, string> = {}
    const ok = Array.from(list).filter((f) => {
      const n = f.name.toLowerCase()
      if (n.endsWith('.doc')) { bad[f.name] = 'Legacy .doc — please save as .docx'; return false }
      if (!n.endsWith('.pdf') && !n.endsWith('.docx')) { bad[f.name] = 'Only PDF or Word (.docx)'; return false }
      if (f.size > 25 * 1024 * 1024) { bad[f.name] = 'Larger than 25 MB'; return false }
      return true
    }).slice(0, Math.max(0, 10 - latest.current.length))
    setRejected(bad)
    const incoming = ok.map((file) => ({ key: `${file.name}-${file.size}-${Math.random()}`, file }))
    setPicked((p) => [...p, ...incoming])
    for (const s of incoming) {
      const sha = await sha256File(s.file)
      setPicked((p) => p.map((x) => (x.key === s.key ? { ...x, sha256: sha } : x)))
    }
  }

  const read = useMutation({
    mutationFn: () => api.smartAnalyze(picked.map((p) => ({ file: p.file, sha256: p.sha256! })), setProgress),
    onSuccess: (a) => {
      setAnalysis(a)
      setRejected(a.rejected)
      toast('ok', `Read ${a.files.length} document(s) — ${Object.keys(a.fields).length} fields pre-filled`)
      nav('/upload/request')
    },
    onError: (e: ApiError) => { setRejected(e.fieldErrors); toast('err', e.message) },
  })

  return (
    <div className="grid g-main" style={{ alignItems: 'start' }}>
      <Card title="Upload clinical documents" sub="PDF or Word (.docx) · up to 10 files · 25 MB each · type verified by file signature">
        <div className={clsx('dropzone', drag && 'drag')} style={{ padding: 34, textAlign: 'center' }}
          onDragOver={(e) => { e.preventDefault(); setDrag(true) }} onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); add(e.dataTransfer.files) }}>
          <UploadCloud size={36} color="var(--brand)" />
          <div style={{ fontWeight: 650, fontSize: 15, marginTop: 8 }}>Drag & drop referral, notes or denial letter</div>
          <div className="faint" style={{ fontSize: 12.5, margin: '4px 0 14px' }}>CIP extracts text from PDF and Word, then pre-fills the intake</div>
          <button className="btn" onClick={() => input.current?.click()}><FileText size={15} />Choose files</button>
          <input ref={input} type="file" hidden multiple accept={ACCEPT} data-testid="smart-upload-input" onChange={(e) => { add(e.target.files); e.target.value = '' }} />
        </div>

        {Object.entries(rejected).map(([n, m]) => <div key={n} className="alert err" style={{ marginTop: 10 }}><TriangleAlert size={15} /><span><b>{n}</b>: {m}</span></div>)}

        {picked.map((p) => (
          <div key={p.key} className="file-row">
            <FileType2 size={18} color={p.file.name.toLowerCase().endsWith('.pdf') ? 'var(--red)' : 'var(--indigo)'} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 600, fontSize: 13 }}>{p.file.name}</div>
              <div className="row" style={{ gap: 6 }}><span className="faint" style={{ fontSize: 11.5 }}>{fmtBytes(p.file.size)} · {p.file.name.toLowerCase().endsWith('.pdf') ? 'PDF' : 'Word'}</span>
                {p.sha256 ? <Hash value={p.sha256} /> : <span className="faint" style={{ fontSize: 11.5 }}>fingerprinting…</span>}</div>
            </div>
            <button className="btn ghost sm" aria-label="Remove" onClick={() => setPicked((x) => x.filter((y) => y.key !== p.key))}><Trash2 size={14} /></button>
          </div>
        ))}

        {read.isPending && (
          <div style={{ marginTop: 14 }}>
            <div className="spread" style={{ fontSize: 12.5, marginBottom: 6 }}><span>{progress < 100 ? 'Uploading…' : 'Reading documents and extracting fields…'}</span><span className="num">{progress}%</span></div>
            <div className="bar"><span style={{ width: `${progress}%`, background: 'var(--brand)' }} /></div>
          </div>
        )}

        <div className="spread" style={{ borderTop: '1px solid var(--border)', paddingTop: 16, marginTop: 18 }}>
          {analysis ? <button className="btn" onClick={reset}><RotateCcw size={15} />Start over</button> : <span />}
          <div className="row">
            {analysis && <button className="btn" onClick={() => nav('/upload/request')}>Continue with current<ArrowRight size={15} /></button>}
            <button className="btn primary" disabled={!picked.length || picked.some((p) => !p.sha256) || read.isPending} onClick={() => read.mutate()}>
              <ScanText size={15} />{analysis ? 'Read all documents again' : 'Read documents & pre-fill'}
            </button>
          </div>
        </div>
      </Card>
      <Card title="How it works">
        <div className="timeline">
          {['Each file is fingerprinted (SHA-256) in your browser', 'Server re-hashes in 64KB chunks, checks the file signature and stages it',
            'Text is read from PDF / Word and classified', 'Request, member & provider fields are pre-filled with confidence and evidence',
            'You confirm each page; submit uses the same ingestion engine (DCN, SLA, encryption, events)'].map((s, i) => (
            <div key={i} className="tl-item neutral" style={{ fontSize: 13 }}>{s}</div>
          ))}
        </div>
      </Card>
    </div>
  )
}

// =============================================================================== 2 & 3. question sections
export function SectionStep({ section }: { section: 'request' | 'member' }) {
  const { qn, answers, setAnswer, sectionErrors, analysis } = useFlow()
  const [touched, setTouched] = useState(false)
  const errs = sectionErrors(section)
  const meta = qn.sections.find((s) => s.id === section)!
  const i = STEPS.findIndex((s) => 'section' in s && s.section === section)
  const prefilled = sectionQuestions(qn, section, answers).filter((q) => analysis?.fields[q.id]).length
  return (
    <div className="grid g-main" style={{ alignItems: 'start' }}>
      <Card title={meta.title} sub={meta.description} action={<span className="badge b-indigo"><Sparkles size={12} />{prefilled} pre-filled</span>}>
        <div className="stack" style={{ gap: 18 }}>
          {sectionQuestions(qn, section, answers).map((q) => (
            <div key={q.id}>
              <FieldRenderer q={q} value={answers[q.id]} onChange={(v) => setAnswer(q.id, v)} required={isRequired(q, answers)}
                error={touched ? errs[q.id] : undefined} onBlur={() => {}} />
              <Provenance id={q.id} />
            </div>
          ))}
        </div>
        <Nav back={STEPS[i - 1].path} next={STEPS[i + 1].path}
          onNext={() => { setTouched(true); return Object.keys(errs).length === 0 }} />
      </Card>
      <Summary />
    </div>
  )
}

// =============================================================================== 4. documents
export function DocumentsStep() {
  const { qn, analysis, answers, setAnswer, assignments, setAssignments, docErrors, sectionErrors } = useFlow()
  const [touched, setTouched] = useState(false)
  if (!analysis) return null
  const slots = qn.documents.filter((d) => isVisible(d, answers))
  const files = analysis.files.filter((f) => !f.error)
  const toggle = (fileId: string, code: string) => {
    const cur = new Set(assignments[fileId] ?? [])
    if (cur.has(code)) cur.delete(code); else cur.add(code)
    setAssignments({ ...assignments, [fileId]: [...cur] })
  }
  const clinicalQ = qn.questions.filter((q) => q.section === 'clinical')
  const errs = { ...sectionErrors('clinical'), ...docErrors }
  return (
    <div className="grid g-main" style={{ alignItems: 'start' }}>
      <Card title="Assign documents" sub="CIP suggested a slot for each file from its content. One file may cover several slots (e.g. a referral that is also the imaging order).">
        <div className="stack" style={{ gap: 16 }}>
          {clinicalQ.map((q) => (
            <div key={q.id}><FieldRenderer q={q} value={answers[q.id]} onChange={(v) => setAnswer(q.id, v)} required={isRequired(q, answers)} error={touched ? errs[q.id] : undefined} onBlur={() => {}} /><Provenance id={q.id} /></div>
          ))}
          {files.map((f) => (
            <div key={f.file_id} className="dropzone" style={{ borderStyle: 'solid', background: 'var(--surface)' }}>
              <div className="row"><FileType2 size={17} color="var(--indigo)" /><b style={{ fontSize: 13.5 }}>{f.filename}</b>
                <span className="badge b-indigo">{humanize(f.doc_class)} · {Math.round(f.confidence * 100)}%</span></div>
              <div className="row wrap" style={{ marginTop: 10, gap: 8 }}>
                {slots.map((s) => {
                  const on = (assignments[f.file_id] ?? []).includes(s.code)
                  return (
                    <label key={s.code} className={clsx('choice', on && 'on')} style={{ padding: '7px 11px', fontSize: 12.5, cursor: 'pointer' }}>
                      <input type="checkbox" aria-label={`${f.filename} as ${s.label}`} checked={on} onChange={() => toggle(f.file_id, s.code)} style={{ accentColor: 'var(--brand)' }} />
                      {s.label}{isRequired(s, answers) && <span className="req">*</span>}
                      {(f.suggested_slots ?? [f.suggested_slot]).includes(s.code) && <span className="faint" style={{ fontSize: 10.5, fontWeight: 500 }}>suggested</span>}
                    </label>
                  )
                })}
              </div>
            </div>
          ))}
          {touched && Object.entries(docErrors).map(([k, m]) => <div key={k} className="err" style={{ color: 'var(--red)', fontSize: 12.5 }}>{m}</div>)}
          <div className="alert info" style={{ fontSize: 12.5 }}><TriangleAlert size={15} /><span>Missing a required document? Go back to <b>Upload & read</b>, add it and read again — values you edited by hand are kept.</span></div>
        </div>
        <Nav back="/upload/member" next="/upload/review" onNext={() => { setTouched(true); return Object.keys(errs).length === 0 }} />
      </Card>
      <Summary />
    </div>
  )
}

// =============================================================================== 5. review & submit
export function ReviewStep() {
  const { qn, analysis, answers, assignments, receipt, setReceipt, sectionErrors, docErrors, reset } = useFlow()
  const toast = useToast()
  const qc = useQueryClient()
  const nav = useNavigate()
  const submit = useMutation({
    mutationFn: () => api.smartSubmit(analysis!.batch_id, visibleAnswers(qn.questions, answers),
      Object.entries(assignments).flatMap(([file_id, codes]) => codes.filter((c) => qn.documents.some((d) => d.code === c && isVisible(d, answers))).map((requirement_code) => ({ file_id, requirement_code })))),
    onSuccess: (t) => { setReceipt(t); qc.invalidateQueries({ queryKey: ['dashboard'] }); toast('ok', `${t.dcn} created — SLA clock started`) },
    onError: (e: ApiError) => toast('err', Object.values(e.fieldErrors)[0] ?? e.message),
  })

  if (receipt) {
    return (
      <div className="grid g-main" style={{ alignItems: 'start' }}>
        <div className="stack">
          <div className="receipt">
            <div className="row" style={{ color: '#7ee8d9', fontWeight: 600 }}><ShieldCheck size={18} />Document Control Number issued from uploaded documents</div>
            <div className="dcn" style={{ marginTop: 10 }}>{receipt.dcn}</div>
            <div className="row wrap" style={{ marginTop: 16, gap: 24, position: 'relative', zIndex: 1 }}>
              <div><div style={{ opacity: .6, fontSize: 12 }}>SLA policy</div><b>{receipt.sla.policy_code} v{receipt.sla.policy_version}</b></div>
              <div><div style={{ opacity: .6, fontSize: 12 }}>Due</div><b>{fmtDateTime(receipt.sla.projected_due_at)}</b></div>
              <div><div style={{ opacity: .6, fontSize: 12 }}>Priority</div><b>{receipt.priority}</b></div>
            </div>
          </div>
          <Card title="Stored documents" sub="Fingerprint verified browser → staging → DCN record">
            <table className="t"><tbody>
              {receipt.documents.filter((d) => !d.parent_document_id).map((d) => (
                <tr key={d.id}><td><b>{d.filename}</b><div className="faint" style={{ fontSize: 12 }}>{humanize(d.requirement_code)}</div></td><td><Hash value={d.sha256} /></td>
                  <td>{d.client_sha256 === d.sha256 && <span className="badge b-green"><CheckCircle2 size={12} />identical</span>}</td></tr>
              ))}
            </tbody></table>
          </Card>
        </div>
        <Card title="Next">
          <div className="row">
            <button className="btn primary" onClick={() => nav(`/pipeline?q=${receipt.dcn}`)}>Track in pipeline<ArrowRight size={15} /></button>
            <button className="btn" onClick={() => { reset(); nav('/upload') }}><RotateCcw size={15} />Upload more</button>
          </div>
        </Card>
      </div>
    )
  }
  if (!analysis) return null

  const problems = { ...sectionErrors('request'), ...sectionErrors('member'), ...sectionErrors('clinical'), ...docErrors }
  const mask = (v: string) => (v.length <= 3 ? '•••' : v[0] + '•'.repeat(Math.min(10, v.length - 2)) + v.slice(-1))
  const fileName = (id: string) => analysis.files.find((f) => f.file_id === id)?.filename ?? id
  return (
    <div className="grid g-main" style={{ alignItems: 'start' }}>
      <Card title="Review & submit" sub="Confirm everything CIP read before the DCN is issued and the SLA clock starts">
        <div className="stack">
          {Object.keys(problems).length > 0 && (
            <div className="alert err"><TriangleAlert size={16} /><div><b>{Object.keys(problems).length} item(s) need attention</b><ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>{Object.entries(problems).map(([k, m]) => <li key={k}>{qn.questions.find((q) => q.id === k)?.label ?? humanize(k)}: {m}</li>)}</ul></div></div>
          )}
          {qn.sections.map((s) => (
            <div key={s.id}>
              <div className="spread"><div className="faint" style={{ fontSize: 11.5, textTransform: 'uppercase', letterSpacing: '.6px', marginBottom: 8 }}>{s.title}</div>
                <button className="btn sm ghost" onClick={() => nav(s.id === 'request' ? '/upload/request' : s.id === 'member' ? '/upload/member' : '/upload/documents')}>Edit</button></div>
              <dl className="kv">
                {sectionQuestions(qn, s.id, answers).filter((q) => answers[q.id] !== undefined && answers[q.id] !== '').map((q) => (
                  <div key={q.id} style={{ display: 'contents' }}>
                    <dt>{q.label.length > 46 ? q.label.slice(0, 44) + '…' : q.label}</dt>
                    <dd className="row" style={{ gap: 6 }}>
                      {q.phi ? <><span className="mono">{mask(String(answers[q.id]))}</span><Lock size={12} color="var(--indigo)" /></>
                        : q.options ? q.options.find((o) => o.value === answers[q.id])?.label : typeof answers[q.id] === 'boolean' ? (answers[q.id] ? 'Yes' : 'No') : String(answers[q.id])}
                      {analysis.fields[q.id] && <Sparkles size={12} color="var(--indigo)" aria-label="auto-filled" />}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>
          ))}
          <div>
            <div className="faint" style={{ fontSize: 11.5, textTransform: 'uppercase', letterSpacing: '.6px', marginBottom: 8 }}>Documents & fingerprints</div>
            {Object.entries(assignments).flatMap(([id, codes]) => codes.map((c) => (
              <div key={id + c} className="file-row"><Fingerprint size={16} color="var(--brand)" /><div style={{ flex: 1 }}><b style={{ fontSize: 13 }}>{fileName(id)}</b><div className="faint" style={{ fontSize: 11.5 }}>{qn.documents.find((d) => d.code === c)?.label}</div></div>
                <Hash value={analysis.files.find((f) => f.file_id === id)?.sha256} /></div>
            )))}
          </div>
          {answers.is_urgent === true && analysis.sla_policy && <div className="alert warn"><Zap size={16} />Expedited — {analysis.sla_policy.code.split(':')[0].replace('_', ' ').toLowerCase()} policy target {fmtHours(analysis.sla_policy.target_hours)}.</div>}
        </div>
        <div className="spread" style={{ borderTop: '1px solid var(--border)', paddingTop: 16, marginTop: 18 }}>
          <button className="btn" onClick={() => nav('/upload/documents')}><ArrowLeft size={15} />Back</button>
          <button className="btn primary" disabled={submit.isPending || Object.keys(problems).length > 0} onClick={() => submit.mutate()}><Send size={15} />Submit & issue DCN</button>
        </div>
      </Card>
      <div className="stack"><Summary /><Card title="Status">{Object.keys(problems).length
        ? <span className="badge b-amber">{Object.keys(problems).length} item(s) need attention</span>
        : <span className="badge b-green"><CheckCircle2 size={12} />Ready to submit</span>}</Card></div>
    </div>
  )
}
