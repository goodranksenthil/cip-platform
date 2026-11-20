import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { Play, Search } from 'lucide-react'
import { api } from '../api/client'
import { Card, Empty, Kpi, PageHeader, Pill, ProgressBar, Skeleton, useToast } from '../components/ui'
import TransactionDrawer from '../components/TransactionDrawer'
import { fmtDateTime, fmtHours, humanize, timeAgo } from '../lib/format'
import { Inbox, Send, Skull, Timer } from 'lucide-react'

const STATES = ['ALL', 'BREACHED', 'AT_RISK', 'ON_TRACK', 'PAUSED'] as const

export default function Pipeline() {
  const [params, setParams] = useSearchParams()
  const q = params.get('q') ?? ''
  const [filter, setFilter] = useState<(typeof STATES)[number]>('ALL')
  const [open, setOpen] = useState<string | null>(null)
  const qc = useQueryClient()
  const toast = useToast()

  const board = useQuery({ queryKey: ['sla-board'], queryFn: api.slaBoard, refetchInterval: 10_000 })
  const outbox = useQuery({ queryKey: ['outbox'], queryFn: api.outbox, refetchInterval: 5_000 })
  const search = useQuery({ queryKey: ['txn-search', q], queryFn: () => api.transactions({ q, size: 50 }), enabled: !!q })
  const run = useMutation({
    mutationFn: api.runPipeline,
    onSuccess: (r) => { toast('ok', `Relay published ${r.published}, consumers processed ${r.processed}`); qc.invalidateQueries() },
  })

  const items = useMemo(() => {
    const src = q ? search.data?.items ?? [] : board.data?.items ?? []
    return filter === 'ALL' ? src : src.filter((t) => t.sla.state === filter)
  }, [board.data, search.data, filter, q])

  const counts = outbox.data?.counts ?? {}
  return (
    <>
      <PageHeader title="WIP pipeline"
        subtitle="Master transaction records and their SLA clocks, plus the transactional outbox that feeds the event backbone."
        reqs={['CIP-PA-005', 'CIP-PA-064', 'CIP-PA-065']}
        actions={<button className="btn primary" onClick={() => run.mutate()} disabled={run.isPending}><Play size={15} />Run relay & consumers now</button>} />

      <div className="grid g-4" style={{ marginBottom: 16 }}>
        <Kpi label="Outbox pending" value={counts.PENDING ?? 0} foot="Claimed with FOR UPDATE SKIP LOCKED" icon={<Inbox size={16} />} tone="amber" />
        <Kpi label="Published" value={counts.PUBLISHED ?? 0} foot={`Broker: ${outbox.data?.broker ?? '…'}`} icon={<Send size={16} />} />
        <Kpi label="Dead-lettered" value={counts.DEAD ?? 0} foot="After 5 attempts with exponential backoff" icon={<Skull size={16} />} tone={counts.DEAD ? 'red' : 'green'} />
        <Kpi label="Avg publish latency" value={outbox.data?.avg_publish_latency_s != null ? `${outbox.data.avg_publish_latency_s}s` : '—'} foot="Commit → broker ack" icon={<Timer size={16} />} tone="indigo" />
      </div>

      <div className="grid g-main" style={{ alignItems: 'start' }}>
        <Card title="SLA board" sub={q ? `Search results for “${q}”` : 'Open transactions, most SLA consumed first'} action={
          <div className="row">
            {q && <button className="btn sm" onClick={() => setParams({})}><Search size={13} />Clear search</button>}
            <div className="toggle">{STATES.map((s) => <button key={s} className={filter === s ? 'on' : ''} onClick={() => setFilter(s)} style={{ padding: '5px 10px', fontSize: 12 }}>{humanize(s)}</button>)}</div>
          </div>
        }>
          {board.isLoading ? <Skeleton h={300} /> : !items.length ? <Empty>No transactions.</Empty> : (
            <div className="table-wrap">
              <table className="t">
                <thead><tr><th>DCN</th><th>Request</th><th>Status</th><th style={{ width: '28%' }}>SLA</th><th>Received</th></tr></thead>
                <tbody>
                  {items.map((t) => (
                    <tr key={t.id} className="click" onClick={() => setOpen(t.id)}>
                      <td><div className="mono" style={{ fontWeight: 600 }}>{t.dcn}</div><Pill value={t.priority} /></td>
                      <td>{humanize(t.request_type)}<div className="faint" style={{ fontSize: 12 }}>{humanize(t.service_category)}</div></td>
                      <td><Pill value={t.status} /></td>
                      <td>
                        <div className="spread" style={{ fontSize: 12, marginBottom: 4 }}><Pill value={t.sla.state} /><span className="num faint">{t.sla.remaining_hours >= 0 ? `${fmtHours(t.sla.remaining_hours)} left` : `${fmtHours(-t.sla.remaining_hours)} over`}</span></div>
                        <ProgressBar pct={t.sla.percent_used} tone={t.sla.state === 'PAUSED' ? '#8492a6' : undefined} />
                      </td>
                      <td className="faint" style={{ fontSize: 12.5 }}>{timeAgo(t.source_received_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <div className="stack">
          <Card title="SLA policies" sub="Versioned; captured on each transaction at START">
            <table className="t">
              <tbody>
                {board.data?.policies.map((p) => (
                  <tr key={p.code}><td><div style={{ fontWeight: 600, fontSize: 12.5 }}>{humanize(p.code.split(':')[0])}</div><div className="faint" style={{ fontSize: 11.5 }}>{p.code.split(':')[1]} · v{p.version}{p.pausable ? '' : ' · no pause'}</div></td><td className="num" style={{ textAlign: 'right', fontWeight: 600 }}>{fmtHours(p.target_hours)}</td></tr>
                ))}
              </tbody>
            </table>
          </Card>
          <Card title="Outbox stream" sub="Latest rows written with business transactions">
            <div style={{ maxHeight: 420, overflowY: 'auto' }}>
              {outbox.data?.events.slice(0, 25).map((e) => (
                <div key={e.id} className="spread" style={{ padding: '8px 0', borderBottom: '1px solid var(--border)' }}>
                  <div style={{ minWidth: 0 }}><div style={{ fontWeight: 600, fontSize: 12.5 }}>{e.event_type}</div><div className="faint mono" style={{ fontSize: 11 }}>{e.topic}</div></div>
                  <div style={{ textAlign: 'right' }}><Pill value={e.status} /><div className="faint" style={{ fontSize: 11 }}>{fmtDateTime(e.created_at)}</div></div>
                </div>
              ))}
            </div>
          </Card>
        </div>
      </div>
      <TransactionDrawer id={open} onClose={() => setOpen(null)} />
    </>
  )
}
