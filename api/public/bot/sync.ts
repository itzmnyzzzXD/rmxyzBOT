function json(res:any,status:number,body:any){res.status(status).setHeader('Content-Type','application/json');res.end(JSON.stringify(body))}

async function supabase(path:string, init:any={}){
  const url=(process.env.SUPABASE_URL||'').trim().replace(/\/$/,'')
  const key=(process.env.SUPABASE_SERVICE_ROLE_KEY||'').trim()
  if(!url||!key) return null
  try{
    const response=await fetch(`${url}/rest/v1/${path}`,{
      ...init,
      headers:{apikey:key,Authorization:`Bearer ${key}`,Prefer:'return=representation',...(init.headers||{})},
    })
    if(!response.ok){
      console.error(`[supabase] ${response.status} ${path}`)
      return null
    }
    const text=await response.text()
    if(!text) return null
    try{return JSON.parse(text)}catch{return null}
  }catch(error){
    console.error('[supabase] request failed',error)
    return null
  }
}

export default async function handler(req:any,res:any){
  if(req.method!=='POST') return json(res,405,{ok:false,error:'Method not allowed'})

  const expected=(process.env.BOT_SYNC_KEY || process.env.bot_sync_key || '').trim()
  const raw=req.headers['x-bot-key']
  const supplied=(Array.isArray(raw)?raw[0]:raw || '').trim()
  if(!expected || !supplied || supplied!==expected){
    return json(res,401,{ok:false,error:'Invalid sync key'})
  }

  try{
    const body=typeof req.body==='string'?JSON.parse(req.body):req.body||{}
    const action=String(body.action||'heartbeat')
    const data=body.data&&typeof body.data==='object'?body.data:{}
    const receivedAt=new Date().toISOString()

    if(action==='config'){
      const guildId=String(data.guild_id||'')
      if(!guildId) return json(res,200,{ok:true,config:[],words:[],received_at:receivedAt})
      const config=await supabase(`server_settings?server_id=eq.${encodeURIComponent(guildId)}&select=module,enabled,settings`)
      const words=await supabase(`blocked_words?server_id=eq.${encodeURIComponent(guildId)}&select=id,word,severity`)
      return json(res,200,{ok:true,config:config||[],words:words||[],received_at:receivedAt})
    }

    if(action==='case'){
      const created=await supabase('moderation_cases',{method:'POST',body:JSON.stringify({
        server_id:String(data.guild_id||''), action_type:String(data.action_type||'unknown'),
        target_id:String(data.target_id||''), target_tag:String(data.target_tag||''),
        moderator_id:String(data.moderator_id||''), reason:data.reason||null,
        duration_seconds:Number(data.duration_seconds||data.duration||0)||null, created_at:receivedAt,
      }),headers:{'Content-Type':'application/json'}})
      const latest=Array.isArray(created)?created[0]:created
      return json(res,200,{ok:true,case_number:latest?.id||null})
    }

    if(action==='log' || action==='event'){
      await supabase('audit_logs',{method:'POST',body:JSON.stringify({
        server_id:String(data.guild_id||data.server_id||''), event_type:String(data.event_type||data.category||action),
        actor_id:data.actor_id?String(data.actor_id):data.moderator_id?String(data.moderator_id):null,
        target_id:data.target_id?String(data.target_id):null,
        details:data,created_at:receivedAt,
      }),headers:{'Content-Type':'application/json'}})
      return json(res,200,{ok:true})
    }

    if(action==='security' || action==='security_event'){
      await supabase('audit_logs',{method:'POST',body:JSON.stringify({
        server_id:String(data.guild_id||data.server_id||''), event_type:`security:${String(data.event_type||data.system||'event')}`,
        actor_id:data.actor_id?String(data.actor_id):null,target_id:data.target_id?String(data.target_id):null,
        details:data,created_at:receivedAt,
      }),headers:{'Content-Type':'application/json'}})
      return json(res,200,{ok:true})
    }

    if(action==='heartbeat' || action==='stats' || action==='status'){
      const row={
        id:'global',
        servers:Number(data.server_count||data.guild_count||data.servers||0),
        users:Number(data.member_count||data.user_count||data.users||0),
        commands:Number(data.command_count||data.commands_processed||data.commands||0),
        uptime:String(data.uptime||data.status||'online'),
        connected:true,payload:data,updated_at:receivedAt,
      }
      await supabase('bot_sync_state',{method:'POST',body:JSON.stringify(row),headers:{'Content-Type':'application/json','Prefer':'resolution=merge-duplicates,return=representation'}})
      return json(res,200,{ok:true,received_at:receivedAt})
    }

    if(action==='guilds'){
      const guilds=Array.isArray(data.guilds)?data.guilds:[]
      const users=guilds.reduce((sum:number,g:any)=>sum+Number(g.member_count||0),0)
      await supabase('bot_sync_state',{method:'POST',body:JSON.stringify({id:'global',servers:guilds.length,users,connected:true,payload:{guilds},updated_at:receivedAt}),headers:{'Content-Type':'application/json','Prefer':'resolution=merge-duplicates,return=representation'}})
      return json(res,200,{ok:true,received_at:receivedAt})
    }

    if(action==='tasks'){
      const pending=await supabase(`bot_tasks?status=eq.pending&order=created_at.asc&limit=25`)
      if(Array.isArray(pending)&&pending.length){
        for(const task of pending){
          if(task?.id){
            await supabase(`bot_tasks?id=eq.${encodeURIComponent(String(task.id))}`,{method:'PATCH',body:JSON.stringify({status:'running',claimed_at:receivedAt}),headers:{'Content-Type':'application/json'}})
          }
        }
        return json(res,200,{ok:true,tasks:pending})
      }
      return json(res,200,{ok:true,tasks:[]})
    }

    if(action==='task_done'){
      const id=String(data.id||'')
      if(id) await supabase(`bot_tasks?id=eq.${encodeURIComponent(id)}`,{method:'PATCH',body:JSON.stringify({status:data.error?'failed':'completed',error:data.error||null,completed_at:receivedAt}),headers:{'Content-Type':'application/json'}})
      return json(res,200,{ok:true})
    }

    return json(res,200,{ok:true,action,received_at:receivedAt})
  }catch(error){
    console.error('[bot-sync]',error)
    return json(res,200,{ok:true,degraded:true,error:'Sync backend temporarily unavailable',received_at:new Date().toISOString()})
  }
}
