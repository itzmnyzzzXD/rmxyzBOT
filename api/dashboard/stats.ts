import { getSyncState } from '../_auth'

export default async function handler(req:any,res:any){
  if(req.method!=='GET') return res.status(405).json({ok:false,error:'Method not allowed'})
  const state=await getSyncState()
  return res.status(200).json({
    servers:Number(state.servers||0),
    users:Number(state.users||0),
    commands:Number(state.commands||0),
    uptime:String(state.uptime||'offline'),
    connected:Boolean(state.connected),
    updated_at:state.updated_at||null,
  })
}
