import crypto from 'node:crypto'

function send(res, status, body) {
  res.status(status).setHeader('Content-Type', 'application/json; charset=utf-8')
  res.end(JSON.stringify(body))
}

function token(bytes = 32) {
  return crypto.randomBytes(bytes).toString('hex')
}

function passwordHash(password, salt = token(16)) {
  const derived = crypto.scryptSync(password, salt, 64).toString('hex')
  return `scrypt:${salt}:${derived}`
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

function generateUsername(discordId) {
  return `rm_${discordId.slice(-12)}`
}

function generatePassword() {
  return crypto.randomBytes(12).toString('base64url')
}

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return send(res, 405, { ok: false, error: 'Method not allowed' })
  }

  const expected = String(process.env.BOT_SYNC_KEY || '').trim()
  const supplied = String(req.headers['x-bot-key'] || '').trim()
  if (!expected || supplied !== expected) {
    return send(res, 401, { ok: false, error: 'Invalid sync key' })
  }

  try {
    let body = req.body
    if (typeof body === 'string') body = JSON.parse(body)
    if (!body || typeof body !== 'object') body = {}

    const discordId = String(body.discord_id || '').trim()
    const discordUsername = String(body.discord_username || '').trim()
    const guildId = String(body.guild_id || '').trim()
    const guildName = String(body.guild_name || '').trim()

    if (!/^\d{15,25}$/.test(discordId) || !/^\d{15,25}$/.test(guildId)) {
      return send(res, 400, { ok: false, error: 'Invalid Discord or server identifier' })
    }

    const existingRows = await supabase(
      `dashboard_users?discord_id=eq.${encodeURIComponent(discordId)}&select=id,username,discord_username&limit=1`
    )
    let user = Array.isArray(existingRows) ? existingRows[0] : existingRows

    const username = user?.username || generateUsername(discordId)
    const password = generatePassword()
    const hashed = passwordHash(password)

    if (user?.id) {
      const updated = await supabase(`dashboard_users?id=eq.${encodeURIComponent(user.id)}`, {
        method: 'PATCH',
        body: JSON.stringify({
          discord_username: discordUsername || user.discord_username,
          password_hash: hashed,
        }),
        headers: { 'Content-Type': 'application/json' },
      })
      user = Array.isArray(updated) ? updated[0] : updated
    } else {
      const created = await supabase('dashboard_users', {
        method: 'POST',
        body: JSON.stringify({
          discord_id: discordId,
          discord_username: discordUsername || discordId,
          username,
          password_hash: hashed,
        }),
        headers: { 'Content-Type': 'application/json' },
      })
      user = Array.isArray(created) ? created[0] : created
    }

    if (!user?.id) throw new Error('Could not create dashboard user')

    await supabase('dashboard_memberships', {
      method: 'POST',
      body: JSON.stringify({ user_id: user.id, server_id: guildId, role: 'owner' }),
      headers: {
        'Content-Type': 'application/json',
        Prefer: 'resolution=merge-duplicates,return=representation',
      },
    })

    // Rotate sessions when credentials are regenerated, then issue a fresh session.
    await supabase(`dashboard_sessions?user_id=eq.${encodeURIComponent(user.id)}`, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
    })

    const sessionToken = token(32)
    const expiresAt = new Date(Date.now() + 180 * 24 * 60 * 60 * 1000).toISOString()
    await supabase('dashboard_sessions', {
      method: 'POST',
      body: JSON.stringify({
        user_id: user.id,
        token_hash: requireHash(sessionToken),
        expires_at: expiresAt,
      }),
      headers: { 'Content-Type': 'application/json' },
    })

    return send(res, 200, {
      ok: true,
      username: user.username || username,
      password,
      expires_at: expiresAt,
      server: { id: guildId, name: guildName || 'Discord server' },
    })
  } catch (error) {
    console.error('[verify/provision]', error)
    return send(res, 500, {
      ok: false,
      error: 'Could not create dashboard credentials',
      detail: process.env.NODE_ENV === 'development' ? String(error?.message || error) : undefined,
    })
  }
}

function requireHash(value) {
  return crypto.createHash('sha256').update(value).digest('hex')
}
