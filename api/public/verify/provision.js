import crypto from 'node:crypto'
import { getJson, id, redis, setJson } from '../../_store.js'

function send(res,status,body){res.status(status).setHeader('Content-Type','application/json; charset=utf-8');res.end(JSON.stringify(body))}
function randomHex(bytes=32){return crypto.randomBytes(bytes).toString('hex')}
function passwordHash(password,salt=randomHex(16)){return `scrypt:${salt}:${crypto.scryptSync(password,salt,64).toString('hex')}`}
function generateUsername(discordId){return `rm_${discordId.slice(-12)}`}
function generatePassword(){return randomHex(12)}

export default async function handler(req,res){
  if(req.method!=='POST')return send(res,405,{ok:false,error:'Method not allowed'})
  const expected=String(process.env.BOT_SYNC_KEY||'').trim()
  const supplied=String(req.headers['x-bot-key']||'').trim()
  if(!expected||supplied!==expected)return send(res,401,{ok:false,error:'Invalid sync key'})

  try{
    let body=req.body
    if(typeof body==='string')body=JSON.parse(body)
    if(!body||typeof body!=='object')body={}
    const discordId=String(body.discord_id||'').trim()
    const discordUsername=String(body.discord_username||'').trim()
    const guildId=String(body.guild_id||'').trim()
    const guildName=String(body.guild_name||'').trim()
    if(!/^\d{15,25}$/.test(discordId)||!/^\d{15,25}$/.test(guildId))return send(res,400,{ok:false,error:'Invalid Discord or server identifier'})

    let userId=await redis.get<string>(`rm:user:discord:${discordId}`)
    let user=userId?await getJson<any>(`rm:user:${userId}`):null
    const username=user?.username||generateUsername(discordId)
    const password=generatePassword()
    const hashed=passwordHash(password)
    const now=new Date().toISOString()

    if(user){
      user={...user,discord_username:discordUsername||user.discord_username,password_hash:hashed}
    }else{
      user={id:id('user'),discord_id:discordId,discord_username:discordUsername||discordId,username,password_hash:hashed,email:null,created_at:now,last_login_at:null}
    }

    await setJson(`rm:user:${user.id}`,user)
    await redis.set(`rm:user:username:${username.toLowerCase()}`,user.id)
    await redis.set(`rm:user:discord:${discordId}`,user.id)
    await redis.sadd(`rm:membership:${user.id}`,guildId)

    return send(res,200,{ok:true,username:user.username,password,server:{id:guildId,name:guildName||'Discord server'}})
  }catch(error){
    console.error('[verify/provision]',error)
    return send(res,500,{ok:false,error:'Could not create dashboard credentials',detail:String(error?.message||error)})
  }
}
