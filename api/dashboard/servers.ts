import { json, ownedServers, sessionUser } from '../_auth'

export default async function handler(req:any,res:any){
  if(req.method!=='GET') return json(res,405,{ok:false,error:'Method not allowed'})
  try{
    const auth=await sessionUser(req)
    if(!auth) return json(res,401,{ok:false,error:'Authentication required'})
    return json(res,200,{ok:true,servers:await ownedServers(auth.user.discord_id)})
  }catch(error){
    console.error('[dashboard/servers]',error)
    return json(res,500,{ok:false,error:'Could not load servers'})
  }
}
