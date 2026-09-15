# RM Control Center

RM is a Discord moderation + security bot with a Vercel-ready React dashboard. `bot.py` is the existing bot backend and is intentionally kept separate from the web application.

## Stack

- React + TypeScript + Vite
- Lucide React
- Vercel serverless API routes
- Upstash Redis for persistent dashboard data, sessions, settings, tasks and bot sync state
- Python `discord.py` bot

## Run the dashboard

```bash
npm install
npm run dev
npm run build
npm run preview
```

The production build is emitted to `dist/`.

## Run the bot

```bash
python3 -m pip install -r requirements.txt
cp .env.example .env
python3 bot.py
```

Set `DISCORD_BOT_TOKEN`, `BOT_SYNC_KEY`, and `DASHBOARD_URL`. The existing bot posts to `/api/public/bot/sync` with `x-bot-key`.

## Vercel

Import this repository into Vercel. The included `vercel.json` uses `npm run build`, serves `dist`, and rewrites non-API routes to the Vite SPA entrypoint.

Connect an Upstash Redis database to the Vercel project and provide these environment variables:

```text
BOT_SYNC_KEY
DISCORD_CLIENT_ID
DISCORD_CLIENT_SECRET
UPSTASH_REDIS_REST_URL
UPSTASH_REDIS_REST_TOKEN
DASHBOARD_URL
```

The Redis credentials are server-side only. Never put them in frontend code.

## Redis storage

The dashboard uses Redis instead of Supabase. User accounts, 180-day sessions, server configuration, blocked words, bot sync state, moderation cases, audit events and dashboard tasks are stored under the `rm:` key namespace.

No SQL setup is required.

## Bot sync contract

`POST /api/public/bot/sync`

Headers:

```text
x-bot-key: <BOT_SYNC_KEY>
Content-Type: application/json
```

Body:

```json
{ "action": "heartbeat", "data": { "server_count": 142, "member_count": 218400 } }
```

Supported adapters include `config`, `case`, `event`, `security_event`, `stats`, `heartbeat`, `status`, `guilds`, `tasks` and `task_done`.

## Authentication

Dashboard login uses username/password and server-side Redis sessions. The test administrator account is `admin` / `admin` as requested. Normal dashboard accounts are provisioned through the Discord `/verify` or `rm!verify` flow.

## Routes

Public: `/`, `/commands`, `/features`, `/security`, `/pricing`, `/docs`, `/status`, `/login`

Dashboard: `/dashboard`, `/dashboard/moderation`, `/dashboard/automod`, `/dashboard/anti-raid`, `/dashboard/anti-nuke`, `/dashboard/security`, `/dashboard/warnings`, `/dashboard/logs`, `/dashboard/tickets`, `/dashboard/welcome`, `/dashboard/reaction-roles`, `/dashboard/auto-responders`, `/dashboard/custom-commands`, `/dashboard/economy`, `/dashboard/levels`, `/dashboard/roleplay`, `/dashboard/server-config`, `/dashboard/commands`, `/dashboard/permissions`, `/dashboard/whitelist`, `/dashboard/trust`, `/dashboard/bot-settings`

## Safety boundary

Dashboard permissions are enforced server-side. Admin sessions can view all synced Discord servers; normal users are limited to their owned servers.
