import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { CheckCircle2, Play, TriangleAlert } from 'lucide-react'
import { api } from '../api/client'
import { Card, PageHeader, Pill, ProgressBar, Skeleton, useToast } from '../components/ui'
import { fmtDateTime } from '../lib/format'
import { AXIS, GRID, SERIES } from '../lib/palette'

function Ring({ value }: { value: number }) {
  const r = 62, c = 2 * Math.PI * r
  const color = value >= 99 ? 'var(--green)' : value >= 95 ? 'var(--amber)' : 'var(--red)'
  return (
    <div className="score-ring">
      <svg width="150" height="150" viewBox="0 0 150 150" role="img" aria-label={`Overall score ${value}`}>
        <circle cx="75" cy="75" r={r} fill="none" stroke="var(--slate-soft)" strokeWidth="12" />
        <circle cx="75" cy="75" r={r} fill="none" stroke={color} strokeWidth="12" strokeLinecap="round"
          strokeDasharray={c} strokeDashoffset={c * (1 - value / 100)} transform="rotate(-90 75 75)" style={{ transition: 'stroke-dashoffset .6s' }} />
      </svg>
      <div className="val"><div><div style={{ fontSize: 30, fontWeight: 750, letterSpacing: -1 }} className="num">{value}</div><div className="faint" style={{ fontSize: 11.5 }}>risk-weighted</div></div></div>
    </div>
  )
}

export default function DataQuality() {
  const qc = useQueryClient()
  const toast = useToast()
  const { data, isLoading } = useQuery({ queryKey: ['dq'], queryFn: api.dataQuality })
  const run = useMutation({ mutationFn: api.runDq, onSuccess: (d) => { qc.setQueryData(['dq'], d); qc.invalidateQueries({ queryKey: ['dashboard'] }); toast('ok', `Data-quality run complete: ${d.overall_score}`) } })
  if (isLoading || !data) return <Skeleton h={400} />
  const failing = data.rules.filter((r) => !r.passing)

  return (
    <>
      <PageHeader title="Data quality"
        subtitle="Measurable, risk-based rules across completeness, validity, consistency, timeliness, integrity, traceability and accuracy. Every run is persisted so trends and threshold breaches are auditable."
        reqs={['CIP-PA-658']}
        actions={<button className="btn primary" onClick={() => run.mutate()} disabled={run.isPending}><Play size={15} />Run all rules</button>} />

      <div className="grid g-3" style={{ marginBottom: 16 }}>
        <Card title="Overall score" sub={`Run ${fmtDateTime(data.run_at)}`}>
          <div className="row" style={{ gap: 22 }}>
            <Ring value={data.overall_score} />
            <div className="stack" style={{ gap: 8, fontSize: 13 }}>
              <div className="row"><CheckCircle2 size={16} color="var(--green)" />{data.rules.length - failing.length} rules passing</div>
              <div className="row"><TriangleAlert size={16} color={failing.length ? 'var(--red)' : 'var(--text-3)'} />{failing.length} below threshold</div>
              <div className="faint" style={{ fontSize: 12 }}>HIGH ×3 · MEDIUM ×2 · LOW ×1</div>
            </div>
          </div>
        </Card>
        <Card title="By dimension">
          <div className="stack" style={{ gap: 10 }}>
            {data.dimensions.map((d) => (
              <div key={d.dimension}><div className="spread" style={{ fontSize: 12.5, marginBottom: 4 }}><span>{d.dimension}</span><b className="num">{d.score}%</b></div>
                <ProgressBar pct={d.score} tone={d.score >= 99 ? 'var(--green)' : d.score >= 95 ? 'var(--amber)' : 'var(--red)'} /></div>
            ))}
          </div>
        </Card>
        <Card title="Score trend" sub="Average rule score per run">
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={data.history} margin={{ top: 8, right: 8, left: -20, bottom: 0 }}>
              <CartesianGrid vertical={false} stroke={GRID} />
              <XAxis dataKey="run_at" tickFormatter={(v) => new Date(v).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} {...AXIS} tickLine={false} />
              <YAxis domain={[80, 100]} {...AXIS} tickLine={false} axisLine={false} />
              <Tooltip labelFormatter={(v) => fmtDateTime(String(v))} contentStyle={{ borderRadius: 10, fontSize: 12.5 }} />
              <Line type="monotone" dataKey="avg_score" name="Avg score" stroke={SERIES[0]} strokeWidth={2} dot={{ r: 4, strokeWidth: 2, stroke: '#fff' }} />
            </LineChart>
          </ResponsiveContainer>
        </Card>
      </div>

      <Card title="Rules" sub="SQL-defined; population = rows evaluated">
        <div className="table-wrap">
          <table className="t">
            <thead><tr><th>Rule</th><th>Dimension</th><th>Description</th><th>Risk</th><th>Population</th><th>Failed</th><th style={{ width: 170 }}>Score / threshold</th></tr></thead>
            <tbody>
              {data.rules.map((r) => (
                <tr key={r.code}>
                  <td className="mono">{r.code}</td><td>{r.dimension}</td><td>{r.description}</td><td><Pill value={r.risk} /></td>
                  <td className="num">{r.population}</td><td className="num">{r.failed ? <b style={{ color: 'var(--red)' }}>{r.failed}</b> : 0}</td>
                  <td><div className="spread" style={{ fontSize: 12, marginBottom: 4 }}><b className="num">{r.score}%</b><span className="faint">≥ {r.threshold}%</span></div><ProgressBar pct={r.score} tone={r.passing ? 'var(--green)' : 'var(--red)'} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  )
}
