import type { BotStats, Server } from '../types'

const demoStats: BotStats = { servers: 142, users: 218400, commands: 642, uptime: '99.98%', connected: true }
const demoServers: Server[] = [
  { id:'100001', name:'RM Community', icon:'RM', members:12480, online:true, manageable:true },
  { id:'100002', name:'Night Shift', icon:'NS', members:5810, online:true, manageable:true },
  { id:'100003', name:'Gaming Hub', icon:'GH', members:2220, online:false, manageable:false },
]

async function request<T>(url: string, init?: RequestInit): Promise<T | null> {
  try {
    const response = await fetch(url, { ...init, headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) } })
    if (!response.ok) return null
    return await response.json() as T
  } catch {
    return null
  }
}

export async function getBotStats(): Promise<BotStats> {
  return (await request<BotStats>('/api/dashboard/stats')) || demoStats
}

export async function getServers(): Promise<Server[]> {
  return (await request<Server[]>('/api/dashboard/servers')) || demoServers
}

export async function syncAction(action: string, data: Record<string, unknown>) {
  return request<{ ok: boolean }>('/api/dashboard/action', { method:'POST', body: JSON.stringify({ action, data }) })
}
