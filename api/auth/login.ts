import { json, findUserByUsername, passwordHash, randomToken, saveUser, sha256, verifyPassword } from '../_auth'
import { getJson, id, setJson } from '../_store'

const ADMIN_USERNAME='admin'
const ADMIN_PASSWORD='admin'
const ADMIN_DISCORD_ID='__rm_admin__'

export default async function handler(req:any,res:any){
  if(req.method!=='POST') return json(res,405,{ok:false,error:'Method not allowed'})
  try{
    const body=typeof req.body==='string'?JSON.parse(req.body):req.body||{}
    const username=String(body.username||'').trim()
    const password=String(body.password||'')

    let user=await findUserByUsername(username)

    if(username===ADMIN_USERNAME && password===ADMIN_PASSWORD){
      if(user && String(user.discord_id)!==ADMIN_DISCORD_ID) return json(res,401,{ok:false,error:'Invalid username or password'})
      if(!user){
        user={id:id('user'),discord_id:ADMIN_DISCORD_ID,discord_username:'RM Admin',username:ADMIN_USERNAME,email:null,password_hash:passwordHash(ADMIN_PASSWORD),created_at:new Date().toISOString(),last_login_at:null}
      }
    }else{
      if(!user || !verifyPassword(password,user.password_hash)) return json(res,401,{ok:false,error:'Invalid username or password'})
    }

    user={...user,last_login_at:new Date().toISOString()}
    await saveUser(user)

    const token=randomToken(32)
    const expiresAt=new Date(Date.now()+180*24*60*60*1000).toISOString()
    await setJson(`rm:session:${sha256(token)}`,{id:id('session'),user_id:user.id,expires_at:expiresAt,last_seen_at:new Date().toISOString()},180*24*60*60)

    return json(res,200,{ok:true,token,user:{username:user.username,discord_username:user.discord_username,is_admin:String(user.discord_id)===ADMIN_DISCORD_ID}})
  }catch(error){
    console.error('[auth/login]',error)
    return json(res,500,{ok:false,error:'Login failed'})
  }
}
