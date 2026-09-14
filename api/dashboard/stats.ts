async function query(){
  const url=String(process.env.SUPABASE_URL||'').trim().replace(/\/$/,'')
  const key=String(process.env.SUPABASE_SERVICE_ROLE_KEY||'').trim()
  if(!url||!key)return null
  try{
    const r=await fetch(`${url}/rest/v1/bot_sync_state?id=eq.global&select=servers,users,commands,uptime,connected,updated_at&limit=1`,{headers:{apikey:key,Authorization:`Bearer ${key}`}})
    if(!r.ok)return null
    const rows=await r.json()
    return rows[0]||null
  }catch{return null}
}
export default async function handler(req:any,res:any){
  if(req.method!=='GET')return res.status(405).json({ok:false,error:'Method not allowed'})
  const live=await query()
  return res.status(200).json(live||{servers:0,users:0,commands:0,uptime:'offline',connected:false})
}
