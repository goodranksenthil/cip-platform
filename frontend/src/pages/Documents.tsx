import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bomb, FileSearch, Fingerprint, GitBranch, RefreshCw, ScanText, ShieldCheck } from 'lucide-react'
import { api, type ApiError } from '../api/client'
import { Card, Drawer, Empty, Hash, Kpi, PageHeader, Pill, Skeleton, useToast } from '../components/ui'
import { fmtBytes, fmtDateTime, humanize } from '../lib/format'

const STAGES = [
  { n: 1, label: 'Ingestion', note: 'hash computed pre-transform' },
  { n: 4, label: 'Classify', note: 're-verify → classify' },
  { n: 5, label: 'Extract', note: 're-verify → derivative + codes' },
  { n: 13, label: 'Clinical review', note: 're-verify before review' },
  { n: 22, label: 'Decision', note: 're-verify before determination' },
]

export default function Documents() {
  const [kind, setKind] = useState('SOURCE')
  const [state, setState] = useState('')
  const [docClass, setDocClass] = useState('')
  const [open, setOpen] = useState<string | null>(null)
  const { data, isLoading } = useQuery({ queryKey: ['documents', kind, state, docClass], queryFn: () => api.documents({ kind: kind || undefined, state: state || undefined, doc_class: docClass || undefined, limit: 300 }) })
  const all = useQuery({ queryKey: ['documents', 'all'], queryFn: () => api.documents({ limit: 500 }) })

  const stats = useMemo(() => {
    const d = all.data ?? []
    const src = d.filter((x) => x.artifact_kind === 'SOURCE')
    const conf = src.filter((x) => x.confidence != null)
    return {
      sources: src.length, derived: d.length - src.length, failed: d.filter((x) => x.integrity_state === 'FAILED').length,
      avgConf: conf.length ? Math.round((100 * conf.reduce((s, x) => s + (x.confidence ?? 0), 0)) / conf.length) : 0,
      codes: new Set(src.flatMap((x) => [...(x.extracted.icd10_codes ?? []), ...(x.extracted.cpt_codes ?? [])])).size,
    }
  }, [all.data])
  const classes = useMemo(() => [...new Set((all.data ?? []).map((d) => d.doc_class).filter(Boolean))] as string[], [all.data])

  return (
    <>
      <PageHeader title="Classify & extract"
        subtitle="Every downstream read recomputes the SHA-256 (streamed in 64KB chunks) and compares it with the DCN record and the blob metadata. A mismatch quarantines the transaction."
        reqs={['CIP-PA-014', 'CIP-PA-025', 'CIP-PA-283', 'CIP-PA-003']} />

      <Card title="Integrity checkpoints" sub="Pipeline steps where the fingerprint is re-proven" className="" bodyClass="">
        <div className="row" style={{ gap: 0, overflowX: 'auto' }}>
          {STAGES.map((s, i) => (
            <div key={s.n} className="row" style={{ gap: 0, flex: 1, minWidth: 150 }}>
              <div style={{ textAlign: 'center', flex: 1 }}>
                <div style={{ width: 44, height: 44, borderRadius: 12, margin: '0 auto', display: 'grid', placeItems: 'center', background: 'var(--brand-soft)', color: 'var(--brand)', fontWeight: 700 }}>{s.n}</div>
                <div style={{ fontWeight: 650, marginTop: 8 }}>{s.label}</div>
                <div className="faint" style={{ fontSize: 11.5 }}>{s.note}</div>
              </div>
              {i < STAGES.length - 1 && <div style={{ height: 2, flex: .4, background: 'linear-gradient(90deg, var(--brand), var(--indigo))', opacity: .35 }} />}
            </div>
          ))}
        </div>
      </Card>

      <div className="grid g-4" style={{ margin: '16px 0' }}>
        <Kpi label="Source artifacts" value={stats.sources} foot={`${stats.derived} derivative artifacts with lineage`} icon={<FileSearch size={16} />} />
        <Kpi label="Avg. classification confidence" value={`${stats.avgConf}%`} foot="Explainable weighted-keyword model" icon={<ScanText size={16} />} tone="indigo" />
        <Kpi label="Distinct codes extracted" value={stats.codes} foot="ICD-10 + CPT, de-identified" icon={<GitBranch size={16} />} tone="amber" />
        <Kpi label="Integrity failures" value={stats.failed} foot="Blocked from clinical processing" icon={<Fingerprint size={16} />} tone={stats.failed ? 'red' : 'green'} />
      </div>

      <Card title="Artifacts" action={
        <div className="row">
          <select className="select" style={{ width: 150 }} value={kind} onChange={(e) => setKind(e.target.value)}><option value="">All kinds</option><option value="SOURCE">Source</option><option value="DERIVATIVE_TEXT">Derivative text</option></select>
          <select className="select" style={{ width: 150 }} value={state} onChange={(e) => setState(e.target.value)}><option value="">Any integrity</option><option value="VERIFIED">Verified</option><option value="FAILED">Failed</option></select>
          <select className="select" style={{ width: 170 }} value={docClass} onChange={(e) => setDocClass(e.target.value)}><option value="">All classes</option>{classes.map((c) => <option key={c} value={c}>{humanize(c)}</option>)}</select>
        </div>
      } bodyClass="" >
        {isLoading ? <Skeleton h={300} /> : !data?.length ? <Empty>No documents match.</Empty> : (
          <div className="table-wrap">
            <table className="t">
              <thead><tr><th>Document</th><th>DCN</th><th>Class</th><th>Codes</th><th>SHA-256</th><th>Integrity</th></tr></thead>
              <tbody>
                {data.map((d) => (
                  <tr key={d.id} className="click" onClick={() => setOpen(d.id)}>
                    <td><b>{d.filename}</b><div className="faint" style={{ fontSize: 12 }}>{fmtBytes(d.size_bytes)} · {humanize(d.artifact_kind)}</div></td>
                    <td className="mono">{d.dcn}</td>
                    <td>{d.doc_class ? <><div>{humanize(d.doc_class)}</div>{d.confidence != null && <div className="faint num" style={{ fontSize: 12 }}>{Math.round(d.confidence * 100)}% conf.</div>}</> : <span className="faint">—</span>}</td>
                    <td style={{ maxWidth: 220 }}>{[...(d.extracted.icd10_codes ?? []), ...(d.extracted.cpt_codes ?? [])].slice(0, 4).map((c) => <span key={c} className="chip">{c}</span>)}</td>
                    <td><Hash value={d.sha256} /></td>
                    <td><Pill value={d.integrity_state} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <DocumentDrawer id={open} onClose={() => setOpen(null)} />
    </>
  )
}

function DocumentDrawer({ id, onClose }: { id: string | null; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const { data: d } = useQuery({ queryKey: ['document', id], queryFn: () => api.document(id!), enabled: !!id })
  const refresh = () => { qc.invalidateQueries({ queryKey: ['document', id] }); qc.invalidateQueries({ queryKey: ['documents'] }); qc.invalidateQueries({ queryKey: ['dashboard'] }) }
  const verify = useMutation({ mutationFn: () => api.verify(id!), onSuccess: (c) => { refresh(); toast(c.passed ? 'ok' : 'err', c.passed ? 'Integrity verified — all three fingerprints agree' : `Integrity FAILED: ${c.detail}`) }, onError: (e: ApiError) => toast('err', e.message) })
  const tamper = useMutation({ mutationFn: () => api.tamper(id!), onSuccess: (r) => toast('ok', r.message), onError: (e: ApiError) => toast('err', e.message) })

  return (
    <Drawer open={!!id} onClose={onClose} title={d ? <div><div className="row"><b>{d.filename}</b><Pill value={d.integrity_state} /></div><div className="faint mono" style={{ marginTop: 4 }}>{d.dcn}</div></div> : 'Loading…'}>
      {!d ? <Skeleton h={300} /> : (
        <div className="stack">
          <div className="row">
            <button className="btn primary" onClick={() => verify.mutate()} disabled={verify.isPending}><RefreshCw size={15} />Re-verify now</button>
            {d.artifact_kind === 'SOURCE' && <button className="btn danger" onClick={() => tamper.mutate()}><Bomb size={15} />Simulate tampering (demo)</button>}
          </div>
          <Card title="Three-way fingerprint" sub="All must be identical for processing to continue">
            <dl className="kv">
              <dt>DCN record (wip_db)</dt><dd><Hash value={d.sha256} full /></dd>
              <dt>Blob metadata</dt><dd><Hash value={d.blob_metadata.sha256} full /></dd>
              <dt>Client (browser)</dt><dd>{d.client_sha256 ? <Hash value={d.client_sha256} full /> : <span className="faint">not supplied</span>}</dd>
            </dl>
          </Card>
          <Card title="Blob metadata" sub="x-ms-meta-* headers on the stored object">
            <pre className="code">{JSON.stringify(d.blob_metadata, null, 2)}</pre>
          </Card>
          {d.children.length > 0 && (
            <Card title="Lineage" sub="Derivative artifacts produced from this source">
              {d.children.map((c) => <div key={c.id} className="file-row"><GitBranch size={15} /><div style={{ flex: 1 }}>{c.filename}<div className="faint" style={{ fontSize: 12 }}>{humanize(c.artifact_kind)} · {fmtBytes(c.size_bytes)}</div></div><Hash value={c.sha256} /></div>)}
            </Card>
          )}
          <Card title="Verification history" sub="Append-only — enforced by a database trigger">
            <div className="timeline">
              {d.integrity_checks?.map((c) => (
                <div key={c.id} className={c.passed ? 'tl-item' : 'tl-item fail'}>
                  <div className="row"><b>{c.stage}</b>{c.passed ? <span className="badge b-green"><ShieldCheck size={11} />pass</span> : <span className="badge b-red">fail</span>}<span className="faint num" style={{ fontSize: 12 }}>{fmtDateTime(c.checked_at)}</span></div>
                  <div className="faint" style={{ fontSize: 12 }}>{c.detail}</div>
                  {!c.passed && c.actual_sha256 && <div style={{ fontSize: 12 }}>recomputed: <Hash value={c.actual_sha256} /></div>}
                </div>
              ))}
            </div>
          </Card>
        </div>
      )}
    </Drawer>
  )
}
