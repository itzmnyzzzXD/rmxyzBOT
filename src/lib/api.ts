import type { BotStats, Server } from '../types'

async function request<T>(url:string,init?:RequestInit):Promise<T|null>{
  try{
    const response=await fetch(url,{...init,headers:{'Content-Type':'application/json',...(init?.headers||{})}})
    const text=await response.text()
    if(!response.ok)return null
    return text?JSON.parse(text) as T:null
  }catch{return null}
}

export async function getBotStats():Promise<BotStats>{
  return (await request<BotStats>('/api/dashboard/stats'))||{servers:0,users:0,commands:0,uptime:'offline',connected:false}
}

export async function getServers():Promise<Server[]>{
  return (await request<Server[]>('/api/dashboard/servers'))||[]
}

export async function syncAction(action:string,data:Record<string,unknown>){
  return request<{ok:boolean;error?:string}>('/api/dashboard/action',{method:'POST',body:JSON.stringify({action,data})})
}
