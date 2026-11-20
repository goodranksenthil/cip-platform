import { createContext, useCallback, useContext, useState, type ReactNode } from 'react'
import { Check, Copy, X, CircleAlert, CircleCheck } from 'lucide-react'
import clsx from 'clsx'
import { shortHash } from '../lib/format'

export function PageHeader({ title, subtitle, reqs, actions }: { title: string; subtitle?: ReactNode; reqs?: string[]; actions?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {subtitle && <p>{subtitle}</p>}
        {reqs && <div className="req-tags">{reqs.map((r) => <span key={r} className="req-tag">{r}</span>)}</div>}
      </div>
      {actions && <div className="row">{actions}</div>}
    </div>
  )
}

export function Card({ title, sub, action, children, className, bodyClass }: {
  title?: ReactNode; sub?: ReactNode; action?: ReactNode; children: ReactNode; className?: string; bodyClass?: string
}) {
  return (
    <section className={clsx('card', className)}>
      {(title || action) && (
        <div className="card-head">
          <div><h3>{title}</h3>{sub && <div className="sub">{sub}</div>}</div>
          {action}
        </div>
      )}
      <div className={clsx('card-body', bodyClass)}>{children}</div>
    </section>
  )
}

const TONES = {
  brand: ['var(--brand)', 'var(--brand-soft)'], indigo: ['var(--indigo)', 'var(--indigo-soft)'],
  amber: ['var(--amber)', 'var(--amber-soft)'], red: ['var(--red)', 'var(--red-soft)'], green: ['var(--green)', 'var(--green-soft)'],
} as const
export type Tone = keyof typeof TONES

export function Kpi({ label, value, foot, icon, tone = 'brand' }: { label: string; value: ReactNode; foot?: ReactNode; icon: ReactNode; tone?: Tone }) {
  const [fg, bg] = TONES[tone]
  return (
    <div className="card kpi" style={{ ['--accent-soft' as string]: bg }}>
      <div className="kpi-label"><span className="kpi-icon" style={{ background: bg, color: fg }}>{icon}</span>{label}</div>
      <div className="kpi-value">{value}</div>
      {foot && <div className="kpi-foot">{foot}</div>}
    </div>
  )
}

const STATUS_TONE: Record<string, string> = {
  RECEIVED: 'b-slate', CLASSIFIED: 'b-indigo', EXTRACTED: 'b-indigo', IN_REVIEW: 'b-brand', COMPLETED: 'b-green', QUARANTINED: 'b-red',
  ON_TRACK: 'b-green', AT_RISK: 'b-amber', BREACHED: 'b-red', PAUSED: 'b-slate', MET: 'b-green', MISSED: 'b-red',
  VERIFIED: 'b-green', FAILED: 'b-red', UNKNOWN: 'b-slate', PENDING: 'b-amber', PUBLISHED: 'b-green', DEAD: 'b-red',
  EXPEDITED: 'b-amber', STANDARD: 'b-slate', HIGH: 'b-red', MEDIUM: 'b-amber', LOW: 'b-green',
}

export function Pill({ value, className }: { value: string; className?: string }) {
  return <span className={clsx('badge', STATUS_TONE[value] ?? 'b-slate', className)}>{value.replace(/_/g, ' ')}</span>
}

export function Hash({ value, full = false }: { value?: string | null; full?: boolean }) {
  const [copied, setCopied] = useState(false)
  if (!value) return <span className="faint">—</span>
  return (
    <span className="hash" title={value}>
      {full ? value : shortHash(value)}
      <button className="btn ghost sm" style={{ padding: 0 }} aria-label="Copy hash"
        onClick={(e) => { e.stopPropagation(); navigator.clipboard?.writeText(value); setCopied(true); setTimeout(() => setCopied(false), 1200) }}>
        {copied ? <Check size={12} /> : <Copy size={12} />}
      </button>
    </span>
  )
}

export function ProgressBar({ pct, tone }: { pct: number; tone?: string }) {
  const color = tone ?? (pct >= 100 ? 'var(--red)' : pct >= 75 ? 'var(--amber)' : 'var(--brand)')
  return <div className="bar"><span style={{ width: `${Math.min(100, Math.max(2, pct))}%`, background: color }} /></div>
}

export function Drawer({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode }) {
  if (!open) return null
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true">
        <div className="drawer-head spread">
          <div style={{ minWidth: 0 }}>{title}</div>
          <button className="icon-btn" onClick={onClose} aria-label="Close"><X size={16} /></button>
        </div>
        <div className="drawer-body">{children}</div>
      </aside>
    </>
  )
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { id: T; label: ReactNode }[]; value: T; onChange: (t: T) => void }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => <button key={t.id} role="tab" className={clsx('tab', value === t.id && 'on')} onClick={() => onChange(t.id)}>{t.label}</button>)}
    </div>
  )
}

export const Spinner = () => <span className="spinner" aria-label="Loading" />
export const Empty = ({ children }: { children: ReactNode }) => <div className="empty">{children}</div>
export const Skeleton = ({ h = 120 }: { h?: number }) => <div className="skeleton" style={{ height: h }} />

// ---------------------------------------------------------------- toasts
type Toast = { id: number; kind: 'ok' | 'err'; text: string }
const ToastCtx = createContext<(kind: Toast['kind'], text: string) => void>(() => {})
export const useToast = () => useContext(ToastCtx)

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const push = useCallback((kind: Toast['kind'], text: string) => {
    const id = Date.now() + Math.random()
    setToasts((t) => [...t, { id, kind, text }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4500)
  }, [])
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toast-stack">
        {toasts.map((t) => (
          <div key={t.id} className="toast">
            {t.kind === 'ok' ? <CircleCheck size={18} color="#3ddc97" /> : <CircleAlert size={18} color="#ff8a95" />}
            <span>{t.text}</span>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  )
}
