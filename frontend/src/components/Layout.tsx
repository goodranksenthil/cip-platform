import { useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  LayoutDashboard, FileUp, FileScan, ScanSearch, Workflow, ShieldCheck, Radio, BrainCircuit, Gauge, ListChecks,
  PanelLeftClose, PanelLeftOpen, Search, Bell,
} from 'lucide-react'
import clsx from 'clsx'
import { api } from '../api/client'

export const NAV = [
  { group: 'Overview', items: [{ to: '/', label: 'Dashboard', icon: LayoutDashboard }] },
  {
    group: 'Clinical pipeline', items: [
      { to: '/upload', label: 'Upload Documents', icon: FileScan },
      { to: '/ingestion', label: 'Document Ingestion', icon: FileUp },
      { to: '/documents', label: 'Classify & Extract', icon: ScanSearch },
      { to: '/pipeline', label: 'WIP Pipeline', icon: Workflow, badge: 'sla' as const },
    ],
  },
  {
    group: 'Platform controls', items: [
      { to: '/security', label: 'Data Security', icon: ShieldCheck },
      { to: '/events', label: 'Event Driven Architecture', icon: Radio },
      { to: '/ai-security', label: 'Application Security', icon: BrainCircuit },
      { to: '/data-quality', label: 'Data Quality', icon: Gauge },
    ],
  },
  { group: 'Governance', items: [{ to: '/requirements', label: 'Requirements', icon: ListChecks }] },
]

const ALL = NAV.flatMap((g) => g.items.map((i) => ({ ...i, group: g.group })))

export default function Layout() {
  const [collapsed, setCollapsed] = useState(false)
  const [q, setQ] = useState('')
  const loc = useLocation()
  const nav = useNavigate()
  const current = ALL.find((i) => (i.to === '/' ? loc.pathname === '/' : loc.pathname.startsWith(i.to)))

  const health = useQuery({ queryKey: ['health'], queryFn: api.health, refetchInterval: 15_000, retry: false })
  const dash = useQuery({ queryKey: ['dashboard'], queryFn: api.dashboard, refetchInterval: 15_000 })
  const slaAlerts = (dash.data?.kpis.sla_breached ?? 0) + (dash.data?.kpis.sla_at_risk ?? 0)

  return (
    <div className={clsx('shell', collapsed && 'collapsed')}>
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">C</div>
          <div className="brand-text">
            <div className="brand-name">CIP Console</div>
            <div className="brand-sub">Clinical Intake Platform</div>
          </div>
        </div>
        <nav>
          {NAV.map((g) => (
            <div key={g.group}>
              <div className="nav-label">{g.group}</div>
              {g.items.map((item) => (
                <NavLink key={item.to} to={item.to} end={item.to === '/'} className={({ isActive }) => clsx('nav-item', isActive && 'active')} title={item.label}>
                  <item.icon size={18} />
                  <span className="nav-text">{item.label}</span>
                  {'badge' in item && slaAlerts > 0 && <span className="nav-badge hot">{slaAlerts}</span>}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="health">
            <span className={clsx('dot', health.isError && 'down')} />
            <span className="nav-text">{health.isError ? 'API unreachable' : `API online · ${health.data?.broker ?? '…'}`}</span>
          </div>
          <button className="nav-item" style={{ width: '100%', border: 0, background: 'transparent' }} onClick={() => setCollapsed((c) => !c)}>
            {collapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}
            <span className="nav-text">Collapse menu</span>
          </button>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <div className="crumbs">{current?.group} / <b>{current?.label ?? 'Transaction'}</b></div>
          <form className="search" onSubmit={(e) => { e.preventDefault(); if (q.trim()) nav(`/pipeline?q=${encodeURIComponent(q.trim())}`) }}>
            <Search size={15} />
            <input placeholder="Search DCN or NPI…" value={q} onChange={(e) => setQ(e.target.value)} />
          </form>
          <button className="icon-btn" aria-label="Notifications" onClick={() => nav('/pipeline')}><Bell size={16} /></button>
          <div className="avatar" title="Project Manager">PM</div>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
