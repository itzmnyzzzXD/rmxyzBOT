import os
import re
import time
import sqlite3
import asyncio
import io
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN', '').strip()
PREFIX = '-'
DB_PATH = os.getenv('DB_PATH', 'bot.db')
OWNER_ID = int(os.getenv('OWNER_ID', '0') or 0)

if not TOKEN:
    raise RuntimeError('DISCORD_TOKEN is missing. Put it in .env')

INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.members = True
INTENTS.reactions = True
INTENTS.guilds = True

bot = commands.Bot(command_prefix=PREFIX, intents=INTENTS, help_command=None)

# -----------------------------
# Database
# -----------------------------

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS guild_config (
        guild_id INTEGER PRIMARY KEY,
        log_channel INTEGER,
        modlog_channel INTEGER,
        anti_links INTEGER DEFAULT 1,
        anti_slurs INTEGER DEFAULT 1,
        anti_spam INTEGER DEFAULT 1,
        anti_caps INTEGER DEFAULT 0,
        anti_mentions INTEGER DEFAULT 1,
        max_mentions INTEGER DEFAULT 5,
        spam_messages INTEGER DEFAULT 6,
        spam_window INTEGER DEFAULT 8,
        punishment TEXT DEFAULT 'timeout',
        timeout_seconds INTEGER DEFAULT 600,
        warn_threshold INTEGER DEFAULT 3,
        auto_mod_role INTEGER,
        muted_role INTEGER
    );
    CREATE TABLE IF NOT EXISTS warnings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        moderator_id INTEGER NOT NULL,
        reason TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS cases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        moderator_id INTEGER NOT NULL,
        action TEXT NOT NULL,
        reason TEXT,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS reaction_roles (
        guild_id INTEGER NOT NULL,
        channel_id INTEGER NOT NULL,
        message_id INTEGER PRIMARY KEY,
        role_id INTEGER NOT NULL,
        emoji TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS bad_words (
        guild_id INTEGER NOT NULL,
        word TEXT NOT NULL,
        PRIMARY KEY(guild_id, word)
    );
    CREATE TABLE IF NOT EXISTS whitelist_links (
        guild_id INTEGER NOT NULL,
        domain TEXT NOT NULL,
        PRIMARY KEY(guild_id, domain)
    );
    ''')
    conn.commit()
    conn.close()


def ensure_guild(guild_id: int):
    conn = db()
    conn.execute('INSERT OR IGNORE INTO guild_config (guild_id) VALUES (?)', (guild_id,))
    conn.commit()
    conn.close()


def get_config(guild_id: int):
    ensure_guild(guild_id)
    conn = db()
    row = conn.execute('SELECT * FROM guild_config WHERE guild_id=?', (guild_id,)).fetchone()
    conn.close()
    return row


def set_config(guild_id: int, key: str, value):
    allowed = {
        'log_channel','modlog_channel','anti_links','anti_slurs','anti_spam','anti_caps',
        'anti_mentions','max_mentions','spam_messages','spam_window','punishment',
        'timeout_seconds','warn_threshold','auto_mod_role','muted_role'
    }
    if key not in allowed:
        raise ValueError('invalid config key')
    conn = db()
    conn.execute(f'UPDATE guild_config SET {key}=? WHERE guild_id=?', (value, guild_id))
    conn.commit()
    conn.close()


def add_case(guild_id, user_id, moderator_id, action, reason=''):
    conn = db()
    cur = conn.execute(
        'INSERT INTO cases (guild_id,user_id,moderator_id,action,reason,created_at) VALUES (?,?,?,?,?,?)',
        (guild_id, user_id, moderator_id, action, reason, datetime.now(timezone.utc).isoformat())
    )
    conn.commit()
    case_id = cur.lastrowid
    conn.close()
    return case_id


def add_warning(guild_id, user_id, moderator_id, reason):
    conn = db()
    cur = conn.execute(
        'INSERT INTO warnings (guild_id,user_id,moderator_id,reason,created_at) VALUES (?,?,?,?,?)',
        (guild_id, user_id, moderator_id, reason, datetime.now(timezone.utc).isoformat())
    )
    conn.commit()
    warning_id = cur.lastrowid
    conn.close()
    return warning_id


def warning_count(guild_id, user_id):
    conn = db()
    n = conn.execute('SELECT COUNT(*) AS n FROM warnings WHERE guild_id=? AND user_id=?', (guild_id, user_id)).fetchone()['n']
    conn.close()
    return n

# -----------------------------
# Utilities
# -----------------------------

URL_RE = re.compile(r'(?:https?://|www\.)\S+', re.I)
DISCORD_INVITE_RE = re.compile(r'(?:discord(?:\.gg|\.com/invite)/)[\w-]+', re.I)
LINK_DOMAIN_RE = re.compile(r'(?:https?://|www\.)(?:[^/]+)', re.I)

message_buckets = defaultdict(lambda: deque())
duplicate_buckets = defaultdict(lambda: deque())
raid_join_buckets = defaultdict(lambda: deque())
chatted_sessions = {}

DEFAULT_BAD_WORDS = {
    'examplebadword',
}

EXEMPT_PERMISSIONS = discord.Permissions(manage_messages=True, administrator=True, manage_guild=True)


def normalize(text: str) -> str:
    text = text.casefold()
    text = re.sub(r'[^\w\s]', '', text, flags=re.UNICODE)
    text = re.sub(r'(.)\1{3,}', r'\1\1', text)
    return text


def member_is_staff(member: discord.Member) -> bool:
    return member.guild_permissions.administrator or member.guild_permissions.manage_guild or member.guild_permissions.manage_messages


def is_protected(member: discord.Member) -> bool:
    return member_is_staff(member) or member.id == member.guild.owner_id


def has_url(text: str) -> bool:
    return bool(URL_RE.search(text))


def extract_domain(url: str) -> str:
    m = LINK_DOMAIN_RE.search(url)
    if not m:
        return ''
    host = m.group(0).lower().replace('https://', '').replace('http://', '').replace('www.', '')
    return host.split('/')[0].split(':')[0]


def get_bad_words(guild_id: int):
    conn = db()
    rows = conn.execute('SELECT word FROM bad_words WHERE guild_id=?', (guild_id,)).fetchall()
    conn.close()
    return {r['word'].casefold() for r in rows} | DEFAULT_BAD_WORDS


def get_whitelist(guild_id: int):
    conn = db()
    rows = conn.execute('SELECT domain FROM whitelist_links WHERE guild_id=?', (guild_id,)).fetchall()
    conn.close()
    return {r['domain'].casefold() for r in rows}


def set_status(text: str):
    return discord.Activity(type=discord.ActivityType.watching, name=text)


async def safe_delete(message):
    try:
        await message.delete()
        return True
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        return False


async def log_event(guild: discord.Guild, *, title: str, description: str, color=discord.Color.blurple(), channel_key='log_channel'):
    cfg = get_config(guild.id)
    channel_id = cfg[channel_key]
    if not channel_id:
        return
    channel = guild.get_channel(channel_id)
    if not channel:
        return
    embed = discord.Embed(title=title, description=description, color=color, timestamp=datetime.now(timezone.utc))
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        pass


async def punish(message: discord.Message, reason: str, *, action=None):
    if is_protected(message.author):
        return False
    cfg = get_config(message.guild.id)
    action = action or cfg['punishment']
    await safe_delete(message)
    try:
        if action == 'warn':
            warning = add_warning(message.guild.id, message.author.id, bot.user.id, reason)
            await message.channel.send(f'{message.author.mention} warning issued. #{warning}', delete_after=6)
        elif action == 'kick':
            if message.author.top_role < message.guild.me.top_role:
                await message.author.kick(reason=reason)
        elif action == 'ban':
            if message.author.top_role < message.guild.me.top_role:
                await message.author.ban(reason=reason, delete_message_seconds=86400)
        else:
            if message.author.top_role < message.guild.me.top_role:
                await message.author.timeout(timedelta(seconds=cfg['timeout_seconds']), reason=reason)
    except discord.HTTPException:
        pass
    case_id = add_case(message.guild.id, message.author.id, bot.user.id, action, reason)
    await log_event(
        message.guild,
        title=f'AutoMod • {action.title()}',
        description=f'**User:** {message.author.mention} (`{message.author.id}`)\n**Reason:** {reason}\n**Case:** `#{case_id}`',
        color=discord.Color.red()
    )
    return True

# -----------------------------
# Views / Reaction Roles
# -----------------------------

class ReactionRoleView(discord.ui.View):
    def __init__(self, role_id: int):
        super().__init__(timeout=None)
        self.role_id = role_id

    @discord.ui.button(label='Get Role', style=discord.ButtonStyle.primary, custom_id='rr:get')
    async def get_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        role = interaction.guild.get_role(self.role_id)
        if not role:
            return await interaction.response.send_message('That role no longer exists.', ephemeral=True)
        if role >= interaction.guild.me.top_role:
            return await interaction.response.send_message('I cannot manage that role. Move my role above it.', ephemeral=True)
        try:
            if role in interaction.user.roles:
                await interaction.user.remove_roles(role, reason='Reaction role toggle')
                msg = f'Removed **{role.name}**.'
            else:
                await interaction.user.add_roles(role, reason='Reaction role toggle')
                msg = f'Added **{role.name}**.'
            await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            await interaction.response.send_message('I could not update that role.', ephemeral=True)

# -----------------------------
# Chatted relay
# -----------------------------

async def is_bot_owner(user: discord.User) -> bool:
    if OWNER_ID and user.id == OWNER_ID:
        return True
    try:
        return await bot.is_owner(user)
    except Exception:
        return False


def resolve_text_channel(guild: discord.Guild, raw: str):
    raw = raw.strip()
    match = re.fullmatch(r'<#(\d+)>', raw)
    if match:
        return guild.get_channel(int(match.group(1)))
    if raw.isdigit():
        return guild.get_channel(int(raw))
    name = raw.lstrip('#').strip().casefold()
    matches = [channel for channel in guild.text_channels if channel.name.casefold() == name]
    return matches[0] if len(matches) == 1 else None


@bot.command()
async def chatted(ctx):
    if ctx.guild is None:
        return await ctx.reply('Use `-chatted` inside a server.')

    authorized = ctx.author.id == ctx.guild.owner_id or await is_bot_owner(ctx.author)
    if not authorized:
        return await ctx.reply('Only the server owner or bot owner can use `-chatted`.')

    if ctx.author.id in chatted_sessions:
        return await ctx.reply('You already have a `-chatted` session running.')

    try:
        await ctx.author.send(
            embed=discord.Embed(
                title='Chatted',
                description=(
                    f'Pick a channel from **{ctx.guild.name}** by sending its mention, ID, or exact name.\n\n'
                    '**Examples:** `#general`, `<#123456789>`, `123456789`\n\n'
                    'Type `CANCEL` to stop.'
                ),
                color=discord.Color.blurple()
            )
        )
    except discord.Forbidden:
        return await ctx.reply('I cannot DM you. Enable DMs for this server and try again.')

    def dm_check(message: discord.Message):
        return message.author.id == ctx.author.id and message.guild is None

    try:
        selection = await bot.wait_for('message', check=dm_check, timeout=120)
        selected_text = selection.content.strip()
        if selected_text.casefold() in {'cancel', 'done'}:
            return await ctx.author.send('Chatted setup cancelled.')

        target = resolve_text_channel(ctx.guild, selected_text)
        if not isinstance(target, discord.TextChannel):
            return await ctx.author.send('I could not find that text channel. Send the channel mention, ID, or exact name.')

        me = ctx.guild.me
        if me is None:
            return await ctx.author.send('I could not verify my permissions in that server.')
        permissions = target.permissions_for(me)
        if not permissions.view_channel or not permissions.send_messages:
            return await ctx.author.send(f'I cannot send messages in {target.mention}. Give me **View Channel** and **Send Messages** there.')

        chatted_sessions[ctx.author.id] = {
            'guild_id': ctx.guild.id,
            'channel_id': target.id,
        }

        await ctx.author.send(
            embed=discord.Embed(
                title='Chatted is live',
                description=(
                    f'Messages you send here will be sent to {target.mention}.\n\n'
                    'Send as many messages/images/files as you want.\n'
                    'Type **DONE** to finish or **CANCEL** to cancel.'
                ),
                color=discord.Color.green()
            )
        )

        while True:
            message = await bot.wait_for('message', check=dm_check, timeout=900)
            content = message.content.strip()

            if content.casefold() == 'done':
                await ctx.author.send('Chatted session ended.')
                break
            if content.casefold() == 'cancel':
                await ctx.author.send('Chatted session cancelled.')
                break

            files = []
            for attachment in message.attachments:
                try:
                    data = await attachment.read()
                    files.append(discord.File(io.BytesIO(data), filename=attachment.filename))
                except (discord.HTTPException, OSError):
                    await ctx.author.send(f'I could not read `{attachment.filename}`.')

            if not content and not files:
                await ctx.author.send('That message was empty. Send text, an image, or a file.')
                continue

            try:
                await target.send(content=message.content or None, files=files)
                try:
                    await message.add_reaction('✅')
                except discord.HTTPException:
                    pass
            except discord.Forbidden:
                await ctx.author.send('I lost permission to send messages in the target channel. Session ended.')
                break
            except discord.HTTPException as error:
                await ctx.author.send(f'I could not send that message: `{error}`')

    except asyncio.TimeoutError:
        try:
            await ctx.author.send('Chatted session timed out after 15 minutes of inactivity.')
        except discord.HTTPException:
            pass
    finally:
        chatted_sessions.pop(ctx.author.id, None)

# -----------------------------
# Bot lifecycle
# -----------------------------

@bot.event
async def setup_hook():
    init_db()
    for guild in bot.guilds:
        ensure_guild(guild.id)
    conn = db()
    rows = conn.execute('SELECT role_id FROM reaction_roles').fetchall()
    conn.close()
    for row in rows:
        bot.add_view(ReactionRoleView(row['role_id']))


@bot.event
async def on_ready():
    await bot.change_presence(status=discord.Status.online, activity=set_status(f'{PREFIX}help • your server'))
    print(f'Logged in as {bot.user} ({bot.user.id})')
    print(f'Connected to {len(bot.guilds)} server(s)')


@bot.event
async def on_guild_join(guild):
    ensure_guild(guild.id)
    await log_event(guild, title='Bot Enabled', description=f'Joined **{guild.name}**.', color=discord.Color.green())


@bot.event
async def on_member_join(member: discord.Member):
    if member.bot:
        return
    key = member.guild.id
    now = time.monotonic()
    bucket = raid_join_buckets[key]
    bucket.append(now)
    while bucket and now - bucket[0] > 10:
        bucket.popleft()
    if len(bucket) >= 8:
        await log_event(member.guild, title='Possible Raid', description=f'**{len(bucket)}** members joined in ~10 seconds.', color=discord.Color.red())


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.user_id == bot.user.id:
        return
    conn = db()
    row = conn.execute('SELECT role_id, emoji FROM reaction_roles WHERE message_id=?', (payload.message_id,)).fetchone()
    conn.close()
    if not row:
        return
    if str(payload.emoji) != row['emoji']:
        return
    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return
    role = guild.get_role(row['role_id'])
    member = guild.get_member(payload.user_id)
    if role and member:
        try:
            await member.add_roles(role, reason='Reaction role')
        except discord.HTTPException:
            pass


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    conn = db()
    row = conn.execute('SELECT role_id, emoji FROM reaction_roles WHERE message_id=?', (payload.message_id,)).fetchone()
    conn.close()
    if not row or str(payload.emoji) != row['emoji']:
        return
    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return
    role = guild.get_role(row['role_id'])
    member = guild.get_member(payload.user_id)
    if role and member:
        try:
            await member.remove_roles(role, reason='Reaction role')
        except discord.HTTPException:
            pass

# -----------------------------
# AutoMod
# -----------------------------

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    ensure_guild(message.guild.id)
    cfg = get_config(message.guild.id)
    member = message.author

    if is_protected(member):
        await bot.process_commands(message)
        return

    text = message.content
    normalized = normalize(text)

    if cfg['anti_links']:
        for url in URL_RE.findall(text):
            if DISCORD_INVITE_RE.search(url):
                await punish(message, 'Discord invite link')
                return

    if cfg['anti_slurs']:
        for word in get_bad_words(message.guild.id):
            if word and re.search(rf'(?<!\w){re.escape(word)}(?!\w)', normalized):
                await punish(message, 'Blocked word / slur filter')
                return

    if cfg['anti_mentions']:
        mentions = len(message.mentions) + len(message.role_mentions)
        if message.mention_everyone:
            mentions += 10
        if mentions >= cfg['max_mentions']:
            await punish(message, f'Mention spam ({mentions} mentions)')
            return

    if cfg['anti_caps'] and len(text) >= 12:
        letters = [c for c in text if c.isalpha()]
        if letters and sum(c.isupper() for c in letters) / len(letters) >= 0.85:
            await punish(message, 'Excessive caps')
            return

    if cfg['anti_spam']:
        key = (message.guild.id, member.id)
        now = time.monotonic()
        bucket = message_buckets[key]
        bucket.append(now)
        while bucket and now - bucket[0] > cfg['spam_window']:
            bucket.popleft()
        if len(bucket) >= cfg['spam_messages']:
            bucket.clear()
            await punish(message, f'Spam ({cfg["spam_messages"]} messages in {cfg["spam_window"]}s)')
            return

        dup = duplicate_buckets[key]
        dup.append((now, normalized[:300]))
        while dup and now - dup[0][0] > 12:
            dup.popleft()
        recent_same = sum(1 for _, value in dup if value and value == normalized[:300])
        if normalized and len(normalized) >= 4 and recent_same >= 3:
            dup.clear()
            await punish(message, 'Repeated message spam')
            return

    await bot.process_commands(message)

# -----------------------------
# Commands
# -----------------------------

@bot.command()
async def ping(ctx):
    await ctx.send(f'🏓 `{round(bot.latency * 1000)}ms`')


@bot.command(name='help')
async def help_cmd(ctx):
    embed = discord.Embed(title='Moderation Bot', description=f'Prefix: `{PREFIX}`', color=discord.Color.blurple())
    embed.add_field(name='Moderation', value='`-warn` `-warnings` `-clearwarns` `-timeout` `-kick` `-ban` `-unban` `-purge` `-lock` `-unlock` `-slowmode`', inline=False)
    embed.add_field(name='Server', value='`-setup` `-config` `-setlog` `-setmodlog` `-reactionrole` `-filter` `-whitelist`', inline=False)
    embed.add_field(name='Info', value='`-ping` `-userinfo` `-serverinfo` `-case`', inline=False)
    embed.add_field(name='Owner', value='`-chatted` — server owner or bot owner only', inline=False)
    await ctx.send(embed=embed)


def mod_only():
    async def predicate(ctx):
        return ctx.author.guild_permissions.manage_messages or ctx.author.guild_permissions.manage_guild or ctx.author.guild_permissions.administrator
    return commands.check(predicate)


def admin_only():
    async def predicate(ctx):
        return ctx.author.guild_permissions.manage_guild or ctx.author.guild_permissions.administrator
    return commands.check(predicate)


@bot.command()
@mod_only()
async def warn(ctx, member: discord.Member, *, reason='No reason provided'):
    warning_id = add_warning(ctx.guild.id, member.id, ctx.author.id, reason)
    count = warning_count(ctx.guild.id, member.id)
    cfg = get_config(ctx.guild.id)
    action = None
    if count >= cfg['warn_threshold'] and member.top_role < ctx.guild.me.top_role:
        action = 'timeout'
        try:
            await member.timeout(timedelta(seconds=cfg['timeout_seconds']), reason=f'Warning threshold reached: {reason}')
        except discord.HTTPException:
            pass
    case_id = add_case(ctx.guild.id, member.id, ctx.author.id, 'warn', reason)
    await ctx.send(f'⚠️ {member.mention} warned. Warning `#{warning_id}` • total `{count}`' + (f' • auto-timeout `{cfg["timeout_seconds"]}s`' if action else ''))
    await log_event(ctx.guild, title='Warning', description=f'**User:** {member.mention}\n**Moderator:** {ctx.author.mention}\n**Reason:** {reason}\n**Case:** `#{case_id}`', color=discord.Color.orange(), channel_key='modlog_channel')


@bot.command()
@mod_only()
async def warnings(ctx, member: discord.Member):
    conn = db()
    rows = conn.execute('SELECT * FROM warnings WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 10', (ctx.guild.id, member.id)).fetchall()
    conn.close()
    if not rows:
        return await ctx.send(f'{member.mention} has no warnings.')
    lines = [f'`#{r["id"]}` • <t:{int(datetime.fromisoformat(r["created_at"]).timestamp())}:R> • {r["reason"]}' for r in rows]
    embed = discord.Embed(title=f'Warnings • {member}', description='\n'.join(lines), color=discord.Color.orange())
    await ctx.send(embed=embed)


@bot.command()
@mod_only()
async def clearwarns(ctx, member: discord.Member):
    conn = db(); conn.execute('DELETE FROM warnings WHERE guild_id=? AND user_id=?', (ctx.guild.id, member.id)); conn.commit(); conn.close()
    await ctx.send(f'✅ Cleared warnings for {member.mention}.')


@bot.command()
@mod_only()
async def timeout(ctx, member: discord.Member, minutes: int, *, reason='No reason provided'):
    if minutes < 1 or minutes > 40320:
        return await ctx.send('Minutes must be between 1 and 40320.')
    if is_protected(member) or member.top_role >= ctx.guild.me.top_role:
        return await ctx.send('I cannot timeout that member.')
    await member.timeout(timedelta(minutes=minutes), reason=reason)
    case_id = add_case(ctx.guild.id, member.id, ctx.author.id, 'timeout', reason)
    await ctx.send(f'⏱️ {member.mention} timed out for `{minutes}m`. Case `#{case_id}`.')
    await log_event(ctx.guild, title='Timeout', description=f'**User:** {member.mention}\n**Moderator:** {ctx.author.mention}\n**Duration:** {minutes}m\n**Reason:** {reason}\n**Case:** `#{case_id}`', color=discord.Color.orange(), channel_key='modlog_channel')


@bot.command()
@mod_only()
async def untimeout(ctx, member: discord.Member):
    await member.timeout(None, reason=f'Removed by {ctx.author}')
    await ctx.send(f'✅ Removed timeout from {member.mention}.')


@bot.command()
@mod_only()
async def kick(ctx, member: discord.Member, *, reason='No reason provided'):
    if is_protected(member) or member.top_role >= ctx.guild.me.top_role:
        return await ctx.send('I cannot kick that member.')
    await member.kick(reason=reason)
    case_id = add_case(ctx.guild.id, member.id, ctx.author.id, 'kick', reason)
    await ctx.send(f'👢 Kicked `{member}`. Case `#{case_id}`.')
    await log_event(ctx.guild, title='Kick', description=f'**User:** `{member}` (`{member.id}`)\n**Moderator:** {ctx.author.mention}\n**Reason:** {reason}\n**Case:** `#{case_id}`', color=discord.Color.red(), channel_key='modlog_channel')


@bot.command()
@mod_only()
async def ban(ctx, member: discord.Member, *, reason='No reason provided'):
    if is_protected(member) or member.top_role >= ctx.guild.me.top_role:
        return await ctx.send('I cannot ban that member.')
    await member.ban(reason=reason, delete_message_seconds=86400)
    case_id = add_case(ctx.guild.id, member.id, ctx.author.id, 'ban', reason)
    await ctx.send(f'🔨 Banned `{member}`. Case `#{case_id}`.')
    await log_event(ctx.guild, title='Ban', description=f'**User:** `{member}` (`{member.id}`)\n**Moderator:** {ctx.author.mention}\n**Reason:** {reason}\n**Case:** `#{case_id}`', color=discord.Color.dark_red(), channel_key='modlog_channel')


@bot.command()
@mod_only()
async def unban(ctx, user_id: int, *, reason='No reason provided'):
    try:
        user = await bot.fetch_user(user_id)
        await ctx.guild.unban(user, reason=reason)
        await ctx.send(f'✅ Unbanned `{user}`.')
    except discord.HTTPException:
        await ctx.send('Could not unban that user. Check the ID and bot permissions.')


@bot.command()
@mod_only()
async def purge(ctx, amount: int):
    if amount < 1 or amount > 100:
        return await ctx.send('Amount must be 1-100.')
    deleted = await ctx.channel.purge(limit=amount + 1)
    msg = await ctx.send(f'🧹 Deleted `{max(0, len(deleted)-1)}` messages.')
    await asyncio.sleep(3)
    await safe_delete(msg)


@bot.command()
@mod_only()
async def slowmode(ctx, seconds: int = 0):
    if seconds < 0 or seconds > 21600:
        return await ctx.send('Slowmode must be 0-21600 seconds.')
    await ctx.channel.edit(slowmode_delay=seconds)
    await ctx.send(f'🐢 Slowmode set to `{seconds}s`.')


@bot.command()
@mod_only()
async def lock(ctx):
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = False
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
    await ctx.send('🔒 Channel locked.')


@bot.command()
@mod_only()
async def unlock(ctx):
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = None
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
    await ctx.send('🔓 Channel unlocked.')


@bot.command()
@admin_only()
async def setlog(ctx, channel: discord.TextChannel):
    set_config(ctx.guild.id, 'log_channel', channel.id)
    await ctx.send(f'✅ AutoMod log channel set to {channel.mention}.')


@bot.command()
@admin_only()
async def setmodlog(ctx, channel: discord.TextChannel):
    set_config(ctx.guild.id, 'modlog_channel', channel.id)
    await ctx.send(f'✅ Moderation log channel set to {channel.mention}.')


@bot.command()
@admin_only()
async def config(ctx):
    cfg = get_config(ctx.guild.id)
    embed = discord.Embed(title='Server Configuration', color=discord.Color.blurple())
    for key in ['anti_links','anti_slurs','anti_spam','anti_caps','anti_mentions']:
        embed.add_field(name=key.replace('_',' ').title(), value='✅ Enabled' if cfg[key] else '❌ Disabled', inline=True)
    embed.add_field(name='Spam threshold', value=f'{cfg["spam_messages"]} msgs / {cfg["spam_window"]}s', inline=True)
    embed.add_field(name='Mention limit', value=str(cfg['max_mentions']), inline=True)
    embed.add_field(name='Auto punishment', value=cfg['punishment'], inline=True)
    embed.add_field(name='Timeout', value=f'{cfg["timeout_seconds"]}s', inline=True)
    await ctx.send(embed=embed)


@bot.command(name='setup')
@admin_only()
async def setup(ctx):
    guild = ctx.guild
    ensure_guild(guild.id)
    log_channel = discord.utils.get(guild.text_channels, name='mod-logs')
    if not log_channel:
        log_channel = await guild.create_text_channel('mod-logs', reason='Moderation bot setup')
    set_config(guild.id, 'log_channel', log_channel.id)
    set_config(guild.id, 'modlog_channel', log_channel.id)
    await ctx.send(f'✅ Setup complete. Logs: {log_channel.mention}\nUse `-config` to view AutoMod settings.')


@bot.group(name='automod', invoke_without_command=True)
@admin_only()
async def automod(ctx):
    await ctx.send('Use `-automod on/off links|slurs|spam|caps|mentions` or `-automod punishment timeout|warn|kick|ban`.')


@automod.command(name='on')
@admin_only()
async def automod_on(ctx, module: str):
    key = {'links':'anti_links','slurs':'anti_slurs','spam':'anti_spam','caps':'anti_caps','mentions':'anti_mentions'}.get(module.lower())
    if not key:
        return await ctx.send('Modules: `links`, `slurs`, `spam`, `caps`, `mentions`.')
    set_config(ctx.guild.id, key, 1)
    await ctx.send(f'✅ Enabled **{module.lower()}** protection.')


@automod.command(name='off')
@admin_only()
async def automod_off(ctx, module: str):
    key = {'links':'anti_links','slurs':'anti_slurs','spam':'anti_spam','caps':'anti_caps','mentions':'anti_mentions'}.get(module.lower())
    if not key:
        return await ctx.send('Modules: `links`, `slurs`, `spam`, `caps`, `mentions`.')
    set_config(ctx.guild.id, key, 0)
    await ctx.send(f'✅ Disabled **{module.lower()}** protection.')


@automod.command(name='punishment')
@admin_only()
async def automod_punishment(ctx, action: str):
    action = action.lower()
    if action not in {'warn','timeout','kick','ban'}:
        return await ctx.send('Punishments: `warn`, `timeout`, `kick`, `ban`.')
    set_config(ctx.guild.id, 'punishment', action)
    await ctx.send(f'✅ AutoMod punishment set to `{action}`.')


@bot.group(name='filter', invoke_without_command=True)
@admin_only()
async def filter_cmd(ctx):
    await ctx.send('Use `-filter add <word>`, `-filter remove <word>`, or `-filter list`.')


@filter_cmd.command(name='add')
@admin_only()
async def filter_add(ctx, *, word: str):
    word = word.strip().casefold()
    if not word or len(word) > 100:
        return await ctx.send('Invalid word.')
    conn = db(); conn.execute('INSERT OR IGNORE INTO bad_words (guild_id,word) VALUES (?,?)', (ctx.guild.id, word)); conn.commit(); conn.close()
    await ctx.send(f'✅ Added `{word}` to the blocked-word list.')


@filter_cmd.command(name='remove')
@admin_only()
async def filter_remove(ctx, *, word: str):
    conn = db(); conn.execute('DELETE FROM bad_words WHERE guild_id=? AND word=?', (ctx.guild.id, word.strip().casefold())); conn.commit(); conn.close()
    await ctx.send(f'✅ Removed `{word}` from the custom blocked-word list.')


@filter_cmd.command(name='list')
@admin_only()
async def filter_list(ctx):
    conn = db(); rows = conn.execute('SELECT word FROM bad_words WHERE guild_id=? ORDER BY word', (ctx.guild.id,)).fetchall(); conn.close()
    if not rows:
        return await ctx.send('No custom blocked words configured.')
    await ctx.send('Custom blocked words:\n' + '\n'.join(f'• `{r["word"]}`' for r in rows[:100]))


@bot.group(name='whitelist', invoke_without_command=True)
@admin_only()
async def whitelist(ctx):
    await ctx.send('Use `-whitelist add <domain>` or `-whitelist remove <domain>`.')


@whitelist.command(name='add')
@admin_only()
async def whitelist_add(ctx, domain: str):
    domain = domain.lower().replace('https://','').replace('http://','').replace('www.','').split('/')[0]
    conn = db(); conn.execute('INSERT OR IGNORE INTO whitelist_links (guild_id,domain) VALUES (?,?)', (ctx.guild.id, domain)); conn.commit(); conn.close()
    await ctx.send(f'✅ Whitelisted `{domain}`.')


@whitelist.command(name='remove')
@admin_only()
async def whitelist_remove(ctx, domain: str):
    domain = domain.lower().replace('https://','').replace('http://','').replace('www.','').split('/')[0]
    conn = db(); conn.execute('DELETE FROM whitelist_links WHERE guild_id=? AND domain=?', (ctx.guild.id, domain)); conn.commit(); conn.close()
    await ctx.send(f'✅ Removed `{domain}` from the link whitelist.')


@bot.command()
@admin_only()
async def reactionrole(ctx, role: discord.Role, emoji: str = '✅', *, text='React to get this role.'):
    if role >= ctx.guild.me.top_role:
        return await ctx.send('Move my bot role above the role you want to assign.')
    embed = discord.Embed(title='Reaction Role', description=text, color=discord.Color.blurple())
    msg = await ctx.send(embed=embed)
    await msg.add_reaction(emoji)
    conn = db()
    conn.execute('INSERT OR REPLACE INTO reaction_roles (guild_id,channel_id,message_id,role_id,emoji) VALUES (?,?,?,?,?)', (ctx.guild.id, ctx.channel.id, msg.id, role.id, emoji))
    conn.commit(); conn.close()
    await ctx.send(f'✅ Reaction role created for {role.mention}.', delete_after=5)


@bot.command()
async def userinfo(ctx, member: discord.Member = None):
    member = member or ctx.author
    embed = discord.Embed(title=str(member), color=member.color)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name='ID', value=member.id)
    embed.add_field(name='Joined', value=discord.utils.format_dt(member.joined_at, 'R') if member.joined_at else 'Unknown')
    embed.add_field(name='Created', value=discord.utils.format_dt(member.created_at, 'R'))
    embed.add_field(name='Top role', value=member.top_role.mention)
    await ctx.send(embed=embed)


@bot.command()
async def serverinfo(ctx):
    g = ctx.guild
    embed = discord.Embed(title=g.name, color=discord.Color.blurple())
    if g.icon:
        embed.set_thumbnail(url=g.icon.url)
    embed.add_field(name='Members', value=g.member_count)
    embed.add_field(name='Channels', value=len(g.channels))
    embed.add_field(name='Roles', value=len(g.roles))
    embed.add_field(name='Owner', value=g.owner.mention if g.owner else str(g.owner_id))
    await ctx.send(embed=embed)


@bot.command()
@mod_only()
async def case(ctx, case_id: int):
    conn = db(); row = conn.execute('SELECT * FROM cases WHERE guild_id=? AND id=?', (ctx.guild.id, case_id)).fetchone(); conn.close()
    if not row:
        return await ctx.send('Case not found.')
    embed = discord.Embed(title=f'Case #{case_id}', color=discord.Color.blurple())
    embed.add_field(name='Action', value=row['action'])
    embed.add_field(name='User', value=f'<@{row["user_id"]}>')
    embed.add_field(name='Moderator', value=f'<@{row["moderator_id"]}>')
    embed.add_field(name='Reason', value=row['reason'] or 'No reason')
    embed.add_field(name='Created', value=discord.utils.format_dt(datetime.fromisoformat(row['created_at']), 'F'))
    await ctx.send(embed=embed)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingPermissions):
        return await ctx.send('🚫 You do not have permission to use that command.', delete_after=5)
    if isinstance(error, commands.CheckFailure):
        return await ctx.send('🚫 You do not have permission to use that command.', delete_after=5)
    if isinstance(error, commands.MissingRequiredArgument):
        return await ctx.send(f'Usage: `{PREFIX}{ctx.command.qualified_name} {ctx.command.signature}`', delete_after=7)
    if isinstance(error, commands.BadArgument):
        return await ctx.send('❌ Invalid member/role/channel/number. Check your arguments.', delete_after=6)
    print(f'Command error in {ctx.command}: {repr(error)}')
    await ctx.send('❌ Something went wrong running that command.', delete_after=6)


if __name__ == '__main__':
    bot.run(TOKEN)
