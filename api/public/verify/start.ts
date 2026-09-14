import { json, randomToken, sha256, supabase } from '../../_auth'

export default async function handler(req:any,res:any){
  if(req.method!=='POST') return json(res,405,{ok:false,error:'Method not allowed'})
  const expected=process.env.BOT_SYNC_KEY
  if(!expected || req.headers['x-bot-key']!==expected) return json(res,401,{ok:false,error:'Invalid sync key'})
  try{
    const body=typeof req.body==='string'?JSON.parse(req.body):req.body||{}
    const discordId=String(body.discord_id||'')
    const discordUsername=String(body.discord_username||'')
    const guildId=String(body.guild_id||'')
    const email=String(body.email||'').trim().toLowerCase() || null
    if(!discordId||!guildId) return json(res,400,{ok:false,error:'Discord and guild identifiers are required'})
    const token=randomToken(32)
    await supabase('dashboard_verify_tokens',{method:'POST',body:JSON.stringify({
      token_hash:sha256(token),discord_id:discordId,discord_username:discordUsername,guild_id:guildId,
      email,expires_at:new Date(Date.now()+15*60*1000).toISOString(),
    }),headers:{'Content-Type':'application/json'}})
    const base=(process.env.DASHBOARD_URL||'').replace(/\/$/,'')
    if(!base) throw new Error('DASHBOARD_URL is missing')
    return json(res,200,{ok:true,url:`${base}/verify/${token}`})
  }catch(error){
    console.error('[verify/start]',error)
    return json(res,500,{ok:false,error:'Could not create verification link'})
  }
}
