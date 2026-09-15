import { Redis } from '@upstash/redis'

function env(...names:string[]){
  for(const name of names){
    const value=String(process.env[name]||'').trim()
    if(value) return value
  }
  return ''
}

const url=env('UPSTASH_REDIS_REST_URL','KV_REST_API_URL','REDIS_REST_URL')
const token=env('UPSTASH_REDIS_REST_TOKEN','KV_REST_API_TOKEN','REDIS_REST_TOKEN')

if(!url||!token) throw new Error('Redis environment variables are missing. Connect Redis to the Vercel project first.')

export const redis=new Redis({url,token})

export async function getJson<T=any>(key:string):Promise<T|null>{
  return await redis.get<T>(key)
}

export async function setJson(key:string,value:any,ttlSeconds?:number){
  if(ttlSeconds) return await redis.set(key,value,{ex:ttlSeconds})
  return await redis.set(key,value)
}

export async function del(key:string){ return await redis.del(key) }

export async function pushJson(key:string,value:any){
  await redis.lpush(key,value)
  return value
}

export async function listJson<T=any>(key:string,start=0,end=-1):Promise<T[]>{
  return await redis.lrange<T>(key,start,end)
}

export async function removeJson(key:string,value:any){ return await redis.lrem(key,0,value) }

export function id(prefix:string='id'){ return `${prefix}_${cryptoRandom(12)}` }
function cryptoRandom(bytes:number){
  const chars='0123456789abcdef'
  let out=''
  for(let i=0;i<bytes*2;i++) out+=chars[Math.floor(Math.random()*chars.length)]
  return out
}
