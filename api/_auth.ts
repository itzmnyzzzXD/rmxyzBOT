import crypto from 'node:crypto'
import { del, getJson, id, redis, setJson } from './_store'

const ADMIN_DISCORD_ID='__rm_admin__'

export function json(res:any,status:number,body:any){
  res.status(status).setHeader('Content-Type','application/json')
  res.end(JSON.stringify(body))
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

export async function findUserByUsername(username:string){
  const userId=await redis.get<string>(`rm:user:username:${username.toLowerCase()}`)
  return userId?await getJson<any>(`rm:user:${userId}`):null
}

export async function findUserByDiscord(discordId:string){
  const userId=await redis.get<string>(`rm:user:discord:${discordId}`)
  return userId?await getJson<any>(`rm:user:${userId}`):null
}

export async function saveUser(user:any){
  await setJson(`rm:user:${user.id}`,user)
  await redis.set(`rm:user:username:${String(user.username).toLowerCase()}`,user.id)
  await redis.set(`rm:user:discord:${user.discord_id}`,user.id)
  return user
}

export async function sessionUser(req:any){
  const raw=String(req.headers.authorization||'')
  const token=raw.startsWith('Bearer ')?raw.slice(7).trim():''
  if(!token) return null
  const tokenHash=sha256(token)
  const session=await getJson<any>(`rm:session:${tokenHash}`)
  if(!session || new Date(session.expires_at).getTime()<=Date.now()){
    if(session) await del(`rm:session:${tokenHash}`)
    return null
  }
  const user=await getJson<any>(`rm:user:${session.user_id}`)
  if(!user) return null
  const now=new Date().toISOString()
  const refreshedExpiry=new Date(Date.now()+180*24*60*60*1000).toISOString()
  const refreshed={...session,last_seen_at:now,expires_at:refreshedExpiry}
  await setJson(`rm:session:${tokenHash}`,refreshed,180*24*60*60)
  return {session:refreshed,user:{...user,is_admin:String(user.discord_id)===ADMIN_DISCORD_ID}}
}

export async function getSyncState(){
  return await getJson<any>('rm:sync:state') || {
    servers:0,users:0,commands:0,uptime:'offline',connected:false,payload:{guilds:[]},updated_at:null,
  }
}

function mapServer(g:any,online:boolean,owner=false){
  return {
    id:String(g.id),name:String(g.name||'Unknown server'),icon:g.icon?String(g.icon):'RM',
    members:Number(g.member_count||0),channels:Number(g.channel_count||0),roles:Number(g.role_count||0),owner,online,
  }
}

export async function ownedServers(discordId:string){
  const state=await getSyncState()
  const guilds=Array.isArray(state?.payload?.guilds)?state.payload.guilds:[]
  return guilds.filter((g:any)=>String(g.owner_id||'')===String(discordId)).map((g:any)=>mapServer(g,Boolean(state.connected),true))
}

export async function allServers(){
  const state=await getSyncState()
  const guilds=Array.isArray(state?.payload?.guilds)?state.payload.guilds:[]
  return guilds.map((g:any)=>mapServer(g,Boolean(state.connected),false))
}

export async function requireServerOwner(req:any,res:any,serverId:string){
  const auth=await sessionUser(req)
  if(!auth){ json(res,401,{ok:false,error:'Authentication required'}); return null }
  const servers=auth.user.is_admin?await allServers():await ownedServers(auth.user.discord_id)
  const server=servers.find((s:any)=>s.id===String(serverId))
  if(!server){ json(res,403,{ok:false,error:'You do not have access to this server'}); return null }
  return {auth,server}
}

export function newUserId(){ return id('user') }
