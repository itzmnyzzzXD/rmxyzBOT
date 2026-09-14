import { json, requireServerOwner, supabase } from '../_auth'

export default async function handler(req:any,res:any){
  if(req.method!=='POST') return json(res,405,{ok:false,error:'Method not allowed'})
  try{
    const body=typeof req.body==='string'?JSON.parse(req.body):req.body||{}
    const action=String(body.action||'')
    const data=body.data&&typeof body.data==='object'?body.data:{}
    const serverId=String(data.server_id||data.guild_id||'')
    if(!serverId) return json(res,400,{ok:false,error:'server_id is required'})
    const gate=await requireServerOwner(req,res,serverId)
    if(!gate) return
    const actor=gate.auth.user.discord_id

    if(action==='set_module'){
      const module=String(data.module||'')
      if(!module) return json(res,400,{ok:false,error:'module is required'})
      const settings=data.settings&&typeof data.settings==='object'?data.settings:{}
      await supabase('server_settings',{method:'POST',body:JSON.stringify({server_id:serverId,module,enabled:data.enabled!==false,settings,updated_at:new Date().toISOString()}),headers:{'Content-Type':'application/json','Prefer':'resolution=merge-duplicates,return=representation'}})
      return json(res,200,{ok:true,saved:true})
    }

    if(action==='set_prefixes'){
      const prefixes=Array.isArray(data.prefixes)?data.prefixes.map(String).filter(Boolean).slice(0,5):['rm!','rm?']
      await supabase('server_settings',{method:'POST',body:JSON.stringify({server_id:serverId,module:'core',enabled:true,settings:{prefixes},updated_at:new Date().toISOString()}),headers:{'Content-Type':'application/json','Prefer':'resolution=merge-duplicates,return=representation'}})
      return json(res,200,{ok:true,saved:true,prefixes})
    }

    if(action==='add_blocked_word'){
      const word=String(data.word||'').trim().toLowerCase()
      if(!word) return json(res,400,{ok:false,error:'word is required'})
      await supabase('blocked_words',{method:'POST',body:JSON.stringify({server_id:serverId,word,severity:String(data.severity||'delete')}),headers:{'Content-Type':'application/json'}})
      return json(res,200,{ok:true,saved:true})
    }

    if(action==='delete_blocked_word'){
      const id=String(data.id||'')
      if(!id) return json(res,400,{ok:false,error:'id is required'})
      await supabase(`blocked_words?id=eq.${encodeURIComponent(id)}&server_id=eq.${encodeURIComponent(serverId)}`,{method:'DELETE'})
      return json(res,200,{ok:true,saved:true})
    }

    const allowedTasks=new Set(['reload_config','lockdown','unlockdown','raidmode_on','raidmode_off','announce','sync_slash','moderation_action'])
    if(!allowedTasks.has(action)) return json(res,400,{ok:false,error:'Unknown dashboard action'})
    const created=await supabase('bot_tasks',{method:'POST',body:JSON.stringify({server_id:serverId,task_type:action,payload:data,created_by:actor,status:'pending'}),headers:{'Content-Type':'application/json'}})
    const task=Array.isArray(created)?created[0]:created
    return json(res,200,{ok:true,queued:true,task_id:task?.id||null})
  }catch(error){
    console.error('[dashboard/action]',error)
    return json(res,500,{ok:false,error:'Dashboard action failed'})
  }
}
