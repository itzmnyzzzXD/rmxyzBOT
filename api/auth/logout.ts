import { json, sha256 } from '../_auth'
import { del } from '../_store'

export default async function handler(req:any,res:any){
  if(req.method!=='POST') return json(res,405,{ok:false,error:'Method not allowed'})
  const raw=String(req.headers.authorization||'')
  const token=raw.startsWith('Bearer ')?raw.slice(7).trim():''
  if(token){ try{ await del(`rm:session:${sha256(token)}`) }catch{} }
  return json(res,200,{ok:true})
}
