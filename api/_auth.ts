import crypto from 'node:crypto'

const ADMIN_DISCORD_ID='__rm_admin__'

export function json(res:any,status:number,body:any){
  res.status(status).setHeader('Content-Type','application/json')
  res.end(JSON.stringify(body))
}

export async function supabase(path:string, init:any={}){
  const url=process.env.SUPABASE_URL
  const key=process.env.SUPABASE_SERVICE_ROLE_KEY
  if(!url||!key) throw new Error('Supabase environment variables are missing')
  const response=await fetch(`${url.replace(/\/$/,'')}/rest/v1/${path}`,{
    ...init,
    headers:{apikey:key,Authorization:`Bearer ${key}`,Prefer:'return=representation',...(init.headers||{})},
  })
  const text=await response.text()
  if(!response.ok) throw new Error(`Supabase ${response.status}: ${text.slice(0,500)}`)
  return text?JSON.parse(text):[]
}

export function randomToken(bytes=32){ return crypto.randomBytes(bytes).toString('hex') }
export function sha256(value:string){ return crypto.createHash('sha256').update(value).digest('hex') }

export function passwordHash(password:string, salt=randomToken(16)){
  const derived=crypto.scryptSync(password,salt,64).toString('hex')
  return `scrypt:${salt}:${derived}`
}

export function verifyPassword(password:string, encoded:string){
  const [kind,salt,expected]=String(encoded||'').split(':')
  if(kind!=='scrypt'||!salt||!expected) return false
  const actual=crypto.scryptSync(password,salt,64).toString('hex')
  return crypto.timingSafeEqual(Buffer.from(actual,'hex'),Buffer.from(expected,'hex'))
}

export async function sessionUser(req:any){
  const raw=String(req.headers.authorization||'')
  const token=raw.startsWith('Bearer ')?raw.slice(7).trim():''
  if(!token) return null
  const rows=await supabase(`dashboard_sessions?token_hash=eq.${encodeURIComponent(sha256(token))}&select=id,user_id,expires_at`)
  const session=Array.isArray(rows)?rows[0]:rows
  if(!session || new Date(session.expires_at).getTime()<=Date.now()) return null
  const users=await supabase(`dashboard_users?id=eq.${encodeURIComponent(session.user_id)}&select=id,discord_id,discord_username,username,email,created_at`)
  const user=Array.isArray(users)?users[0]:users
  if(!user) return null
  const isAdmin=String(user.discord_id)===ADMIN_DISCORD_ID
  const now=new Date().toISOString()
  const refreshedExpiry=new Date(Date.now()+180*24*60*60*1000).toISOString()
  await supabase(`dashboard_sessions?id=eq.${encodeURIComponent(session.id)}`,{method:'PATCH',body:JSON.stringify({last_seen_at:now,expires_at:refreshedExpiry}),headers:{'Content-Type':'application/json'}})
  return {session:{...session,expires_at:refreshedExpiry},user:{...user,is_admin:isAdmin}}
}

export async function ownedServers(discordId:string){
  const stateRows=await supabase('bot_sync_state?id=eq.global&select=payload,connected,updated_at')
  const state=Array.isArray(stateRows)?stateRows[0]:stateRows
  const guilds=Array.isArray(state?.payload?.guilds)?state.payload.guilds:[]
  return guilds.filter((g:any)=>String(g.owner_id||'')===String(discordId)).map((g:any)=>({
    id:String(g.id),
    name:String(g.name||'Unknown server'),
    icon:g.icon?String(g.icon):'RM',
    members:Number(g.member_count||0),
    channels:Number(g.channel_count||0),
    roles:Number(g.role_count||0),
    owner:true,
    online:Boolean(state?.connected),
  }))
}

export async function allServers(){
  const stateRows=await supabase('bot_sync_state?id=eq.global&select=payload,connected,updated_at')
  const state=Array.isArray(stateRows)?stateRows[0]:stateRows
  const guilds=Array.isArray(state?.payload?.guilds)?state.payload.guilds:[]
  return guilds.map((g:any)=>({
    id:String(g.id),
    name:String(g.name||'Unknown server'),
    icon:g.icon?String(g.icon):'RM',
    members:Number(g.member_count||0),
    channels:Number(g.channel_count||0),
    roles:Number(g.role_count||0),
    owner:false,
    online:Boolean(state?.connected),
  }))
}

export async function requireServerOwner(req:any,res:any,serverId:string){
  const auth=await sessionUser(req)
  if(!auth){ json(res,401,{ok:false,error:'Authentication required'}); return null }
  const servers=auth.user.is_admin?await allServers():await ownedServers(auth.user.discord_id)
  const server=servers.find((s:any)=>s.id===String(serverId))
  if(!server){ json(res,403,{ok:false,error:'You do not have access to this server'}); return null }
  return {auth,server}
}
