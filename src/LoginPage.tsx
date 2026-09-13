import { ArrowRight, Bot, LockKeyhole, ShieldCheck } from 'lucide-react'
import './login.css'

export default function LoginPage(){
  const start=()=>{window.location.href='https://discord.com/oauth2/authorize'}
  return <div className="public-shell"><main className="login-screen"><div className="login-card"><div className="login-logo"><span className="brand-mark">RM</span></div><span className="eyebrow"><span className="status-dot online"/> DISCORD OAUTH</span><h1>Sign in to RM.</h1><p>Connect your Discord account to manage servers where you have the right permissions.</p><div className="login-points"><div><ShieldCheck size={16}/><span>Permission-aware server access</span></div><div><LockKeyhole size={16}/><span>No bot token is exposed to the browser</span></div><div><Bot size={16}/><span>Built around your existing RM bot</span></div></div><button className="primary-button large full" onClick={start}>Continue with Discord <ArrowRight size={16}/></button><small className="login-note">OAuth credentials must be configured in Vercel before production authentication is enabled.</small></div></main></div>
}
