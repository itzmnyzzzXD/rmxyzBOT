function send(res, status, body) {
  res.status(status).setHeader('Content-Type', 'application/json; charset=utf-8');
  return res.end(JSON.stringify(body));
}

function env(name) {
  return String(process.env[name] || '').trim();
}

async function supabase(path, init = {}) {
  const base = env('SUPABASE_URL').replace(/\/$/, '');
  const key = env('SUPABASE_SERVICE_ROLE_KEY');
  if (!base || !key) return null;

  try {
    const response = await fetch(`${base}/rest/v1/${path}`, {
      ...init,
      headers: {
        apikey: key,
        Authorization: `Bearer ${key}`,
        Accept: 'application/json',
        ...(init.headers || {}),
      },
    });

    const text = await response.text();
    if (!response.ok) {
      console.error('[supabase]', response.status, path, text.slice(0, 500));
      return null;
    }
    if (!text) return null;
    try { return JSON.parse(text); } catch { return null; }
  } catch (error) {
    console.error('[supabase] request failed', error);
    return null;
  }
}

function getBody(req) {
  if (!req || req.body == null) return {};
  if (typeof req.body === 'object') return req.body;
  try { return JSON.parse(String(req.body)); } catch { return {}; }
}

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST');
    return send(res, 405, { ok: false, error: 'Method not allowed' });
  }

  const expected = (env('BOT_SYNC_KEY') || env('bot_sync_key')).trim();
  const raw = req.headers['x-bot-key'];
  const supplied = String(Array.isArray(raw) ? raw[0] || '' : raw || '').trim();

  if (!expected || !supplied || supplied !== expected) {
    return send(res, 401, { ok: false, error: 'Invalid sync key' });
  }

  try {
    const body = getBody(req);
    const action = String(body.action || 'heartbeat');
    const data = body.data && typeof body.data === 'object' ? body.data : {};
    const now = new Date().toISOString();

    if (action === 'config') {
      const guildId = String(data.guild_id || '');
      if (!guildId) return send(res, 200, { ok: true, config: [], words: [], received_at: now });

      const config = await supabase(`server_settings?server_id=eq.${encodeURIComponent(guildId)}&select=module,enabled,settings`);
      const words = await supabase(`blocked_words?server_id=eq.${encodeURIComponent(guildId)}&select=id,word,severity`);
      return send(res, 200, { ok: true, config: config || [], words: words || [], received_at: now });
    }

    if (action === 'case') {
      const created = await supabase('moderation_cases', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Prefer: 'return=representation' },
        body: JSON.stringify({
          server_id: String(data.guild_id || ''),
          action_type: String(data.action_type || 'unknown'),
          target_id: String(data.target_id || ''),
          target_tag: String(data.target_tag || ''),
          moderator_id: String(data.moderator_id || ''),
          reason: data.reason || null,
          duration_seconds: Number(data.duration_seconds || data.duration || 0) || null,
          created_at: now,
        }),
      });
      const row = Array.isArray(created) ? created[0] : created;
      return send(res, 200, { ok: true, case_number: row?.id || null, received_at: now });
    }

    if (action === 'log' || action === 'event' || action === 'security' || action === 'security_event') {
      await supabase('audit_logs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Prefer: 'return=minimal' },
        body: JSON.stringify({
          server_id: String(data.guild_id || data.server_id || ''),
          event_type: action === 'security' || action === 'security_event'
            ? `security:${String(data.event_type || data.system || 'event')}`
            : String(data.event_type || data.category || action),
          actor_id: data.actor_id ? String(data.actor_id) : data.moderator_id ? String(data.moderator_id) : null,
          target_id: data.target_id ? String(data.target_id) : null,
          details: data,
          created_at: now,
        }),
      });
      return send(res, 200, { ok: true, received_at: now });
    }

    if (action === 'heartbeat' || action === 'stats' || action === 'status') {
      const row = {
        id: 'global',
        servers: Number(data.server_count || data.guild_count || data.servers || 0),
        users: Number(data.member_count || data.user_count || data.users || 0),
        commands: Number(data.command_count || data.commands_processed || data.commands || 0),
        uptime: String(data.uptime || data.status || 'online'),
        connected: true,
        payload: data,
        updated_at: now,
      };
      await supabase('bot_sync_state', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Prefer: 'resolution=merge-duplicates,return=minimal',
        },
        body: JSON.stringify(row),
      });
      return send(res, 200, { ok: true, received_at: now });
    }

    if (action === 'guilds') {
      const guilds = Array.isArray(data.guilds) ? data.guilds : [];
      const users = guilds.reduce((sum, guild) => sum + Number(guild?.member_count || 0), 0);
      await supabase('bot_sync_state', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Prefer: 'resolution=merge-duplicates,return=minimal',
        },
        body: JSON.stringify({
          id: 'global',
          servers: guilds.length,
          users,
          connected: true,
          payload: { guilds },
          updated_at: now,
        }),
      });
      return send(res, 200, { ok: true, received_at: now });
    }

    if (action === 'tasks') {
      const tasks = await supabase('bot_tasks?status=eq.pending&order=created_at.asc&limit=25');
      const pending = Array.isArray(tasks) ? tasks : [];
      for (const task of pending) {
        if (!task?.id) continue;
        await supabase(`bot_tasks?id=eq.${encodeURIComponent(String(task.id))}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json', Prefer: 'return=minimal' },
          body: JSON.stringify({ status: 'running', claimed_at: now }),
        });
      }
      return send(res, 200, { ok: true, tasks: pending, received_at: now });
    }

    if (action === 'task_done') {
      const id = String(data.id || '');
      if (id) {
        await supabase(`bot_tasks?id=eq.${encodeURIComponent(id)}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json', Prefer: 'return=minimal' },
          body: JSON.stringify({
            status: data.error ? 'failed' : 'completed',
            error: data.error || null,
            completed_at: now,
          }),
        });
      }
      return send(res, 200, { ok: true, received_at: now });
    }

    // Unknown actions are intentionally acknowledged so new bot features do not
    // break the bridge while the dashboard/backend evolves.
    return send(res, 200, { ok: true, action, received_at: now });
  } catch (error) {
    console.error('[bot-sync] handler failure', error);
    return send(res, 200, {
      ok: true,
      degraded: true,
      error: 'Dashboard bridge temporarily degraded',
      received_at: new Date().toISOString(),
    });
  }
}
