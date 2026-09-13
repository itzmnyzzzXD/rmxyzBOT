import { useEffect, useMemo, useState } from 'react'
import {
  Activity, AlertTriangle, ArrowRight, BarChart3, Bell, Bot, Check, ChevronDown,
  Command, FileText, Gauge, Hammer, KeyRound, LayoutDashboard, Lock, Menu,
  Radar, Search, Server, Settings, Shield, ShieldAlert, ShieldCheck, Sparkles,
  Ticket, Users, X, Zap
} from 'lucide-react'

type ServerItem = { id: string; name: string; members: number; icon: string; online: boolean }
type Toast = { message: string; kind: 'success' | 'info' | 'error' }

const servers: ServerItem[] = [
  { id: '1', name: 'RM Community', members: 12480, icon: 'RM', online: true },
  { id: '2', name: 'Night Shift', members: 5810, icon: 'NS', online: true },
  { id: '3', name: 'Gaming Hub', members: 2220, icon: 'GH', online: false },
]

const modules = [
  ['Overview', LayoutDashboard, '/dashboard'],
  ['Moderation', Hammer, '/dashboard/moderation'],
  ['AutoMod', ShieldCheck, '/dashboard/automod'],
  ['Anti-Raid', Radar, '/dashboard/anti-raid'],
  ['Anti-Nuke', ShieldAlert, '/dashboard/anti-nuke'],
  ['Security Center', Shield, '/dashboard/security'],
  ['Warnings', AlertTriangle, '/dashboard/warnings'],
  ['Logs', FileText, '/dashboard/logs'],
  ['Tickets', Ticket, '/dashboard/tickets'],
  ['Welcome', Sparkles, '/dashboard/welcome'],
  ['Reaction Roles', Users, '/dashboard/reaction-roles'],
  ['Custom Commands', Command, '/dashboard/custom-commands'],
  ['Economy', BarChart3, '/dashboard/economy'],
  ['Levels', Gauge, '/dashboard/levels'],
  ['Permissions', KeyRound, '/dashboard/permissions'],
  ['Bot Settings', Settings, '/dashboard/bot-settings'],
] as const

const commandData = [
  ['warn', 'Warn a member', 'Moderate Members'],
  ['timeout', 'Timeout a member', 'Moderate Members'],
  ['mute', 'Mute a member', 'Moderate Members'],
  ['kick', 'Kick a member', 'Kick Members'],
  ['ban', 'Ban a member', 'Ban Members'],
  ['purge', 'Bulk delete messages', 'Manage Messages'],
  ['lock', 'Lock a channel', 'Manage Channels'],
  ['unlock', 'Unlock a channel', 'Manage Channels'],
  ['slowmode', 'Configure slowmode', 'Manage Channels'],
  ['automod', 'Configure message protection', 'Manage Guild'],
  ['antinuke', 'Open anti-nuke controls', 'Administrator'],
  ['antiraid', 'Manage raid protection', 'Manage Guild'],
] as const

function cn(...items: Array<string | false | undefined>) { return items.filter(Boolean).join(' ') }

function go(path: string) {
  window.history.pushState({}, '', path)
  window.dispatchEvent(new PopStateEvent('popstate'))
}

export default function AppFixed() {
  const [path, setPath] = useState(window.location.pathname)
  const [selected, setSelected] = useState(servers[0])
  const [serverOpen, setServerOpen] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [collapsed, setCollapsed] = useState(false)
  const [toast, setToast] = useState<Toast | null>(null)

  useEffect(() => {
    const onPop = () => setPath(window.location.pathname)
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(() => setToast(null), 2800)
    return () => window.clearTimeout(timer)
  }, [toast])

  if (!path.startsWith('/dashboard')) return <PublicHome />

  const title = path === '/dashboard' ? 'Overview' : modules.find((m) => m[2] === path)?.[0] ?? 'Commands'
  const Icon = modules.find((m) => m[2] === path)?.[1] ?? Command

  return (
    <div className="app-shell">
      <aside className={cn('sidebar', collapsed && 'collapsed', sidebarOpen && 'mobile-open')}>
        <div className="sidebar-brand">
          <button className="brand" onClick={() => go('/')}><span className="brand-mark">RM</span>{!collapsed && <span>RM</span>}</button>
          {!collapsed && <button className="collapse-button" onClick={() => setCollapsed((v) => !v)}><ChevronDown size={17} /></button>}
        </div>
        <div className="sidebar-scroll">
          <div className="side-caption">CONTROL</div>
          {modules.map(([label, ItemIcon, route]) => (
            <button key={route} className={cn('side-link', path === route && 'active')} onClick={() => { go(route); setSidebarOpen(false) }}>
              <ItemIcon size={18} />{!collapsed && <span>{label}</span>}
              {!collapsed && label === 'Security Center' && <span className="side-badge">92</span>}
            </button>
          ))}
          <div className="side-caption">WORKSPACE</div>
          <button className="side-link" onClick={() => { go('/commands'); setSidebarOpen(false) }}><Command size={18}/>{!collapsed && <span>Command Directory</span>}</button>
          <button className="side-link" onClick={() => { go('/docs'); setSidebarOpen(false) }}><FileText size={18}/>{!collapsed && <span>Documentation</span>}</button>
        </div>
        <div className="sidebar-footer"><button className="profile-chip"><span className="avatar">A</span>{!collapsed && <span><b>Admin</b><small>Owner</small></span>}</button></div>
      </aside>

      {sidebarOpen && <div className="mobile-overlay show" onClick={() => setSidebarOpen(false)} />}
      <div className={cn('dashboard-main', collapsed && 'sidebar-collapsed')}>
        <header className="topbar">
          <button className="mobile-menu" onClick={() => setSidebarOpen(true)}><Menu size={22}/></button>
          <button className="server-selector" onClick={() => setServerOpen((v) => !v)}>
            <span className="server-avatar">{selected.icon}</span><span className="server-name"><small>SERVER</small><b>{selected.name}</b></span><ChevronDown size={16}/>
          </button>
          {serverOpen && <div className="server-menu">{servers.map((s) => <button key={s.id} onClick={() => { setSelected(s); setServerOpen(false); setToast({ kind: 'success', message: `Switched to ${s.name}` }) }}><span className="server-avatar">{s.icon}</span><span><b>{s.name}</b><small>{s.members.toLocaleString()} members</small></span><span className={cn('status-dot', s.online ? 'online' : 'offline')}/></button>)}</div>}
          <div className="topbar-right"><button className="search-trigger" onClick={() => setToast({ kind: 'info', message: 'Command palette is available with Ctrl/Cmd + K' })}><Search size={17}/><span>Search RM...</span><kbd>⌘ K</kbd></button><button className="icon-button"><Bell size={18}/></button><button className="icon-button" onClick={() => go('/dashboard/bot-settings')}><Settings size={18}/></button><div className="top-avatar">A</div></div>
        </header>

        <main className="page-wrap">
          <div className="page">
            <div className="page-title-row">
              <div><span className="eyebrow">RM CONTROL CENTER</span><h1>{title}</h1><p>{selected.name} · {selected.members.toLocaleString()} members</p></div>
              <div className="page-actions"><button className="secondary-button" onClick={() => setToast({ kind: 'info', message: 'Refreshing live data...' })}><Activity size={16}/>Refresh</button><button className="primary-button" onClick={() => setToast({ kind: 'success', message: `${title} settings saved` })}><Check size={16}/>Save changes</button></div>
            </div>
            {path === '/dashboard' ? <Overview selected={selected} onToast={setToast} /> : path === '/dashboard/commands' || path === '/commands' ? <Commands onToast={setToast} /> : <Module title={title} Icon={Icon} onToast={setToast} />}
          </div>
        </main>
      </div>
      {toast && <div className={cn('toast', toast.kind)}><Check size={17}/><span>{toast.message}</span><button onClick={() => setToast(null)}><X size={14}/></button></div>}
    </div>
  )
}

function Overview({ selected, onToast }: { selected: ServerItem; onToast: (t: Toast) => void }) {
  const metrics = [['Members', selected.members.toLocaleString(), '+4.8%', Users], ['Moderation actions', '1,284', '+12%', Hammer], ['Warnings', '86', '-3.2%', AlertTriangle], ['Security events', '19', '-18%', ShieldAlert], ['Tickets', '14', '+2', Ticket], ['Uptime', '99.98%', 'stable', Gauge]] as const
  return <>
    <div className="metric-grid">{metrics.map(([label, value, change, ItemIcon]) => <div className="metric-card" key={label}><div className="metric-top"><span>{label}</span><span className="metric-icon"><ItemIcon size={17}/></span></div><strong>{value}</strong><small>{change} <span>vs last period</span></small></div>)}</div>
    <div className="overview-grid">
      <div className="panel activity-panel"><div className="panel-head"><div><span className="panel-kicker">ACTIVITY</span><h3>Recent events</h3></div></div>{['Anti-nuke shield checked', 'Timeout action issued', 'Raid threshold reviewed', 'Webhook creation blocked'].map((x, i) => <div className="activity-row" key={x}><span className={cn('activity-icon', i === 3 ? 'red' : 'green')}><Activity size={15}/></span><div><b>{x}</b><span>{selected.name} · security pipeline</span></div><time>{i * 7 + 2}m ago</time></div>)}</div>
      <div className="panel security-panel"><div className="panel-head"><div><span className="panel-kicker">SECURITY</span><h3>System posture</h3></div><span className="status-chip safe"><ShieldCheck size={13}/>SAFE</span></div><div className="security-score"><div className="ring"><span>92</span><small>/100</small></div><div><b>Excellent protection</b><span>Core security layers are online and policy checks are passing.</span></div></div><div className="check-list">{['Anti-nuke shield','Anti-raid detection','AutoMod rules','Logging pipeline'].map((x) => <div key={x}><Check size={15}/><span>{x}</span><small>Enabled</small></div>)}</div></div>
    </div>
    <div className="panel quick-panel"><div className="panel-head"><div><span className="panel-kicker">ACTIONS</span><h3>Quick moderation</h3></div></div><div className="quick-grid">{[['Warn user',AlertTriangle],['Kick member',Hammer],['Ban member',ShieldAlert],['Timeout',Lock],['Clear messages',FileText],['Lock channel',Lock]].map(([label, ItemIcon]) => <button key={String(label)} onClick={() => onToast({ kind: 'success', message: `${String(label)} flow opened` })}><span><ItemIcon size={17}/></span>{String(label)}</button>)}</div></div>
  </>
}

function Module({ title, Icon, onToast }: { title: string; Icon: React.ComponentType<{ size?: number }>; onToast: (t: Toast) => void }) {
  const [enabled, setEnabled] = useState(true)
  const [query, setQuery] = useState('')
  const settings = ['Protection mode', 'Thresholds', 'Exceptions', 'Staff permissions', 'Logging', 'Notifications']
  return <div className="module-layout"><div className="panel"><div className="panel-head"><div><span className="panel-kicker">CONFIGURATION</span><h3>Core controls</h3></div><Icon size={18}/></div><div className="setting-grid">{settings.map((label, i) => <div className="setting-card" key={label}><div><b>{label}</b><span>{i === 0 ? 'Primary module protection.' : 'Configured for this server.'}</span></div><button className={cn('mini-toggle', (enabled || i !== 0) && 'on')} onClick={() => i === 0 && setEnabled((v) => !v)}><span/></button></div>)}</div></div><div className="panel side-config"><div className="panel-head"><div><span className="panel-kicker">LIVE PREVIEW</span><h3>Policy status</h3></div><span className="status-chip safe"><CheckShield size={13}/>ACTIVE</span></div><div className="preview-config"><span className="icon-box"><Icon size={20}/></span><b>{title} is ready</b><p>Changes are staged locally and can sync through the secure API layer.</p></div><div className="detail-list"><div><span>Mode</span><b>{enabled ? 'Enforced' : 'Paused'}</b></div><div><span>Last sync</span><b>just now</b></div></div></div><div className="panel table-panel"><div className="panel-head"><div><span className="panel-kicker">MANAGED ITEMS</span><h3>{title} rules</h3></div><div className="inline-search"><Search size={15}/><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Filter rules..."/></div></div><div className="data-list">{settings.filter((x) => x.toLowerCase().includes(query.toLowerCase())).map((x) => <div className="data-row" key={x}><div className="row-main"><span className="icon-box tiny"><Icon size={15}/></span><div><b>{x}</b><span>{enabled ? 'Configured and monitoring' : 'Paused by administrator'}</span></div></div><span className={cn('status-chip', enabled ? 'safe' : 'muted')}>{enabled ? 'ACTIVE' : 'OFF'}</span></div>)}</div></div></div>
}

function CheckShield({ size }: { size?: number }) { return <ShieldCheck size={size} /> }

function Commands({ onToast }: { onToast: (t: Toast) => void }) {
  const [query, setQuery] = useState('')
  const filtered = useMemo(() => commandData.filter(([name, description, permission]) => `${name} ${description} ${permission}`.toLowerCase().includes(query.toLowerCase())), [query])
  return <div><div className="search-box large-search"><Search size={17}/><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search commands..."/></div><div className="command-grid">{filtered.map(([name, description, permission]) => <article className="command-card" key={name}><div className="command-head"><span className="command-icon"><Command size={17}/></span><div><b>rm!{name}</b><span>Moderation</span></div></div><p>{description}</p><div className="command-meta"><span><KeyRound size={13}/>{permission}</span></div><div className="usage-line">rm!{name} @user reason</div><button className="secondary-button" onClick={() => onToast({ kind: 'success', message: `rm!${name} copied` })}>Use command <ArrowRight size={14}/></button></article>)}</div></div>
}

function PublicHome() {
  return <div className="public-shell"><header className="public-nav container"><button className="brand" onClick={() => go('/')}><span className="brand-mark">RM</span><span>RM</span></button><nav className="public-links"><button className="active" onClick={() => go('/')}>Home</button><button onClick={() => go('/commands')}>Commands</button><button onClick={() => go('/security')}>Security</button><button onClick={() => go('/docs')}>Docs</button></nav><div className="public-actions"><button className="ghost-button"><span className="status-dot online"/>Status</button><button className="primary-button" onClick={() => go('/dashboard')}><LayoutDashboard size={16}/>Dashboard</button></div></header><section className="hero container"><div className="hero-copy"><span className="eyebrow"><span className="status-dot online"/> Discord security, rebuilt</span><h1>Keep your server<br/><span className="gradient-text">under control.</span></h1><p>RM is a modern Discord moderation and security system for communities that want serious protection without the clutter.</p><div className="hero-actions"><button className="primary-button large" onClick={() => go('/dashboard')}><Bot size={18}/>Open RM Dashboard<ArrowRight size={17}/></button><button className="secondary-button large" onClick={() => go('/security')}>Security Center</button></div></div><div className="hero-preview"><div className="preview-window"><div className="preview-top"><div className="preview-brand"><span className="brand-mark mini">RM</span>RM Control Center</div><span className="live-pill"><span className="status-dot online"/>LIVE</span></div><div className="preview-body"><div className="preview-sidebar"><span className="active">Overview</span><span>Moderation</span><span>Security</span><span>Logs</span></div><div className="preview-content"><div className="preview-heading"><div><span>SERVER</span><b>RM Community</b></div><span className="server-chip"><Server size={13}/>Connected</span></div><div className="preview-stats"><div><span>Members</span><b>12.4k</b></div><div><span>Actions</span><b>1,284</b></div><div><span>Warnings</span><b>86</b></div></div><div className="preview-security"><div className="score-ring"><b>92</b><span>SECURE</span></div><div><b>Security posture</b><span>Anti-nuke, raid protection and AutoMod are active.</span></div></div></div></div></div></div></section><section className="section container"><div className="section-intro"><span className="eyebrow">WHY RM</span><h2>Protection that feels like software.</h2><p>Moderation, automation and security controls in one fast control center.</p></div><div className="feature-grid">{[['Advanced moderation',Hammer],['Anti-nuke',ShieldAlert],['Anti-raid',Radar],['AutoMod',ShieldCheck],['Logging',FileText],['Tickets',Ticket],['Server tools',Settings],['Fast controls',Zap]].map(([label, ItemIcon]) => <div className="feature-card" key={String(label)}><span className="icon-box"><ItemIcon size={19}/></span><div><b>{String(label)}</b><span>Configured in a few taps.</span></div></div>)}</div></section><footer className="public-footer container"><span>RM Control Center</span><span>Built for serious communities.</span><span>v3.0</span></footer></div>
}
