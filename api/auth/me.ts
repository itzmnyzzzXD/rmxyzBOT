import { json, sessionUser, ownedServers, allServers } from '../_auth'

export default async function handler(req:any,res:any){
  if(req.method!=='GET') return json(res,405,{ok:false,error:'Method not allowed'})
  try{
    const auth=await sessionUser(req)
    if(!auth) return json(res,401,{ok:false,error:'Authentication required'})
    const servers=auth.user.is_admin?await allServers():await ownedServers(auth.user.discord_id)
    return json(res,200,{ok:true,user:auth.user,servers})
  }catch(error){
    console.error('[auth/me]',error)
    return json(res,500,{ok:false,error:'Session lookup failed'})
  }
}
