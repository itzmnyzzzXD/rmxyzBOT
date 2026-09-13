# RM Control Center

RM is a Discord moderation + security bot with a Vercel-ready React dashboard. `bot.py` is the existing bot backend and is intentionally kept separate from the web application.

## Stack

- React + TypeScript + Vite
- Lucide React
- Vercel serverless API routes
- Optional Supabase PostgreSQL backend
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

Set `DISCORD_BOT_TOKEN`, `BOT_SYNC_KEY`, and `DASHBOARD_URL`. The existing bot already posts to `/api/public/bot/sync` with `x-bot-key`; do not rewrite the bot to make the dashboard work.

## Vercel

Import this repository into Vercel. The included `vercel.json` uses `npm run build`, serves `dist`, and rewrites non-API routes to the Vite SPA entrypoint.

Set these Vercel environment variables:

```text
BOT_SYNC_KEY
DISCORD_CLIENT_ID
DISCORD_CLIENT_SECRET
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
SESSION_SECRET
DASHBOARD_URL
```

Never place bot tokens, `BOT_SYNC_KEY`, `SESSION_SECRET`, or the Supabase service-role key in frontend code.

## Supabase

Run `supabase/schema.sql` in the Supabase SQL editor. The API uses server-side REST requests and the service role key only inside Vercel functions. The browser never receives the service-role key.

The schema covers bot sync state, server settings, blocked words, moderation cases, audit logs, warnings, tickets, custom commands, autoresponses and trust entries. More modules can use the same server-scoped pattern.

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

Supported adapters include `config`, `case`, `event`, `security_event`, `stats`, `heartbeat`, and `status`. Unknown actions return an acknowledged response so the bot can evolve without requiring a dashboard rewrite.

## Authentication

The UI includes the server-selector architecture and a fail-closed mutation boundary. Discord OAuth/session middleware should be connected before enabling real admin mutations. Never trust the frontend for permission decisions.

## Demo/live behavior

The dashboard has clearly labeled fallback values so it stays useful while credentials are missing. Live bot values replace them automatically when the backend can read `bot_sync_state`.

## Routes

Public: `/`, `/commands`, `/features`, `/security`, `/pricing`, `/docs`, `/status`, `/login`

Dashboard: `/dashboard`, `/dashboard/moderation`, `/dashboard/automod`, `/dashboard/anti-raid`, `/dashboard/anti-nuke`, `/dashboard/security`, `/dashboard/warnings`, `/dashboard/logs`, `/dashboard/tickets`, `/dashboard/welcome`, `/dashboard/reaction-roles`, `/dashboard/auto-responders`, `/dashboard/custom-commands`, `/dashboard/economy`, `/dashboard/levels`, `/dashboard/roleplay`, `/dashboard/server-config`, `/dashboard/commands`, `/dashboard/permissions`, `/dashboard/whitelist`, `/dashboard/trust`, `/dashboard/bot-settings`

## Safety boundary

The dashboard mutation API deliberately returns `401` until proper Discord OAuth and session validation are wired in. This prevents a public Vercel deployment from turning demo controls into unauthenticated moderation actions.
