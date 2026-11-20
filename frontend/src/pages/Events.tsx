import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CopyCheck, Layers, Repeat, Users } from 'lucide-react'
import { api } from '../api/client'
import { Card, Drawer, Kpi, PageHeader, useToast } from '../components/ui'
import type { BrokerMsg } from '../api/client'
import { fmtDateTime, timeAgo } from '../lib/format'

export default function Events() {
  const [topic, setTopic] = useState<string>('')
  const [msg, setMsg] = useState<BrokerMsg | null>(null)
  const toast = useToast()
  const qc = useQueryClient()
  const log = useQuery({ queryKey: ['event-log', topic], queryFn: () => api.eventLog(topic || undefined), refetchInterval: 5_000 })
  const cons = useQuery({ queryKey: ['consumers'], queryFn: api.consumers, refetchInterval: 5_000 })
  const replay = useMutation({
    mutationFn: (offset: number) => api.replay(offset),
    onSuccess: (r) => {
      toast('ok', `Re-delivered ${r.redelivered_event_id.slice(0, 8)}… → processed ${r.consumer_result.PROCESSED}, duplicates skipped ${r.consumer_result.DUPLICATE_SKIPPED}`)
      qc.invalidateQueries({ queryKey: ['event-log'] }); qc.invalidateQueries({ queryKey: ['consumers'] }); setMsg(null)
    },
  })
  const totalLag = cons.data?.groups.reduce((s, g) => s + g.lag, 0) ?? 0

  return (
    <>
      <PageHeader title="Event-driven architecture"
        subtitle="Kafka semantics — topics, offsets, consumer groups, manual commit — on a Postgres log broker (set CIP_KAFKA_BOOTSTRAP_SERVERS to use real Kafka). Consumers dedupe on (event_id, group) so at-least-once delivery yields exactly-once outcomes."
        reqs={['CIP-PA-065', 'CIP-PA-289']} />

      <div className="grid g-4" style={{ marginBottom: 16 }}>
        <Kpi label="Topics" value={log.data?.topics.length ?? 0} foot={`${log.data?.topics.reduce((s, t) => s + t.messages, 0) ?? 0} messages retained`} icon={<Layers size={16} />} />
        <Kpi label="Consumer groups" value={cons.data?.groups.length ?? 0} foot={`Total lag ${totalLag}`} icon={<Users size={16} />} tone="indigo" />
        <Kpi label="Processed (idempotent)" value={cons.data?.processed.OK ?? 0} foot={`${cons.data?.processed.FAILED ?? 0} parked as failed`} icon={<CopyCheck size={16} />} tone="green" />
        <Kpi label="Duplicates skipped" value={cons.data?.duplicates.length ?? 0} foot="Re-deliveries caught by processed_events" icon={<Repeat size={16} />} tone="amber" />
      </div>

      <Card title="Consumer groups" sub="Offset is committed manually, in the same DB transaction as the business effect" className="" bodyClass="">
        <div className="table-wrap">
          <table className="t">
            <thead><tr><th>Group</th><th>Topic</th><th>Log end</th><th>Committed</th><th>Lag</th><th style={{ width: '25%' }}>Progress</th></tr></thead>
            <tbody>
              {cons.data?.groups.map((g) => (
                <tr key={g.group}>
                  <td><b>{g.group}</b></td><td className="mono">{g.topic}</td>
                  <td className="num">{g.log_end_offset}</td><td className="num">{g.committed_offset}</td>
                  <td>{g.lag ? <span className="badge b-amber">{g.lag}</span> : <span className="badge b-green">0</span>}</td>
                  <td><div className="bar"><span style={{ width: `${g.messages ? (100 * (g.messages - g.lag)) / g.messages : 100}%`, background: 'var(--brand)' }} /></div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="grid g-main" style={{ marginTop: 16, alignItems: 'start' }}>
        <Card title="Topic log" sub="Click a message to inspect its PHI-free payload or replay it" action={
          <select className="select" style={{ width: 240 }} value={topic} onChange={(e) => setTopic(e.target.value)}>
            <option value="">All topics</option>
            {log.data?.topics.map((t) => <option key={t.topic} value={t.topic}>{t.topic} ({t.messages})</option>)}
          </select>
        }>
          <div className="table-wrap" style={{ maxHeight: 520, overflowY: 'auto' }}>
            <table className="t">
              <thead><tr><th>Offset</th><th>Event</th><th>Key (aggregate)</th><th>Published</th></tr></thead>
              <tbody>
                {log.data?.messages.map((m) => (
                  <tr key={m.offset} className="click" onClick={() => setMsg(m)}>
                    <td className="mono num">#{m.offset}</td>
                    <td><b>{m.event_type}</b><div className="faint mono" style={{ fontSize: 11 }}>{m.topic}</div></td>
                    <td className="mono faint">{m.key.slice(0, 13)}…</td>
                    <td className="faint" style={{ fontSize: 12.5 }}>{timeAgo(m.published_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
        <Card title="Duplicate deliveries" sub="Detected and skipped — no double side-effects">
          {!cons.data?.duplicates.length ? <div className="faint" style={{ fontSize: 13 }}>None yet. Open a message and press <b>Replay</b> to re-deliver it with the same event_id.</div> :
            cons.data.duplicates.map((d, i) => (
              <div key={i} style={{ padding: '8px 0', borderBottom: '1px solid var(--border)', fontSize: 12.5 }}>
                <div className="spread"><b>{d.consumer_group}</b><span className="faint">{timeAgo(d.detected_at)}</span></div>
                <div className="mono faint">{d.event_id}</div>
              </div>
            ))}
        </Card>
      </div>

      <Drawer open={!!msg} onClose={() => setMsg(null)} title={msg && <div><b>{msg.event_type}</b><div className="faint mono" style={{ marginTop: 4 }}>{msg.topic} · offset #{msg.offset}</div></div>}>
        {msg && (
          <div className="stack">
            <dl className="kv"><dt>event_id</dt><dd className="mono">{msg.event_id}</dd><dt>Published</dt><dd>{fmtDateTime(msg.published_at)}</dd></dl>
            <pre className="code">{JSON.stringify(msg.payload, null, 2)}</pre>
            <div className="alert info">Replay appends the same event_id to the topic again, simulating a broker re-delivery. Consumers will record it as a duplicate and skip the side effects.</div>
            <div><button className="btn primary" onClick={() => replay.mutate(msg.offset)} disabled={replay.isPending}><Repeat size={15} />Replay this event</button></div>
          </div>
        )}
      </Drawer>
    </>
  )
}
