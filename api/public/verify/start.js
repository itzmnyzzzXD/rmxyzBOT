import crypto from 'node:crypto'

function send(res, status, body) {
  res.status(status).setHeader('Content-Type', 'application/json; charset=utf-8')
  res.end(JSON.stringify(body))
}

function token(bytes = 32) {
  return crypto.randomBytes(bytes).toString('hex')
}

function hash(value) {
  return crypto.createHash('sha256').update(value).digest('hex')
}

async function supabase(path, init = {}) {
  const url = String(process.env.SUPABASE_URL || '').trim().replace(/\/$/, '')
  const key = String(process.env.SUPABASE_SERVICE_ROLE_KEY || '').trim()
  if (!url || !key) throw new Error('Supabase environment variables are missing')

  const response = await fetch(`${url}/rest/v1/${path}`, {
    ...init,
    headers: {
      apikey: key,
      Authorization: `Bearer ${key}`,
      Prefer: 'return=representation',
      ...(init.headers || {}),
    },
  })

  const text = await response.text()
  if (!response.ok) throw new Error(`Supabase ${response.status}: ${text.slice(0, 500)}`)
  return text ? JSON.parse(text) : []
}

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return send(res, 405, { ok: false, error: 'Method not allowed' })
  }

  const expected = String(process.env.BOT_SYNC_KEY || '').trim()
  const supplied = String(req.headers['x-bot-key'] || '').trim()
  if (!expected || !supplied || supplied !== expected) {
    return send(res, 401, { ok: false, error: 'Invalid sync key' })
  }

  try {
    let body = req.body
    if (typeof body === 'string') body = JSON.parse(body)
    if (!body || typeof body !== 'object') body = {}

    const discordId = String(body.discord_id || '').trim()
    const discordUsername = String(body.discord_username || '').trim()
    const guildId = String(body.guild_id || '').trim()

    if (!discordId || !guildId) {
      return send(res, 400, {
        ok: false,
        error: 'Discord and guild identifiers are required',
      })
    }

    const dashboardUrl = String(process.env.DASHBOARD_URL || '').trim().replace(/\/$/, '')
    if (!dashboardUrl) throw new Error('DASHBOARD_URL is missing')

    const verifyToken = token(32)
    const expiresAt = new Date(Date.now() + 15 * 60 * 1000).toISOString()

    await supabase('dashboard_verify_tokens', {
      method: 'POST',
      body: JSON.stringify({
        token_hash: hash(verifyToken),
        discord_id: discordId,
        discord_username: discordUsername,
        guild_id: guildId,
        expires_at: expiresAt,
      }),
      headers: { 'Content-Type': 'application/json' },
    })

    return send(res, 200, {
      ok: true,
      url: `${dashboardUrl}/verify/${verifyToken}`,
      expires_at: expiresAt,
    })
  } catch (error) {
    console.error('[verify/start]', error)
    return send(res, 500, {
      ok: false,
      error: 'Could not create verification link',
      detail: process.env.NODE_ENV === 'development' ? String(error?.message || error) : undefined,
    })
  }
}
