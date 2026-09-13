function json(res:any,status:number,body:any){res.status(status).setHeader('Content-Type','application/json');res.end(JSON.stringify(body))}

async function supabase(path:string, init:any={}){
  const url=process.env.SUPABASE_URL
  const key=process.env.SUPABASE_SERVICE_ROLE_KEY
  if(!url||!key) return null
  const response=await fetch(`${url.replace(/\\/$/,'')}/rest/v1/${path}`,{
    ...init,
    headers:{apikey:key,Authorization:`Bearer ${key}`,Prefer:'return=representation',...(init.headers||{})},
  })
  if(!response.ok) return null
  const text=await response.text()
  return text?JSON.parse(text):null
}

export default async function handler(req:any,res:any){
  if(req.method!=='POST') return json(res,405,{ok:false,error:'Method not allowed'})
  const expected=process.env.BOT_SYNC_KEY
  const supplied=req.headers['x-bot-key']
  if(!expected || supplied!==expected) return json(res,401,{ok:false,error:'Invalid sync key'})
  try{
    const body=typeof req.body==='string'?JSON.parse(req.body):req.body||{}
    const action=String(body.action||'heartbeat')
    const data=body.data&&typeof body.data==='object'?body.data:{}
    const receivedAt=new Date().toISOString()

    if(action==='config'){
      const guildId=String(data.guild_id||'')
      const config=await supabase(`server_settings?server_id=eq.${encodeURIComponent(guildId)}&select=module,enabled,settings`)
      const words=await supabase(`blocked_words?server_id=eq.${encodeURIComponent(guildId)}&select=id,word,severity`)
      return json(res,200,{ok:true,config:config||[],words:words||[],received_at:receivedAt})
    }

    if(action==='case'){
      await supabase('moderation_cases',{method:'POST',body:JSON.stringify({
        server_id:String(data.guild_id||''), action_type:String(data.action_type||'unknown'),
        target_id:String(data.target_id||''), target_tag:String(data.target_tag||''),
        moderator_id:String(data.moderator_id||''), reason:data.reason||null,
        duration_seconds:data.duration||null, created_at:receivedAt,
      }),headers:{'Content-Type':'application/json'}})
      return json(res,200,{ok:true})
    }

    if(action==='event' || action==='security_event'){
      await supabase('audit_logs',{method:'POST',body:JSON.stringify({
        server_id:String(data.guild_id||data.server_id||''), event_type:action,
        actor_id:data.actor_id?String(data.actor_id):null,target_id:data.target_id?String(data.target_id):null,
        details:data,created_at:receivedAt,
      }),headers:{'Content-Type':'application/json'}})
      return json(res,200,{ok:true})
    }

    if(action==='stats' || action==='heartbeat' || action==='status'){
      const row={
        id:'global', servers:Number(data.server_count||data.servers||0), users:Number(data.member_count||data.users||0),
        commands:Number(data.command_count||data.commands||0), uptime:String(data.uptime||'0s'), connected:true,
        payload:data,updated_at:receivedAt,
      }
      await supabase('bot_sync_state',{method:'POST',body:JSON.stringify(row),headers:{'Content-Type':'application/json','Prefer':'resolution=merge-duplicates,return=representation'}})
      return json(res,200,{ok:true,received_at:receivedAt})
    }

    return json(res,200,{ok:true,action,received_at:receivedAt})
  }catch(error){
    console.error('[bot-sync]',error)
    return json(res,500,{ok:false,error:'Sync failed'})
  }
}
