import { json, sha256, supabase } from '../_auth'

export default async function handler(req:any,res:any){
  if(req.method!=='POST') return json(res,405,{ok:false,error:'Method not allowed'})
  const raw=String(req.headers.authorization||'')
  const token=raw.startsWith('Bearer ')?raw.slice(7).trim():''
  if(token){
    try{ await supabase(`dashboard_sessions?token_hash=eq.${encodeURIComponent(sha256(token))}`,{method:'DELETE'}) }catch{}
  }
  return json(res,200,{ok:true})
}
