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

function validUsername(value) {
  return /^[a-zA-Z0-9_.-]{3,24}$/.test(value)
}

function validPassword(value) {
  return value.length >= 8 && value.length <= 128
}

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return send(res, 405, { ok: false, error: 'Method not allowed' })
  }

  try {
    let body = req.body
    if (typeof body === 'string') body = JSON.parse(body)
    if (!body || typeof body !== 'object') body = {}

    const verifyToken = String(body.token || '').trim()
    const username = String(body.username || '').trim()
    const password = String(body.password || '')

    if (!verifyToken || !validUsername(username) || !validPassword(password)) {
      return send(res, 400, {
        ok: false,
        error: 'Use a valid username and a password with at least 8 characters',
      })
    }

    const rows = await supabase(
      `dashboard_verify_tokens?token_hash=eq.${encodeURIComponent(hash(verifyToken))}&select=*&limit=1`
    )
    const record = Array.isArray(rows) ? rows[0] : rows

    if (!record || record.used_at || new Date(record.expires_at).getTime() <= Date.now()) {
      return send(res, 400, { ok: false, error: 'This verification link is invalid or expired' })
    }

    const existing = await supabase(
      `dashboard_users?or=(discord_id.eq.${encodeURIComponent(record.discord_id)},username.eq.${encodeURIComponent(username)})&select=id,discord_id,username&limit=5`
    )
    const list = Array.isArray(existing) ? existing : []
    const conflict = list.find((x) => String(x.username).toLowerCase() === username.toLowerCase() && String(x.discord_id) !== String(record.discord_id))
    if (conflict) {
      return send(res, 409, { ok: false, error: 'That dashboard username is already taken' })
    }

    let user = list.find((x) => String(x.discord_id) === String(record.discord_id)) || null

    if (user) {
      const updated = await supabase(`dashboard_users?id=eq.${encodeURIComponent(user.id)}`, {
        method: 'PATCH',
        body: JSON.stringify({
          username,
          password_hash: passwordHash(password),
          discord_username: record.discord_username,
        }),
        headers: { 'Content-Type': 'application/json' },
      })
      user = Array.isArray(updated) ? updated[0] : updated
    } else {
      const created = await supabase('dashboard_users', {
        method: 'POST',
        body: JSON.stringify({
          discord_id: record.discord_id,
          discord_username: record.discord_username,
          username,
          password_hash: passwordHash(password),
        }),
        headers: { 'Content-Type': 'application/json' },
      })
      user = Array.isArray(created) ? created[0] : created
    }

    if (!user?.id) throw new Error('Dashboard user could not be created')

    await supabase('dashboard_memberships', {
      method: 'POST',
      body: JSON.stringify({
        user_id: user.id,
        server_id: record.guild_id,
        role: 'owner',
      }),
      headers: {
        'Content-Type': 'application/json',
        Prefer: 'resolution=merge-duplicates,return=representation',
      },
    })

    await supabase(`dashboard_verify_tokens?id=eq.${encodeURIComponent(record.id)}`, {
      method: 'PATCH',
      body: JSON.stringify({ used_at: new Date().toISOString() }),
      headers: { 'Content-Type': 'application/json' },
    })

    const sessionToken = token(32)
    await supabase('dashboard_sessions', {
      method: 'POST',
      body: JSON.stringify({
        user_id: user.id,
        token_hash: hash(sessionToken),
        expires_at: new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString(),
      }),
      headers: { 'Content-Type': 'application/json' },
    })

    return send(res, 200, {
      ok: true,
      token: sessionToken,
      user: {
        username: user.username,
        discord_username: user.discord_username,
      },
    })
  } catch (error) {
    console.error('[verify/complete]', error)
    return send(res, 500, {
      ok: false,
      error: 'Could not finish account setup',
      detail: process.env.NODE_ENV === 'development' ? String(error?.message || error) : undefined,
    })
  }
}
