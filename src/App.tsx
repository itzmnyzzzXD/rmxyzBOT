import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Activity as ActivityIcon, AlertTriangle, ArrowRight, Ban, BarChart3, Bell, Bot,
  Check, ChevronDown, ChevronRight, CircleHelp, Clock3, Command, Copy, Crown, Database,
  FileText, Fingerprint, Gauge, Globe2, Hammer, Home, KeyRound, LayoutDashboard, Lock,
  Menu, MessageSquare, MoreHorizontal, PanelLeftClose, PanelLeftOpen, Plus, Radar, RefreshCw,
  Search, Server, Settings, Shield, ShieldAlert, ShieldCheck, Siren, Sparkles, Ticket,
  UserRound, Users, X, Zap, CheckCircle2, XCircle, Link2, Eye, Filter
} from 'lucide-react'
import type { Activity, BotStats, CommandItem, ModuleKey, Server as ServerType } from './types'
import { getBotStats, getServers, syncAction } from './lib/api'

const purple = '#a855f7'

const commands: CommandItem[] = [
  ['warn','Warn a member','rm!warn @user reason','Moderate Members','2s','Moderation'],
  ['timeout','Timeout a member','rm!timeout @user 10m reason','Moderate Members','2s','Moderation'],
  ['mute','Mute a member','rm!mute @user reason','Moderate Members','2s','Moderation'],
  ['kick','Kick a member','rm!kick @user reason','Kick Members','3s','Moderation'],
  ['ban','Ban a member','rm!ban @user reason','Ban Members','3s','Moderation'],
  ['unban','Unban a member','rm!unban user_id','Ban Members','3s','Moderation'],
  ['softban','Ban then remove recent messages','rm!softban @user reason','Ban Members','5s','Moderation'],
  ['purge','Bulk delete messages','rm!purge 50','Manage Messages','5s','Moderation'],
  ['lock','Lock a channel','rm!lock #channel','Manage Channels','2s','Moderation'],
  ['unlock','Unlock a channel','rm!unlock #channel','Manage Channels','2s','Moderation'],
  ['slowmode','Configure slowmode','rm!slowmode 10','Manage Channels','2s','Moderation'],
  ['automod','Configure message protection','rm!automod','Manage Guild','—','AutoMod'],
  ['antinuke','Open anti-nuke controls','rm!antinuke','Administrator','—','Anti-Nuke'],
  ['antiraid','Manage raid protection','rm!antiraid','Manage Guild','—','Anti-Raid'],
  ['security','View security score','rm!security','Manage Guild','—','Security'],
  ['logs','Open logging controls','rm!logs','Manage Guild','—','Configuration'],
  ['warns','View warning history','rm!warns @user','Moderate Members','2s','Moderation'],
  ['ticket','Create a support ticket','rm!ticket','Send Messages','5s','Tickets'],
  ['welcome','Preview welcome settings','rm!welcome','Manage Guild','—','Welcome'],
  ['role','Manage roles','rm!role','Manage Roles','3s','Roles'],
  ['userinfo','Inspect a member','rm!userinfo @user','Manage Guild','2s','Utility'],
  ['serverinfo','Inspect server info','rm!serverinfo','View Server','2s','Utility'],
  ['poll','Create a poll','rm!poll question','Send Messages','5s','Fun'],
  ['balance','Check balance','rm!balance','Send Messages','2s','Economy'],
  ['daily','Claim daily reward','rm!daily','Send Messages','24h','Economy'],
  ['rank','View XP rank','rm!rank','Send Messages','2s','Levels'],
]

const commandList = commands.map(([name, description, usage, permissions, cooldown, category]) => ({ name, description, usage, permissions, cooldown, category }))

const demoServers: ServerType[] = [
  { id:'100001', name:'RM Community', icon:'RM', members:12480, online:true, manageable:true },
  { id:'100002', name:'Night Shift', icon:'NS', members:5810, online:true, manageable:true },
  { id:'100003', name:'Gaming Hub', icon:'GH', members:2220, online:false, manageable:false },
]

const demoActivities: Activity[] = [
  { id:'1', title:'Anti-nuke shield checked', meta:'RM Community · policy scan', time:'just now', tone:'green' },
  { id:'2', title:'Timeout action issued', meta:'Moderator · 10 minute timeout', time:'6m ago', tone:'purple' },
  { id:'3', title:'Raid threshold raised', meta:'Night Shift · 7 → 10 joins/15s', time:'18m ago', tone:'amber' },
  { id:'4', title:'Webhook creation blocked', meta:'Security · suspicious permission', time:'43m ago', tone:'red' },
]

const modules: { key: ModuleKey; label: string; icon: typeof Shield }[] = [
  ['overview','Overview',LayoutDashboard], ['moderation','Moderation',Hammer], ['automod','AutoMod',ShieldCheck],
  ['anti-raid','Anti-Raid',Radar], ['anti-nuke','Anti-Nuke',ShieldAlert], ['security','Security Center',Shield],
  ['warnings','Warnings',AlertTriangle], ['logs','Logs',FileText], ['tickets','Tickets',Ticket], ['welcome','Welcome',Sparkles],
  ['reaction-roles','Reaction Roles',Users], ['auto-responders','Auto Responders',MessageSquare], ['custom-commands','Custom Commands',Command],
  ['economy','Economy',Crown], ['levels','Levels',BarChart3], ['roleplay','Roleplay / Fun',Zap], ['server-config','Server Config',Settings],
  ['commands','Commands',Command], ['permissions','Permissions',KeyRound], ['whitelist','Whitelist',Fingerprint], ['trust','Trust System',Eye],
  ['bot-settings','Bot Settings',Bot],
]

function cn(...parts: Array<string | false | null | undefined>) { return parts.filter(Boolean).join(' ') }

function useRoute() {
  const [path, setPath] = useState(window.location.pathname)
  useEffect(() => {
    const fn = () => setPath(window.location.pathname)
    window.addEventListener('popstate', fn)
    return () => window.removeEventListener('popstate', fn)
  }, [])
  const go = (next: string) => { window.history.pushState({}, '', next); window.dispatchEvent(new PopStateEvent('popstate')) }
  return { path, go }
}

function App() {
  const { path, go } = useRoute()
  const [stats, setStats] = useState<BotStats>({ servers:142, users:218400, commands:642, uptime:'99.98%', connected:true })
  const [servers, setServers] = useState<ServerType[]>(demoServers)
  const [selectedServer, setSelectedServer] = useState<ServerType>(demoServers[0])
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [collapsed, setCollapsed] = useState(false)
  const [serverOpen, setServerOpen] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)
  const [search, setSearch] = useState('')
  const [toast, setToast] = useState<{kind:'success'|'error'|'info'|'warning'; message:string} | null>(null)
  const [modal, setModal] = useState<{title:string; body:string; action?:string} | null>(null)

  useEffect(() => { (async () => {
    const [s, gs] = await Promise.all([getBotStats(), getServers()])
    if (s) setStats(s)
    if (gs?.length) { setServers(gs); setSelectedServer(gs[0]) }
  })() }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setSearchOpen(true) } }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => { if (toast) { const t = setTimeout(() => setToast(null), 3200); return () => clearTimeout(t) } }, [toast])

  const navigate = (route: string) => { go(route); setSidebarOpen(false) }
  const isDashboard = path.startsWith('/dashboard') || path.startsWith('/login')

  if (!isDashboard) return <PublicApp path={path} go={go} stats={stats} />

  return (
    <div className="app-shell">
      <DashboardSidebar collapsed={collapsed} open={sidebarOpen} path={path} navigate={navigate} onCollapse={() => setCollapsed(v => !v)} />
      <div className={cn('mobile-overlay', sidebarOpen && 'show')} onClick={() => setSidebarOpen(false)} />
      <div className={cn('dashboard-main', collapsed && 'sidebar-collapsed')}>
        <Topbar
          selectedServer={selectedServer}
          serverOpen={serverOpen}
          setServerOpen={setServerOpen}
          servers={servers}
          onServer={(s) => { setSelectedServer(s); setServerOpen(false); setToast({kind:'success', message:`Switched to ${s.name}`}) }}
          onMenu={() => setSidebarOpen(true)}
          onSearch={() => setSearchOpen(true)}
          onNavigate={navigate}
        />
        <main className="page-wrap">
          <PageRouter path={path} selectedServer={selectedServer} stats={stats} navigate={navigate} toast={setToast} openModal={setModal} />
        </main>
      </div>
      {searchOpen && <SearchPalette value={search} setValue={setSearch} close={() => {setSearch(''); setSearchOpen(false)}} navigate={navigate} />}
      {modal && <ConfirmModal modal={modal} close={() => setModal(null)} confirm={() => { setToast({kind:'success', message:`${modal.title} completed`}); setModal(null) }} />}
      {toast && <Toast toast={toast} close={() => setToast(null)} />}
    </div>
  )
}

function PublicApp({ path, go, stats }: { path:string; go:(p:string)=>void; stats:BotStats }) {
  const pages = ['commands','features','security','pricing','docs','status']
  const active = path.slice(1)
  return <div className="public-shell">
    <header className="public-nav container">
      <button className="brand" onClick={() => go('/')}><span className="brand-mark">RM</span><span>RM</span></button>
      <nav className="public-links">{pages.slice(0,5).map(p => <button key={p} className={cn(active===p && 'active')} onClick={() => go(`/${p}`)}>{p[0].toUpperCase()+p.slice(1)}</button>)}</nav>
      <div className="public-actions"><button className="ghost-button" onClick={() => go('/status')}><span className="status-dot online"/>Status</button><button className="primary-button" onClick={() => go('/dashboard')}><LayoutDashboard size={16}/>Dashboard</button></div>
    </header>
    {path==='/' ? <Landing stats={stats} go={go} /> : path==='/commands' ? <CommandsPublic go={go}/> : path==='/features' ? <FeaturePublic go={go}/> : path==='/security' ? <SecurityPublic go={go}/> : path==='/pricing' ? <PricingPublic go={go}/> : path==='/docs' ? <DocsPublic go={go}/> : <StatusPublic stats={stats} go={go}/>} 
    <footer className="public-footer container"><span>RM Control Center</span><span>Built for serious communities.</span><span>v3.0</span></footer>
  </div>
}

function Landing({ stats, go }: { stats:BotStats; go:(p:string)=>void }) {
  return <>
    <section className="hero container"><div className="hero-copy"><span className="eyebrow"><span className="status-dot online"/> Discord security, rebuilt</span><h1>Keep your server<br/><span className="gradient-text">under control.</span></h1><p>RM is a modern Discord moderation and security system for communities that want serious protection without the clutter.</p><div className="hero-actions"><button className="primary-button large" onClick={() => window.open('https://discord.com/oauth2/authorize','_blank')}><Bot size={18}/>Add RM to Discord<ArrowRight size={17}/></button><button className="secondary-button large" onClick={() => go('/dashboard')}>Open Dashboard</button></div><div className="trust-row"><span><ShieldCheck size={15}/>Anti-nuke ready</span><span><Zap size={15}/>Fast controls</span><span><Lock size={15}/>Permission-aware</span></div></div><div className="hero-preview"><DashboardPreview/></div></section>
    <section className="section container"><SectionIntro label="WHY RM" title="Protection that feels like software, not setup." body="Moderation, automation and security controls live in one fast control center."/><div className="feature-grid">{[['Advanced moderation',Hammer],['Anti-nuke',ShieldAlert],['Anti-raid',Radar],['AutoMod',ShieldCheck],['Logging',FileText],['Tickets',Ticket],['Welcome system',Sparkles],['Reaction roles',Users],['Economy',Crown],['Leveling',BarChart3],['Custom commands',Command],['Server protection',Siren]].map(([label,Icon]) => <div className="feature-card" key={String(label)}><span className="icon-box"><Icon size={19}/></span><div><b>{String(label)}</b><span>Configured in a few taps.</span></div></div>)}</div></section>
    <section className="section container stats-band"><SectionIntro label="RM NETWORK" title="Built for scale." body="Live values are shown when the backend is connected, with clearly labeled demo fallback."/><div className="stat-grid">{[['Servers',stats.servers.toLocaleString(),Server],['Users',stats.users.toLocaleString(),Users],['Commands',stats.commands.toLocaleString(),Command],['Uptime',stats.uptime,Gauge]].map(([l,v,I]) => <div className="stat-tile" key={String(l)}><I size={18}/><span>{String(l)}</span><strong>{String(v)}</strong></div>)}</div></section>
    <section className="section container center-cta"><div className="cta-card"><span className="eyebrow">RM CONTROL CENTER</span><h2>Run moderation like infrastructure.</h2><p>One dashboard for your staff team. One security layer for your community.</p><button className="primary-button large" onClick={() => go('/dashboard')}>Enter the control center <ArrowRight size={16}/></button></div></section>
  </>
}

function DashboardPreview() { return <div className="preview-window"><div className="preview-top"><div className="preview-brand"><span className="brand-mark mini">RM</span>RM Control Center</div><span className="live-pill"><span className="status-dot online"/>LIVE</span></div><div className="preview-body"><div className="preview-sidebar"><span className="active">Overview</span><span>Moderation</span><span>Security</span><span>Logs</span><span>Settings</span></div><div className="preview-content"><div className="preview-heading"><div><span>Welcome back.</span><b>RM Community</b></div><span className="server-chip"><Server size={13}/> Connected</span></div><div className="preview-stats">{[['Members','12.4k'],['Actions','1,284'],['Warnings','86']].map(([a,b]) => <div key={a}><span>{a}</span><b>{b}</b></div>)}</div><div className="preview-security"><div className="score-ring"><b>92</b><span>SECURE</span></div><div><b>Security posture</b><span>Anti-nuke, raid protection and automod are active.</span><div className="mini-bars"><i/><i/><i/><i/><i/></div></div></div></div></div></div> }

function SectionIntro({ label, title, body }: {label:string; title:string; body:string}) { return <div className="section-intro"><span className="eyebrow">{label}</span><h2>{title}</h2><p>{body}</p></div> }

function DashboardSidebar({ collapsed, open, path, navigate, onCollapse }: {collapsed:boolean; open:boolean; path:string; navigate:(r:string)=>void; onCollapse:()=>void}) {
  return <aside className={cn('sidebar', collapsed && 'collapsed', open && 'mobile-open')}><div className="sidebar-brand"><button className="brand" onClick={() => navigate('/')}><span className="brand-mark">RM</span>{!collapsed && <span>RM</span>}</button>{!collapsed && <button className="collapse-button" onClick={onCollapse}><PanelLeftClose size={17}/></button>}</div><div className="sidebar-scroll"><div className="side-caption">CONTROL</div>{modules.map(([key,label,Icon]) => <button key={key} className={cn('side-link', (path==='/dashboard'&&key==='overview') || path===`/dashboard/${key}` ? 'active':'')} onClick={() => navigate(key==='overview'?'/dashboard':`/dashboard/${key}`)}><Icon size={18}/>{!collapsed && <span>{label}</span>}{!collapsed && key==='security' && <span className="side-badge">92</span>}</button>)}<div className="side-caption">WORKSPACE</div><button className="side-link" onClick={() => navigate('/commands')}><Command size={18}/>{!collapsed && <span>Command Directory</span>}</button><button className="side-link" onClick={() => navigate('/docs')}><CircleHelp size={18}/>{!collapsed && <span>Documentation</span>}</button></div><div className="sidebar-footer"><button className="profile-chip"><span className="avatar">A</span>{!collapsed && <span><b>Admin</b><small>Owner</small></span>}</button></div></aside>
}

function Topbar({selectedServer, serverOpen, setServerOpen, servers, onServer, onMenu, onSearch, onNavigate}: {selectedServer:ServerType; serverOpen:boolean; setServerOpen:(v:boolean)=>void; servers:ServerType[]; onServer:(s:ServerType)=>void; onMenu:()=>void; onSearch:()=>void; onNavigate:(r:string)=>void}) {
  return <header className="topbar"><button className="mobile-menu" onClick={onMenu}><Menu size={22}/></button><button className="server-selector" onClick={() => setServerOpen(!serverOpen)}><span className="server-avatar">{selectedServer.icon}</span><span className="server-name"><small>SERVER</small><b>{selectedServer.name}</b></span><ChevronDown size={16}/></button>{serverOpen && <div className="server-menu">{servers.map(s => <button key={s.id} disabled={!s.manageable} onClick={() => onServer(s)}><span className="server-avatar">{s.icon}</span><span><b>{s.name}</b><small>{s.members.toLocaleString()} members · {s.manageable?'Manageable':'No access'}</small></span><span className={cn('status-dot',s.online?'online':'offline')}/></button>)}</div>}<div className="topbar-right"><button className="search-trigger" onClick={onSearch}><Search size={17}/><span>Search RM...</span><kbd>⌘ K</kbd></button><button className="icon-button" title="Notifications"><Bell size={18}/><i className="notif-dot"/></button><button className="icon-button" title="Settings" onClick={() => onNavigate('/dashboard/bot-settings')}><Settings size={18}/></button><div className="top-avatar">A</div></div></header>
}

function PageRouter({path, selectedServer, stats, navigate, toast, openModal}:{path:string; selectedServer:ServerType; stats:BotStats; navigate:(r:string)=>void; toast:(t:{kind:'success'|'error'|'info'|'warning';message:string})=>void; openModal:(m:{title:string;body:string;action?:string})=>void}) {
  if (path==='/dashboard' || path==='/dashboard/overview') return <OverviewPage server={selectedServer} stats={stats} toast={toast} openModal={openModal}/>
  const key = path.replace('/dashboard/','') as ModuleKey
  return <ModulePage moduleKey={modules.some(m=>m[0]===key)?key:'commands'} server={selectedServer} toast={toast} />
}

function OverviewPage({server, stats, toast, openModal}:{server:ServerType; stats:BotStats; toast:(t:{kind:'success'|'error'|'info'|'warning';message:string})=>void; openModal:(m:{title:string;body:string;action?:string})=>void}) {
  const actions = [
    ['Warn user',AlertTriangle],['Kick member',Hammer],['Ban member',Ban],['Timeout',Clock3],['Clear messages',MessageSquare],['Lock channel',Lock],
  ] as const
  return <div className="page"><div className="page-title-row"><div><span className="eyebrow">OVERVIEW</span><h1>Welcome back.</h1><p>Here’s what’s happening in {server.name}.</p></div><div className="page-actions"><button className="secondary-button" onClick={() => toast({kind:'info',message:'Refreshing live data…'})}><RefreshCw size={16}/>Refresh</button><button className="primary-button" onClick={() => toast({kind:'success',message:'Quick action panel opened'})}><Plus size={16}/>Quick action</button></div></div><div className="metric-grid">{[['Members',server.members.toLocaleString(),'+4.8%',Users],['Moderation actions','1,284','+12%',Hammer],['Warnings','86','-3.2%',AlertTriangle],['Security events','19','-18%',ShieldAlert],['Tickets','14','+2',Ticket],['Uptime',stats.uptime,'stable',Gauge]].map(([label,value,change,Icon]) => <div className="metric-card" key={String(label)}><div className="metric-top"><span>{String(label)}</span><span className="metric-icon"><Icon size={17}/></span></div><strong>{String(value)}</strong><small className={String(change).startsWith('-')?'down':''}>{String(change)} <span>vs last period</span></small></div>)}</div><div className="overview-grid"><div className="panel activity-panel"><div className="panel-head"><div><span className="panel-kicker">ACTIVITY</span><h3>Recent events</h3></div><button className="text-button">View all <ChevronRight size={14}/></button></div>{demoActivities.map(a => <div className="activity-row" key={a.id}><span className={cn('activity-icon',a.tone)}><ActivityIcon size={15}/></span><div><b>{a.title}</b><span>{a.meta}</span></div><time>{a.time}</time></div>)}</div><div className="panel security-panel"><div className="panel-head"><div><span className="panel-kicker">SECURITY</span><h3>System posture</h3></div><span className="status-chip safe"><ShieldCheck size={13}/>SAFE</span></div><div className="security-score"><div className="ring"><span>92</span><small>/100</small></div><div><b>Excellent protection</b><span>Core security layers are online and policy checks are passing.</span></div></div><div className="check-list">{['Anti-nuke shield','Anti-raid detection','AutoMod rules','Logging pipeline'].map(x => <div key={x}><CheckCircle2 size={15}/><span>{x}</span><small>Enabled</small></div>)}</div></div></div><div className="panel quick-panel"><div className="panel-head"><div><span className="panel-kicker">ACTIONS</span><h3>Quick moderation</h3></div><MoreHorizontal size={18}/></div><div className="quick-grid">{actions.map(([label,Icon]) => <button key={label} onClick={() => openModal({title:String(label),body:'This action will open a secure moderation confirmation flow for the selected server.',action:String(label)})}><span><Icon size={17}/></span>{label}</button>)}</div></div></div>
}

function ModulePage({moduleKey, server, toast}:{moduleKey:ModuleKey; server:ServerType; toast:(t:{kind:'success'|'error'|'info'|'warning';message:string})=>void}) {
  const meta = modules.find(m => m[0]===moduleKey)
  const Icon = meta?.[2] || Shield
  const [enabled,setEnabled] = useState(true)
  const [query,setQuery] = useState('')
  const title = meta?.[1] || 'Commands'
  if (moduleKey==='commands') return <CommandsPanel toast={toast}/>
  const presets: Record<string,string[]> = {
    moderation:['Warn & timeout flow','Ban / unban actions','Purge controls','Slowmode and locking','Nickname & roles'],
    automod:['Anti-spam','Anti-flood','Anti-links','Anti-invites','Anti-caps','Anti-mentions','Anti-swear'],
    'anti-raid':['Join spike detector','Account age rules','Verification mode','Automatic lockdown','Join rate limits'],
    'anti-nuke':['Mass channel deletion','Mass role changes','Mass bans / kicks','Permission escalation','Webhook creation','Mass bot additions'],
    security:['Security score','Trusted users','Whitelist status','Verification','Audit trail'],
    warnings:['Warning history','Threshold punishments','User search','Reason templates','Automatic escalation'],
    logs:['Moderation','Messages','Members','Roles','Channels','Security','Tickets'],
    tickets:['Support tickets','Reports','Appeals','Cooldowns','Transcript settings'],
    welcome:['Welcome message','Leave message','Welcome channel','Auto role','Embed builder','DM welcome'],
    'reaction-roles':['Buttons','Select menus','Reaction roles','Role preview'],
    'auto-responders':['Exact match','Contains','Case sensitivity','Cooldowns','Channel rules'],
    'custom-commands':['Command name','Response / embed','Variables','Cooldown','Permissions'],
    economy:['Currency name','Daily rewards','Leaderboard','Shop','Admin adjustments'],
    levels:['XP gain','Cooldown','Multiplier','Role rewards','Leaderboard'],
    roleplay:['Fun commands','Roleplay commands','Image commands','Utility commands'],
    'server-config':['Prefix settings','Mention response','Command toggles','Command aliases','Cooldowns'],
    permissions:['Allowed roles','Denied roles','Allowed users','Denied users','Channel rules'],
    whitelist:['Trusted users','Trusted bots','Trusted roles','Expiration','Reason'],
    trust:['Trust tiers','Trusted staff','Permissions','Expiry rules'],
    'bot-settings':['Bot status','Command sync','Dashboard connection','Notifications','Service settings'],
  }
  const items = presets[moduleKey] || ['Configuration','Rules','Thresholds','Exceptions']
  return <div className="page"><div className="page-title-row"><div><span className="eyebrow">{title.toUpperCase()}</span><h1>{title}</h1><p>{moduleKey==='security'?'Live protection posture for ': 'Configure '}{server.name}.</p></div><div className="page-actions"><button className={cn('toggle', enabled && 'on')} onClick={() => {setEnabled(v=>!v);toast({kind:'success',message:`${title} ${enabled?'disabled':'enabled'}`})}}><span/>{enabled?'Enabled':'Disabled'}</button><button className="primary-button" onClick={() => toast({kind:'success',message:'Settings saved'})}><Check size={16}/>Save changes</button></div></div>{moduleKey==='security' ? <SecurityModule toast={toast}/> : <div className="module-layout"><div className="panel"><div className="panel-head"><div><span className="panel-kicker">CONFIGURATION</span><h3>Core controls</h3></div><Icon size={18}/></div><div className="setting-grid">{items.map((item,i) => <SettingCard key={item} label={item} index={i}/>)}</div></div><div className="panel side-config"><div className="panel-head"><div><span className="panel-kicker">LIVE PREVIEW</span><h3>Policy status</h3></div><span className="status-chip safe"><CheckCircle2 size={13}/>ACTIVE</span></div><div className="preview-config"><span className="icon-box"><Icon size={20}/></span><b>{title} is ready</b><p>Changes are staged locally and can be connected to the bot through the secure API layer.</p></div><div className="detail-list"><div><span>Server</span><b>{server.name}</b></div><div><span>Mode</span><b>{enabled?'Enforced':'Paused'}</b></div><div><span>Last sync</span><b>just now</b></div></div></div></div><div className="panel table-panel"><div className="panel-head"><div><span className="panel-kicker">MANAGED ITEMS</span><h3>{title} rules</h3></div><div className="inline-search"><Search size={15}/><input value={query} onChange={e=>setQuery(e.target.value)} placeholder="Filter rules…"/></div></div><div className="data-list">{items.filter(i => i.toLowerCase().includes(query.toLowerCase())).map((item,i) => <div className="data-row" key={item}><div className="row-main"><span className="icon-box tiny"><Icon size={15}/></span><div><b>{item}</b><span>{enabled?'Configured and monitoring':'Paused by administrator'}</span></div></div><span className={cn('status-chip', enabled?'safe':'muted')}>{enabled?'ACTIVE':'OFF'}</span><ChevronRight size={15}/></div>)}</div></div></div>
}

function SettingCard({label,index}:{label:string;index:number}) { const [on,setOn]=useState(index!==4); return <div className="setting-card"><div><b>{label}</b><span>{on?'Protection is currently active.':'Optional rule is currently disabled.'}</span></div><button className={cn('mini-toggle',on&&'on')} onClick={()=>setOn(v=>!v)}><span/></button></div> }

function SecurityModule({toast}:{toast:(t:{kind:'success'|'error'|'info'|'warning';message:string})=>void}) { return <div className="security-layout"><div className="panel big-score"><div className="score-hero"><div className="score-ring-big"><strong>92</strong><span>SECURE</span></div><div><span className="panel-kicker">SECURITY SCORE</span><h2>Excellent posture</h2><p>Most protection layers are fully configured. Complete the last checks for maximum coverage.</p><button className="secondary-button" onClick={()=>toast({kind:'info',message:'Running security scan…'})}><Radar size={16}/>Run scan</button></div></div><div className="security-bars">{[['Anti-nuke',100],['Anti-raid',94],['AutoMod',91],['Logging',100],['Verification',86],['Trust controls',78]].map(([name,val])=><div key={String(name)}><span>{String(name)}</span><b>{String(val)}%</b><i><em style={{width:`${val}%`}}/></i></div>)}</div></div><div className="panel"><div className="panel-head"><div><span className="panel-kicker">EVENT STREAM</span><h3>Security timeline</h3></div><Siren size={18}/></div>{demoActivities.concat([{id:'5',title:'Permission policy reviewed',meta:'Admin · policy center',time:'1h ago',tone:'green'}]).map(a=><div className="activity-row" key={a.id}><span className={cn('activity-icon',a.tone)}><Shield size={15}/></span><div><b>{a.title}</b><span>{a.meta}</span></div><time>{a.time}</time></div>)}</div></div> }

function CommandsPanel({toast}:{toast:(t:{kind:'success'|'error'|'info'|'warning';message:string})=>void}) { const [query,setQuery]=useState(''); const [cat,setCat]=useState('All'); const cats=['All',...Array.from(new Set(commandList.map(c=>c.category)))]; const filtered=useMemo(()=>commandList.filter(c=>(cat==='All'||c.category===cat)&&`${c.name} ${c.description} ${c.category}`.toLowerCase().includes(query.toLowerCase())),[cat,query]); return <div className="page"><div className="page-title-row"><div><span className="eyebrow">COMMAND DIRECTORY</span><h1>Commands</h1><p>Search every available RM command and its permission model.</p></div><button className="secondary-button" onClick={()=>toast({kind:'info',message:`${commandList.length} commands indexed`})}><Database size={16}/>Indexed</button></div><div className="command-tools"><div className="search-box large-search"><Search size={17}/><input value={query} onChange={e=>setQuery(e.target.value)} placeholder="Search commands, descriptions, categories…"/><kbd>/</kbd></div><div className="chip-row">{cats.map(c=><button key={c} className={cn('filter-chip',cat===c&&'active')} onClick={()=>setCat(c)}>{c}</button>)}</div></div><div className="command-grid">{filtered.map(c=><article className="command-card" key={c.name}><div className="command-head"><span className="command-icon"><Command size={17}/></span><div><b>rm!{c.name}</b><span>{c.category}</span></div><button className="icon-button small" onClick={()=>navigator.clipboard?.writeText(c.usage).then(()=>toast({kind:'success',message:'Usage copied'}))}><Copy size={15}/></button></div><p>{c.description}</p><div className="command-meta"><span><KeyRound size={13}/>{c.permissions}</span><span><Clock3 size={13}/>{c.cooldown}</span></div><div className="usage-line">{c.usage}</div></article>)}</div>{!filtered.length && <EmptyState title="No commands found" body="Try a different search or category."/>}</div> }

function SearchPalette({value,setValue,close,navigate}:{value:string;setValue:(v:string)=>void;close:()=>void;navigate:(r:string)=>void}) { const pages=[['Overview','/dashboard',LayoutDashboard],['Moderation','/dashboard/moderation',Hammer],['Security Center','/dashboard/security',Shield],['Anti-Nuke','/dashboard/anti-nuke',ShieldAlert],['Commands','/dashboard/commands',Command],['Server Config','/dashboard/server-config',Settings],['Documentation','/docs',CircleHelp]]; const filtered=pages.filter(([name])=>String(name).toLowerCase().includes(value.toLowerCase())); return <div className="modal-backdrop" onMouseDown={e=>{if(e.currentTarget===e.target)close()}}><div className="search-modal"><div className="search-modal-input"><Search size={19}/><input autoFocus value={value} onChange={e=>setValue(e.target.value)} placeholder="Jump to a page or setting…"/><kbd>ESC</kbd></div><div className="search-results">{filtered.map(([name,route,Icon])=><button key={String(route)} onClick={()=>{navigate(String(route));close()}}><span className="icon-box"><Icon size={16}/></span><span><b>{String(name)}</b><small>{String(route)}</small></span><ChevronRight size={15}/></button>)}</div><div className="search-footer"><span>RM Command Palette</span><span>Navigate with ↑ ↓ · Enter</span></div></div></div> }

function ConfirmModal({modal,close,confirm}:{modal:{title:string;body:string;action?:string};close:()=>void;confirm:()=>void}) { return <div className="modal-backdrop"><div className="confirm-modal"><div className="modal-icon danger"><ShieldAlert size={20}/></div><button className="modal-close" onClick={close}><X size={18}/></button><span className="eyebrow">CONFIRM ACTION</span><h2>{modal.title}</h2><p>{modal.body}</p><div className="modal-actions"><button className="secondary-button" onClick={close}>Cancel</button><button className="danger-button" onClick={confirm}><ShieldAlert size={16}/>{modal.action || 'Confirm'}</button></div></div></div> }

function Toast({toast,close}:{toast:{kind:'success'|'error'|'info'|'warning';message:string};close:()=>void}) { const icon={success:CheckCircle2,error:XCircle,info:CircleHelp,warning:AlertTriangle}[toast.kind]; const Icon=icon; return <div className={cn('toast',toast.kind)}><Icon size={17}/><span>{toast.message}</span><button onClick={close}><X size={14}/></button></div> }
function EmptyState({title,body}:{title:string;body:string}) { return <div className="empty-state"><div className="icon-box"><Search size={19}/></div><h3>{title}</h3><p>{body}</p></div> }

function CommandsPublic({go}:{go:(p:string)=>void}) { return <section className="public-section container"><SectionIntro label="COMMANDS" title="Everything your staff needs." body="A searchable command directory for moderation, security, utility and configuration."/><div className="command-grid public-commands">{commandList.slice(0,12).map(c=><article className="command-card" key={c.name}><div className="command-head"><span className="command-icon"><Command size={17}/></span><div><b>rm!{c.name}</b><span>{c.category}</span></div></div><p>{c.description}</p><div className="usage-line">{c.usage}</div></article>)}</div><div className="center-cta"><button className="primary-button large" onClick={()=>go('/dashboard/commands')}>Open full command explorer <ArrowRight size={16}/></button></div></section> }
function FeaturePublic({go}:{go:(p:string)=>void}) { return <section className="public-section container"><SectionIntro label="FEATURES" title="A real control center, not a template." body="RM brings moderation, automation, tickets, roles, economy and security into one coherent workspace."/><div className="feature-grid large-feature-grid">{modules.slice(1).map(([k,label,Icon])=><div className="feature-card feature-large" key={k}><span className="icon-box"><Icon size={20}/></span><div><b>{label}</b><span>Fast, permission-aware controls with mobile-first layouts.</span></div></div>)}</div><div className="center-cta"><button className="primary-button large" onClick={()=>go('/dashboard')}>Explore the dashboard <ArrowRight size={16}/></button></div></section> }
function SecurityPublic({go}:{go:(p:string)=>void}) { return <section className="public-section container"><div className="security-hero-public"><div><span className="eyebrow">SECURITY CENTER</span><h1>Layered protection for real communities.</h1><p>Detect risky behavior, lock down destructive changes, and keep staff controls auditable.</p><button className="primary-button large" onClick={()=>go('/dashboard/security')}><Shield size={17}/>Open Security Center</button></div><div className="public-score"><span>Security score</span><strong>92</strong><small>/ 100</small><div className="score-line"><i/></div><b>SAFE</b></div></div><div className="feature-grid">{[['Anti-nuke',ShieldAlert],['Anti-raid',Radar],['AutoMod',ShieldCheck],['Audit trail',FileText],['Trust system',Fingerprint],['Permission engine',KeyRound]].map(([x,I])=><div className="feature-card" key={String(x)}><span className="icon-box"><I size={18}/></span><div><b>{String(x)}</b><span>Designed to fail safe and keep staff actions visible.</span></div></div>)}</div></section> }
function PricingPublic({go}:{go:(p:string)=>void}) { return <section className="public-section container"><SectionIntro label="PLANS" title="Pricing, ready when you are." body="The plan system is structured now so billing can be connected later without rebuilding the product."/><div className="pricing-grid"><Price title="Community" price="Free" points={['Core moderation','AutoMod starter rules','Command directory','Basic logs']} /><Price title="Pro" price="$6" popular points={['Everything in Community','Advanced security','Tickets + role menus','Priority sync','Extended audit data']} /><Price title="Enterprise" price="Custom" points={['Everything in Pro','Multi-server controls','Custom policy limits','Dedicated support']} /></div><div className="center-cta"><button className="secondary-button large" onClick={()=>go('/docs')}>Read setup docs <ArrowRight size={16}/></button></div></section> }
function Price({title,price,points,popular=false}:{title:string;price:string;points:string[];popular?:boolean}) { return <div className={cn('price-card',popular&&'popular')}>{popular&&<span className="popular-tag">MOST USED</span>}<span className="panel-kicker">{title.toUpperCase()}</span><h3>{price}<small>{price.startsWith('$')?' / month':''}</small></h3>{points.map(p=><div key={p}><Check size={15}/>{p}</div>)}<button className="primary-button full">Choose {title}</button></div> }
function DocsPublic({go}:{go:(p:string)=>void}) { return <section className="public-section container"><SectionIntro label="DOCUMENTATION" title="Configure RM without guessing." body="Everything here maps to the production architecture: environment variables, Discord OAuth, bot sync and Supabase."/><div className="docs-grid">{[['Install','npm install\nnpm run dev\nnpm run build'],['Environment','DISCORD_BOT_TOKEN\nBOT_SYNC_KEY\nDISCORD_CLIENT_ID\nDISCORD_CLIENT_SECRET\nSUPABASE_URL\nSUPABASE_SERVICE_ROLE_KEY\nSESSION_SECRET'],['Bot sync','POST /api/public/bot/sync\nHeader: x-bot-key\nJSON: { action, data }'],['Deploy','Import the repository into Vercel. Keep secrets in Project Settings → Environment Variables.']].map(([a,b])=><article className="doc-card" key={String(a)}><span className="panel-kicker">{String(a).toUpperCase()}</span><pre>{String(b)}</pre></article>)}</div><div className="center-cta"><button className="primary-button large" onClick={()=>go('/dashboard')}>Enter RM Control Center <ArrowRight size={16}/></button></div></section> }
function StatusPublic({stats,go}:{stats:BotStats;go:(p:string)=>void}) { const services=[['Discord Bot',stats.connected,'99.98%'],['Dashboard',true,'99.99%'],['API',true,'99.97%'],['Database',true,'99.95%'],['Security systems',true,'99.98%']]; return <section className="public-section container"><SectionIntro label="STATUS" title="Everything that matters, visible." body="A clean service view with room for real monitoring data when connected."/><div className="status-overall"><span className="status-dot online"/><div><b>All systems operational</b><span>Last checked just now · live data when available</span></div><button className="secondary-button" onClick={()=>go('/dashboard')}>Dashboard</button></div><div className="service-list">{services.map(([name,ok,up])=><div className="service-row" key={String(name)}><div><span className={cn('status-dot',ok?'online':'offline')}/><b>{String(name)}</b></div><span>Operational</span><small>{String(up)} uptime</small><ChevronRight size={15}/></div>)}</div></section> }

export default App
