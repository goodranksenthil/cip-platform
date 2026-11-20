import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Eye, KeyRound, ShieldCheck, ShieldX } from 'lucide-react'
import { api, type ApiError, type Revealed } from '../api/client'
import { Card, PageHeader, Skeleton, useToast } from '../components/ui'
import { timeAgo } from '../lib/format'

const SAMPLE = `{
  "dcn": "DCN-20261119-000042",
  "transaction_id": "8f0c…",
  "request_type": "PRIOR_AUTH",
  "member_dob": "1984-03-12",
  "note": "call member at 555-201-7788"
}`

export default function Security() {
  const toast = useToast()
  const qc = useQueryClient()
  const members = useQuery({ queryKey: ['members'], queryFn: api.members })
  const audit = useQuery({ queryKey: ['access-audit'], queryFn: api.accessAudit })
  const allow = useQuery({ queryKey: ['phi-allowlist'], queryFn: api.phiAllowlist })
  const [role, setRole] = useState('analyst')
  const [revealed, setRevealed] = useState<Record<string, Revealed>>({})
  const [eventType, setEventType] = useState('DocumentIngested')
  const [payload, setPayload] = useState(SAMPLE)

  const reveal = useMutation({
    mutationFn: (token: string) => api.reveal(token, role, 'Utilization review of active case'),
    onSuccess: (r, token) => { setRevealed((x) => ({ ...x, [token]: r })); qc.invalidateQueries({ queryKey: ['access-audit'] }); toast(r.masked ? 'err' : 'ok', r.masked ? `Role "${role}" is not authorised — masked values returned (audited)` : 'Decrypted for authorised role (audited)') },
    onError: (e: ApiError) => toast('err', e.message),
  })
  const check = useMutation({
    mutationFn: () => { let p: object; try { p = JSON.parse(payload.replace(/…/g, '')) } catch { throw new Error('Payload is not valid JSON') } return api.phiCheck(eventType, p) },
    onError: (e: Error) => toast('err', e.message),
  })

  return (
    <>
      <PageHeader title="Data security"
        subtitle="Zone 2: highest-sensitivity member fields are AES-256 encrypted inside PostgreSQL with pgcrypto, with role-gated, audited decryption. Zone 3: a structural allowlist keeps PHI out of every event topic."
        reqs={['CIP-PA-274', 'CIP-PA-275']} />

      <div className="grid g-main" style={{ alignItems: 'start', marginBottom: 16 }}>
        <Card title="Zone 2 — encrypted member records" sub="What is physically stored is ciphertext (bytea)" action={
          <div className="row"><span className="faint" style={{ fontSize: 12.5 }}>Acting as</span>
            <select className="select" style={{ width: 240 }} value={role} onChange={(e) => setRole(e.target.value)}>
              <option value="analyst">analyst (no PHI access)</option>
              <option value="clinical_reviewer">clinical_reviewer</option>
              <option value="medical_director">medical_director</option>
            </select></div>
        }>
          {members.isLoading ? <Skeleton h={260} /> : (
            <div className="table-wrap">
              <table className="t">
                <thead><tr><th>Member token</th><th>member_dob_enc (stored)</th><th>Decrypted view</th><th /></tr></thead>
                <tbody>
                  {members.data?.slice(0, 10).map((m) => {
                    const r = revealed[m.member_token]
                    return (
                      <tr key={m.member_token}>
                        <td className="mono">{m.member_token}<div className="faint" style={{ fontSize: 11.5 }}>{m.transactions} txn</div></td>
                        <td><span className="hash">\x{m.dob_ciphertext_prefix_hex.slice(0, 22)}…</span><div className="faint" style={{ fontSize: 11 }}>{m.dob_ciphertext_bytes} bytes</div></td>
                        <td style={{ fontSize: 12.5 }}>{r ? <div><b>{r.name}</b> · {r.dob}<div className="faint">{r.member_id} · {r.address}</div>{r.masked && <span className="badge b-amber" style={{ marginTop: 4 }}>masked</span>}</div> : <span className="faint">—</span>}</td>
                        <td><button className="btn sm" onClick={() => reveal.mutate(m.member_token)}><Eye size={13} />Reveal</button></td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Card>
        <Card title="Access audit" sub="Every decrypt attempt, allowed or not">
          <div style={{ maxHeight: 440, overflowY: 'auto' }}>
            {!audit.data?.length && <div className="faint">No decrypt attempts yet — try “Reveal”.</div>}
            {audit.data?.map((a, i) => (
              <div key={i} className="row" style={{ padding: '8px 0', borderBottom: '1px solid var(--border)', alignItems: 'flex-start' }}>
                {a.allowed ? <ShieldCheck size={16} color="var(--green)" /> : <ShieldX size={16} color="var(--red)" />}
                <div style={{ flex: 1, fontSize: 12.5 }}><b>{a.actor_role}</b> {a.action.toLowerCase()} <span className="mono">{a.resource.replace('member:', '')}</span><div className="faint">{a.purpose}</div></div>
                <span className="faint" style={{ fontSize: 11.5 }}>{timeAgo(a.at)}</span>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <div className="grid g-2" style={{ alignItems: 'start' }}>
        <Card title="Zone 3 — PHI exclusion tester" sub="The exact guard the outbox runs before an event row is written">
          <div className="stack" style={{ gap: 12 }}>
            <select className="select" value={eventType} onChange={(e) => setEventType(e.target.value)}>
              {Object.keys(allow.data?.allowlist ?? {}).map((k) => <option key={k}>{k}</option>)}
            </select>
            <textarea className="textarea mono" style={{ minHeight: 170 }} value={payload} onChange={(e) => setPayload(e.target.value)} />
            <div className="row"><button className="btn primary" onClick={() => check.mutate()}><KeyRound size={15} />Check payload</button>
              <button className="btn" onClick={() => setPayload('{\n  "dcn": "DCN-20261119-000042",\n  "request_type": "PRIOR_AUTH",\n  "priority": "STANDARD"\n}')}>Load safe example</button></div>
            {check.data && (check.data.allowed
              ? <div className="alert ok"><ShieldCheck size={16} />Allowed — payload may be published.</div>
              : <div className="alert err"><ShieldX size={16} /><div><b>Rejected ({check.data.problems.length})</b><ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>{check.data.problems.map((p) => <li key={p}>{p}</li>)}</ul></div></div>)}
          </div>
        </Card>
        <Card title="Event field allowlist" sub={`Content scan also blocks: ${allow.data?.patterns.join(', ') ?? ''}`}>
          <div className="stack" style={{ gap: 12 }}>
            {Object.entries(allow.data?.allowlist ?? {}).map(([k, v]) => (
              <div key={k}><div style={{ fontWeight: 650, fontSize: 13, marginBottom: 4 }}>{k}</div>{v.map((f) => <span key={f} className="chip">{f}</span>)}</div>
            ))}
          </div>
        </Card>
      </div>
    </>
  )
}
