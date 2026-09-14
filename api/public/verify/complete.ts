import { json, passwordHash, randomToken, sha256, supabase } from '../../_auth'

function validUsername(value:string){ return /^[a-zA-Z0-9_.-]{3,24}$/.test(value) }
function validPassword(value:string){ return value.length>=8 && value.length<=128 }

export default async function handler(req:any,res:any){
  if(req.method!=='POST') return json(res,405,{ok:false,error:'Method not allowed'})
  try{
    const body=typeof req.body==='string'?JSON.parse(req.body):req.body||{}
    const token=String(body.token||'').trim()
    const username=String(body.username||'').trim()
    const password=String(body.password||'')
    if(!token||!validUsername(username)||!validPassword(password)) return json(res,400,{ok:false,error:'Use a valid username and a password with at least 8 characters'})
    const rows=await supabase(`dashboard_verify_tokens?token_hash=eq.${encodeURIComponent(sha256(token))}&select=*&limit=1`)
    const record=Array.isArray(rows)?rows[0]:rows
    if(!record || record.used_at || new Date(record.expires_at).getTime()<=Date.now()) return json(res,400,{ok:false,error:'This verification link is invalid or expired'})

    const existing=await supabase(`dashboard_users?or=(discord_id.eq.${encodeURIComponent(record.discord_id)},username.eq.${encodeURIComponent(username)})&select=id,discord_id,username&limit=5`)
    const conflict=Array.isArray(existing)?existing[0]:existing
    if(conflict && String(conflict.discord_id)!==String(record.discord_id)) return json(res,409,{ok:false,error:'That dashboard username is already taken'})

    let user:any=Array.isArray(existing)?existing.find((x:any)=>String(x.discord_id)===String(record.discord_id)):null
    if(user){
      const updated=await supabase(`dashboard_users?id=eq.${encodeURIComponent(user.id)}`,{method:'PATCH',body:JSON.stringify({username,password_hash:passwordHash(password),discord_username:record.discord_username}),headers:{'Content-Type':'application/json'}})
      user=Array.isArray(updated)?updated[0]:updated
    }else{
      const created=await supabase('dashboard_users',{method:'POST',body:JSON.stringify({
        discord_id:record.discord_id,discord_username:record.discord_username,username,password_hash:passwordHash(password),
      }),headers:{'Content-Type':'application/json'}})
      user=Array.isArray(created)?created[0]:created
    }

    await supabase('dashboard_memberships',{method:'POST',body:JSON.stringify({user_id:user.id,server_id:record.guild_id,role:'owner'}),headers:{'Content-Type':'application/json','Prefer':'resolution=merge-duplicates,return=representation'}})
    await supabase(`dashboard_verify_tokens?id=eq.${encodeURIComponent(record.id)}`,{method:'PATCH',body:JSON.stringify({used_at:new Date().toISOString()}),headers:{'Content-Type':'application/json'}})

    const sessionToken=randomToken(32)
    await supabase('dashboard_sessions',{method:'POST',body:JSON.stringify({user_id:user.id,token_hash:sha256(sessionToken),expires_at:new Date(Date.now()+30*24*60*60*1000).toISOString()}),headers:{'Content-Type':'application/json'}})
    return json(res,200,{ok:true,token:sessionToken,user:{username:user.username,discord_username:user.discord_username}})
  }catch(error){
    console.error('[verify/complete]',error)
    return json(res,500,{ok:false,error:'Could not finish account setup'})
  }
}
