import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ArrowUpRight, Code2 } from 'lucide-react'
import { api } from '../api/client'
import { Card, PageHeader, Skeleton } from '../components/ui'

export default function Requirements() {
  const { data, isLoading } = useQuery({ queryKey: ['requirements'], queryFn: api.requirements, staleTime: Infinity })
  const [module, setModule] = useState('')
  const modules = useMemo(() => [...new Set(data?.map((r) => r.module))], [data])
  const rows = (data ?? []).filter((r) => !module || r.module === module)

  return (
    <>
      <PageHeader title="Requirements traceability"
        subtitle="Each backlog requirement from the source sheet mapped to the code that implements it, the evidence, and the screen where you can see it working."
        actions={<select className="select" style={{ width: 220 }} value={module} onChange={(e) => setModule(e.target.value)}><option value="">All modules</option>{modules.map((m) => <option key={m}>{m}</option>)}</select>} />
      {isLoading ? <Skeleton h={400} /> : (
        <Card title={`${rows.length} requirements`} sub="Backlog status is as given in the source sheet; this sample implements all of them">
          <div className="table-wrap">
            <table className="t">
              <thead><tr><th>ID</th><th>Requirement</th><th>Implemented in</th><th>Backlog status</th><th>Sample</th><th /></tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id}>
                    <td><div className="mono" style={{ fontWeight: 600 }}>{r.id}</div><div className="faint" style={{ fontSize: 12 }}>{r.module} · P{r.priority}</div></td>
                    <td style={{ maxWidth: 420 }}><div style={{ fontWeight: 600 }}>{r.title}</div><div className="muted" style={{ fontSize: 12.5, marginTop: 3, lineHeight: 1.45 }}>{r.evidence}</div></td>
                    <td>{r.implementation.map((f) => <div key={f} className="row" style={{ gap: 6, fontSize: 12 }}><Code2 size={12} color="var(--text-3)" /><span className="mono">{f}</span></div>)}</td>
                    <td><span className={`badge ${r.backlog_status === 'Dev Completed' ? 'b-green' : r.backlog_status === 'Partially Completed' ? 'b-amber' : 'b-slate'}`}>{r.backlog_status}</span></td>
                    <td><span className="badge b-brand">Implemented</span></td>
                    <td><Link className="btn sm" to={r.route}>Open<ArrowUpRight size={13} /></Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </>
  )
}
