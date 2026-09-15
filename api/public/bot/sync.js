import { getJson, id, redis, setJson } from '../../_store.js'

function send(res,status,body){
  res.status(status).setHeader('Content-Type','application/json; charset=utf-8')
  return res.end(JSON.stringify(body))
}
function env(name){ return String(process.env[name]||'').trim() }
function getBody(req){
  if(!req||req.body==null)return {}
  if(typeof req.body==='object')return req.body
  try{return JSON.parse(String(req.body))}catch{return {}}
}

export default async function handler(req,res){
  if(req.method!=='POST')return send(res,405,{ok:false,error:'Method not allowed'})
  const expected=(env('BOT_SYNC_KEY')||env('bot_sync_key')).trim()
  const raw=req.headers['x-bot-key']
  const supplied=String(Array.isArray(raw)?raw[0]||'':raw||'').trim()
  if(!expected||!supplied||supplied!==expected)return send(res,401,{ok:false,error:'Invalid sync key'})

  try{
    const body=getBody(req)
    const action=String(body.action||'heartbeat')
    const data=body.data&&typeof body.data==='object'?body.data:{}
    const now=new Date().toISOString()

    if(action==='config'){
      const guildId=String(data.guild_id||'')
      if(!guildId)return send(res,200,{ok:true,config:[],words:[],received_at:now})
      const configKeys=await redis.keys(`rm:config:${guildId}:*`)
      const config=await Promise.all(configKeys.map(async key=>await getJson<any>(key)))
      const wordIds=await redis.lrange<string>(`rm:blocked_ids:${guildId}`,0,99)
      const words=await Promise.all(wordIds.map(async wordId=>await getJson<any>(`rm:blocked:${guildId}:${wordId}`)))
      return send(res,200,{ok:true,config:config.filter(Boolean),words:words.filter(Boolean),received_at:now})
    }

    if(action==='case'){
      const caseNumber=await redis.incr('rm:case_seq')
      await setJson(`rm:case:${caseNumber}`,{id:caseNumber,server_id:String(data.guild_id||''),action_type:String(data.action_type||'unknown'),target_id:String(data.target_id||''),target_tag:String(data.target_tag||''),moderator_id:String(data.moderator_id||''),reason:data.reason||null,duration_seconds:Number(data.duration_seconds||data.duration||0)||null,created_at:now})
      return send(res,200,{ok:true,case_number:caseNumber,received_at:now})
    }

    if(action==='log'||action==='event'||action==='security'||action==='security_event'){
      const logId=id('log')
      await setJson(`rm:log:${logId}`,{id:logId,server_id:String(data.guild_id||data.server_id||''),event_type:action==='security'||action==='security_event'?`security:${String(data.event_type||data.system||'event')}`:String(data.event_type||data.category||action),actor_id:data.actor_id?String(data.actor_id):data.moderator_id?String(data.moderator_id):null,target_id:data.target_id?String(data.target_id):null,details:data,created_at:now})
      return send(res,200,{ok:true,received_at:now})
    }

    if(action==='heartbeat'||action==='stats'||action==='status'){
      await setJson('rm:sync:state',{servers:Number(data.server_count||data.guild_count||data.servers||0),users:Number(data.member_count||data.user_count||data.users||0),commands:Number(data.command_count||data.commands_processed||data.commands||0),uptime:String(data.uptime||data.status||'online'),connected:true,payload:data,updated_at:now})
      return send(res,200,{ok:true,received_at:now})
    }

    if(action==='guilds'){
      const guilds=Array.isArray(data.guilds)?data.guilds:[]
      const users=guilds.reduce((sum,guild)=>sum+Number(guild?.member_count||0),0)
      const previous=await getJson<any>('rm:sync:state')||{}
      await setJson('rm:sync:state',{servers:guilds.length,users,commands:Number(previous.commands||0),uptime:String(previous.uptime||'online'),connected:true,payload:{guilds},updated_at:now})
      return send(res,200,{ok:true,received_at:now})
    }

    if(action==='tasks'){
      const taskIds=await redis.lrange<string>('rm:task_ids',0,49)
      const tasks=[]
      for(const taskId of taskIds){
        const task=await getJson<any>(`rm:task:${taskId}`)
        if(task?.status==='pending'){
          task.status='running';task.claimed_at=now
          await setJson(`rm:task:${taskId}`,task)
          tasks.push(task)
        }
      }
      return send(res,200,{ok:true,tasks,received_at:now})
    }

    if(action==='task_done'){
      const taskId=String(data.id||'')
      if(taskId){
        const task=await getJson<any>(`rm:task:${taskId}`)
        if(task){task.status=data.error?'failed':'completed';task.error=data.error||null;task.completed_at=now;await setJson(`rm:task:${taskId}`,task)}
      }
      return send(res,200,{ok:true,received_at:now})
    }

    return send(res,200,{ok:true,action,received_at:now})
  }catch(error){
    console.error('[bot-sync] handler failure',error)
    return send(res,500,{ok:false,error:'Dashboard bridge failed'})
  }
}
