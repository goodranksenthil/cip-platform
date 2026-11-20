import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Bar, BarChart, CartesianGrid, Cell, LabelList, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Activity, AlarmClock, FileCheck2, Fingerprint, Gauge, Inbox, ShieldAlert, Upload } from 'lucide-react'
import { api } from '../api/client'
import { Card, Kpi, PageHeader, Pill, ProgressBar, Skeleton } from '../components/ui'
import TransactionDrawer from '../components/TransactionDrawer'
import { fmtDate, fmtHours, humanize, timeAgo } from '../lib/format'
import { AXIS, GRID, SERIES, STATUS_COLOR } from '../lib/palette'

const tooltipStyle = { borderRadius: 10, border: '1px solid #e3e8ef', boxShadow: '0 8px 24px rgba(15,27,45,.12)', fontSize: 12.5 }

export default function Dashboard() {
  const { data, isLoading } = useQuery({ queryKey: ['dashboard'], queryFn: api.dashboard, refetchInterval: 10_000 })
  const [open, setOpen] = useState<string | null>(null)

  if (isLoading || !data) return <div className="stack"><Skeleton h={60} /><div className="grid g-4">{[1, 2, 3, 4].map((i) => <Skeleton key={i} />)}</div><Skeleton h={320} /></div>
  const k = data.kpis

  return (
    <>
      <PageHeader title="Operations dashboard"
        subtitle="Live view of intake volume, pipeline state, SLA exposure and integrity posture. Auto-refreshes every 10 seconds."
        actions={<Link className="btn primary" to="/ingestion"><Upload size={15} />New intake</Link>} />

      <div className="grid g-4" style={{ marginBottom: 16 }}>
        <Kpi label="Transactions" value={k.transactions} foot={`${k.open} open · ${k.documents} source documents`} icon={<Inbox size={16} />} />
        <Kpi label="Integrity pass rate" value={`${k.integrity_pass_rate}%`} foot={`${k.integrity_checks} SHA-256 verifications · ${k.quarantined} quarantined`} icon={<Fingerprint size={16} />} tone={k.quarantined ? 'red' : 'green'} />
        <Kpi label="SLA exposure" value={k.sla_breached + k.sla_at_risk} foot={`${k.sla_breached} breached · ${k.sla_at_risk} at risk · ${k.sla_met_rate}% met`} icon={<AlarmClock size={16} />} tone="amber" />
        <Kpi label="Data quality score" value={k.dq_score} foot={`${k.events_processed} events processed · backlog ${k.outbox_backlog}`} icon={<Gauge size={16} />} tone="indigo" />
      </div>

      <div className="grid g-main" style={{ marginBottom: 16 }}>
        <Card title="Daily intake" sub="Transactions received per day, last 14 days, by SLA priority">
          <ResponsiveContainer width="100%" height={270}>
            <BarChart data={data.daily_intake} margin={{ top: 8, right: 8, left: -18, bottom: 0 }} barCategoryGap="22%">
              <CartesianGrid vertical={false} stroke={GRID} />
              <XAxis dataKey="date" tickFormatter={fmtDate} {...AXIS} tickLine={false} />
              <YAxis allowDecimals={false} {...AXIS} tickLine={false} axisLine={false} />
              <Tooltip contentStyle={tooltipStyle} labelFormatter={(l) => fmtDate(String(l))} cursor={{ fill: 'rgba(42,120,214,.06)' }} />
              <Legend iconType="square" iconSize={10} wrapperStyle={{ fontSize: 12.5, color: '#4a5a70' }} />
              <Bar dataKey="standard" name="Standard" stackId="a" fill={SERIES[0]} stroke="#fff" strokeWidth={2} />
              <Bar dataKey="expedited" name="Expedited" stackId="a" fill={SERIES[1]} stroke="#fff" strokeWidth={2} radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </Card>
        <Card title="SLA state" sub="All transactions, derived from the SLA ledger">
          <ResponsiveContainer width="100%" height={270}>
            <BarChart data={data.sla_breakdown} layout="vertical" margin={{ top: 4, right: 36, left: 4, bottom: 0 }}>
              <XAxis type="number" hide allowDecimals={false} />
              <YAxis type="category" dataKey="state" width={86} tickFormatter={humanize} {...AXIS} tickLine={false} axisLine={false} />
              <Tooltip contentStyle={tooltipStyle} cursor={{ fill: 'rgba(0,0,0,.03)' }} />
              <Bar dataKey="count" name="Transactions" radius={[0, 4, 4, 0]} barSize={18}>
                {data.sla_breakdown.map((s) => <Cell key={s.state} fill={STATUS_COLOR[s.state] ?? '#8492a6'} />)}
                <LabelList dataKey="count" position="right" style={{ fill: '#4a5a70', fontSize: 12 }} />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Card>
      </div>

      <div className="grid g-3" style={{ marginBottom: 16 }}>
        <Card title="Pipeline status" sub="Where each transaction is now">
          <div className="stack" style={{ gap: 12 }}>
            {data.status_breakdown.map((s) => (
              <div key={s.status}>
                <div className="spread" style={{ fontSize: 13, marginBottom: 5 }}><span>{humanize(s.status)}</span><b className="num">{s.count}</b></div>
                <ProgressBar pct={(100 * s.count) / Math.max(1, k.transactions)} tone={STATUS_COLOR[s.status]} />
              </div>
            ))}
          </div>
        </Card>
        <Card title="Document classes" sub="Classifier output on source artifacts">
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={data.doc_classes} layout="vertical" margin={{ top: 0, right: 30, left: 4, bottom: 0 }}>
              <XAxis type="number" hide />
              <YAxis type="category" dataKey="doc_class" width={118} tickFormatter={humanize} {...AXIS} tickLine={false} axisLine={false} />
              <Tooltip contentStyle={tooltipStyle} cursor={{ fill: 'rgba(0,0,0,.03)' }} />
              <Bar dataKey="count" name="Documents" fill={SERIES[0]} radius={[0, 4, 4, 0]} barSize={16}>
                <LabelList dataKey="count" position="right" style={{ fill: '#4a5a70', fontSize: 12 }} />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Card>
        <Card title="Data quality by dimension" sub="Latest run, % of population passing" action={<Link className="btn sm" to="/data-quality">Details</Link>}>
          <div className="stack" style={{ gap: 11 }}>
            {data.dq_dimensions.map((d) => (
              <div key={d.dimension}>
                <div className="spread" style={{ fontSize: 13, marginBottom: 5 }}><span>{d.dimension}</span><b className="num">{d.score}%</b></div>
                <ProgressBar pct={d.score} tone={d.score >= 99 ? 'var(--green)' : d.score >= 95 ? 'var(--amber)' : 'var(--red)'} />
              </div>
            ))}
          </div>
        </Card>
      </div>

      <div className="grid g-2">
        <Card title={<span className="row"><ShieldAlert size={16} color="var(--red)" />Needs attention</span>} sub="Highest SLA consumption first">
          {data.at_risk.length === 0 ? <div className="empty">Nothing at risk 🎉</div> : (
            <table className="t">
              <tbody>
                {data.at_risk.map((t) => (
                  <tr key={t.id} className="click" onClick={() => setOpen(t.id)}>
                    <td><div className="mono" style={{ fontWeight: 600 }}>{t.dcn}</div><div className="faint" style={{ fontSize: 12 }}>{humanize(t.request_type)}</div></td>
                    <td style={{ width: '38%' }}><ProgressBar pct={t.sla.percent_used} /><div className="faint num" style={{ fontSize: 11.5, marginTop: 4 }}>{t.sla.percent_used}% of {fmtHours(t.sla.target_hours)}</div></td>
                    <td style={{ textAlign: 'right' }}><Pill value={t.sla.state} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
        <Card title={<span className="row"><Activity size={16} color="var(--brand)" />Recent intake</span>} sub="Click a row for documents, lineage & integrity history">
          <table className="t">
            <tbody>
              {data.recent.map((t) => (
                <tr key={t.id} className="click" onClick={() => setOpen(t.id)}>
                  <td><div className="mono" style={{ fontWeight: 600 }}>{t.dcn}</div><div className="faint" style={{ fontSize: 12 }}>{humanize(t.service_category)}</div></td>
                  <td><span className="row faint" style={{ fontSize: 12.5 }}><FileCheck2 size={14} />{t.document_count}</span></td>
                  <td><Pill value={t.status} /></td>
                  <td className="faint" style={{ textAlign: 'right', fontSize: 12.5 }}>{timeAgo(t.source_received_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
      <TransactionDrawer id={open} onClose={() => setOpen(null)} />
    </>
  )
}
