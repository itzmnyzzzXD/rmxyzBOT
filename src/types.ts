export type Server = { id: string; name: string; icon: string; members: number; online: boolean; manageable: boolean }
export type BotStats = { servers: number; users: number; commands: number; uptime: string; connected: boolean; updatedAt?: string }
export type Activity = { id: string; title: string; meta: string; time: string; tone: 'purple'|'green'|'amber'|'red' }
export type ModuleKey =
  | 'overview'|'moderation'|'automod'|'anti-raid'|'anti-nuke'|'security'|'warnings'|'logs'|'tickets'|'welcome'
  | 'reaction-roles'|'auto-responders'|'custom-commands'|'economy'|'levels'|'roleplay'|'server-config'|'commands'
  | 'permissions'|'whitelist'|'trust'|'bot-settings'

export type CommandItem = { name: string; description: string; usage: string; permissions: string; cooldown: string; category: string }
