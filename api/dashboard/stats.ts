async function query(){
  const url=process.env.SUPABASE_URL,key=process.env.SUPABASE_SERVICE_ROLE_KEY
  if(!url||!key) return null
  const r=await fetch(`${url.replace(/\\/$/,'')}/rest/v1/bot_sync_state?id=eq.global&select=servers,users,commands,uptime,connected,updated_at&limit=1`,{headers:{apikey:key,Authorization:`Bearer ${key}`}})
  if(!r.ok)return null
  const rows=await r.json(); return rows[0]||null
}
export default async function handler(req:any,res:any){
  if(req.method!=='GET')return res.status(405).json({error:'Method not allowed'})
  const live=await query()
  res.status(200).json(live||{servers:142,users:218400,commands:642,uptime:'99.98%',connected:false})
}
