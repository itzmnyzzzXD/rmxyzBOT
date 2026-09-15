import { json, passwordHash, randomToken, sha256, supabase, verifyPassword } from '../_auth'

const ADMIN_USERNAME = 'admin'
const ADMIN_PASSWORD = 'admin'
const ADMIN_DISCORD_ID = '__rm_admin__'

export default async function handler(req:any,res:any){
  if(req.method!=='POST') return json(res,405,{ok:false,error:'Method not allowed'})
  try{
    const body=typeof req.body==='string'?JSON.parse(req.body):req.body||{}
    const username=String(body.username||'').trim()
    const password=String(body.password||'')

    if(username===ADMIN_USERNAME && password===ADMIN_PASSWORD){
      const existingRows=await supabase(`dashboard_users?username=eq.${encodeURIComponent(ADMIN_USERNAME)}&select=id,discord_id,discord_username,username,email,password_hash&limit=1`)
      let user=Array.isArray(existingRows)?existingRows[0]:existingRows

      if(!user){
        const created=await supabase('dashboard_users',{
          method:'POST',
          body:JSON.stringify({discord_id:ADMIN_DISCORD_ID,discord_username:'RM Admin',username:ADMIN_USERNAME,email:null,password_hash:passwordHash(ADMIN_PASSWORD)}),
          headers:{'Content-Type':'application/json'},
        })
        user=Array.isArray(created)?created[0]:created
      }else if(String(user.discord_id)!==ADMIN_DISCORD_ID){
        return json(res,401,{ok:false,error:'Invalid username or password'})
      }

      const token=randomToken(32)
      const expiresAt=new Date(Date.now()+180*24*60*60*1000).toISOString()
      await supabase('dashboard_sessions',{method:'POST',body:JSON.stringify({user_id:user.id,token_hash:sha256(token),expires_at:expiresAt}),headers:{'Content-Type':'application/json'}})
      await supabase(`dashboard_users?id=eq.${encodeURIComponent(user.id)}`,{method:'PATCH',body:JSON.stringify({last_login_at:new Date().toISOString()}),headers:{'Content-Type':'application/json'}})
      return json(res,200,{ok:true,token,user:{username:ADMIN_USERNAME,discord_username:'RM Admin',is_admin:true}})
    }

    const rows=await supabase(`dashboard_users?username=eq.${encodeURIComponent(username)}&select=id,discord_id,discord_username,username,email,password_hash&limit=1`)
    const user=Array.isArray(rows)?rows[0]:rows
    if(!user || !verifyPassword(password,user.password_hash)) return json(res,401,{ok:false,error:'Invalid username or password'})
    const token=randomToken(32)
    const expiresAt=new Date(Date.now()+180*24*60*60*1000).toISOString()
    await supabase('dashboard_sessions',{method:'POST',body:JSON.stringify({user_id:user.id,token_hash:sha256(token),expires_at:expiresAt}),headers:{'Content-Type':'application/json'}})
    await supabase(`dashboard_users?id=eq.${encodeURIComponent(user.id)}`,{method:'PATCH',body:JSON.stringify({last_login_at:new Date().toISOString()}),headers:{'Content-Type':'application/json'}})
    return json(res,200,{ok:true,token,user:{username:user.username,discord_username:user.discord_username}})
  }catch(error){
    console.error('[auth/login]',error)
    return json(res,500,{ok:false,error:'Login failed'})
  }
}
