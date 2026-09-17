import os
import re
import time
import sqlite3
import asyncio
import io
import random
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

try:
    import aiohttp
except ImportError:
    aiohttp = None

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN") or os.getenv("DISCORD_BOT_TOKEN", "")
PREFIX = os.getenv("PREFIX", "-")
DB_PATH = os.getenv("DB_PATH", "bot.db")
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "").rstrip("/")
BOT_SYNC_KEY = os.getenv("BOT_SYNC_KEY", "")

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN / DISCORD_BOT_TOKEN is missing.")

INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.members = True
INTENTS.reactions = True
INTENTS.guilds = True
INTENTS.moderation = True

bot = commands.Bot(
    command_prefix=commands.when_mentioned_or(PREFIX),
    intents=INTENTS,
    help_command=None,
    strip_after_prefix=True,
)

URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.I)
INVITE_RE = re.compile(r"(?:discord(?:\.gg|\.com/invite)/)[\w-]+", re.I)
DOMAIN_RE = re.compile(r"(?:https?://|www\.)(?:[^/]+)", re.I)

message_buckets = defaultdict(deque)
duplicate_buckets = defaultdict(deque)
raid_join_buckets = defaultdict(deque)
level_cooldowns = {}
chatted_sessions = {}
ready_once = False

DEFAULT_BAD_WORDS = {"examplebadword"}

PALETTE = {
    "main": discord.Color.from_rgb(88, 101, 242),
    "success": discord.Color.from_rgb(87, 242, 135),
    "warning": discord.Color.from_rgb(254, 231, 92),
    "danger": discord.Color.from_rgb(237, 66, 69),
    "dark": discord.Color.from_rgb(35, 39, 42),
}


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS guild_config (
            guild_id INTEGER PRIMARY KEY,
            prefix TEXT DEFAULT '-',
            log_channel INTEGER,
            modlog_channel INTEGER,
            welcome_channel INTEGER,
            welcome_message TEXT DEFAULT 'Welcome {member} to **{server}**!',
            autorole_id INTEGER,
            verify_channel INTEGER,
            verify_role INTEGER,
            unverified_role INTEGER,
            ticket_category INTEGER,
            ticket_support_role INTEGER,
            suggestion_channel INTEGER,
            level_channel INTEGER,
            anti_links INTEGER DEFAULT 1,
            anti_invites INTEGER DEFAULT 1,
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
            leveling INTEGER DEFAULT 1,
            welcome_enabled INTEGER DEFAULT 1,
            autorole_enabled INTEGER DEFAULT 0,
            verification_enabled INTEGER DEFAULT 0,
            tickets_enabled INTEGER DEFAULT 0
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
            message_id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            label TEXT DEFAULT 'Get Role',
            emoji TEXT DEFAULT '✅'
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
        CREATE TABLE IF NOT EXISTS levels (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            xp INTEGER DEFAULT 0,
            level INTEGER DEFAULT 0,
            PRIMARY KEY(guild_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            message_id INTEGER,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tickets (
            channel_id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            closed_at TEXT
        );
        """
    )

    # Upgrade existing installations without wiping bot.db.
    existing = {row[1] for row in conn.execute("PRAGMA table_info(guild_config)").fetchall()}
    additions = {
        "prefix": "TEXT DEFAULT '-'",
        "log_channel": "INTEGER",
        "modlog_channel": "INTEGER",
        "welcome_channel": "INTEGER",
        "welcome_message": "TEXT DEFAULT 'Welcome {member} to **{server}**!'",
        "autorole_id": "INTEGER",
        "verify_channel": "INTEGER",
        "verify_role": "INTEGER",
        "unverified_role": "INTEGER",
        "ticket_category": "INTEGER",
        "ticket_support_role": "INTEGER",
        "suggestion_channel": "INTEGER",
        "level_channel": "INTEGER",
        "anti_links": "INTEGER DEFAULT 1",
        "anti_invites": "INTEGER DEFAULT 1",
        "anti_slurs": "INTEGER DEFAULT 1",
        "anti_spam": "INTEGER DEFAULT 1",
        "anti_caps": "INTEGER DEFAULT 0",
        "anti_mentions": "INTEGER DEFAULT 1",
        "max_mentions": "INTEGER DEFAULT 5",
        "spam_messages": "INTEGER DEFAULT 6",
        "spam_window": "INTEGER DEFAULT 8",
        "punishment": "TEXT DEFAULT 'timeout'",
        "timeout_seconds": "INTEGER DEFAULT 600",
        "warn_threshold": "INTEGER DEFAULT 3",
        "leveling": "INTEGER DEFAULT 1",
        "welcome_enabled": "INTEGER DEFAULT 1",
        "autorole_enabled": "INTEGER DEFAULT 0",
        "verification_enabled": "INTEGER DEFAULT 0",
        "tickets_enabled": "INTEGER DEFAULT 0",
    }
    for column, definition in additions.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE guild_config ADD COLUMN {column} {definition}")
    conn.commit()
    conn.close()


def ensure_guild(guild_id: int):
    conn = db()
    conn.execute("INSERT OR IGNORE INTO guild_config (guild_id) VALUES (?)", (guild_id,))
    conn.commit()
    conn.close()


def get_config(guild_id: int):
    ensure_guild(guild_id)
    conn = db()
    row = conn.execute("SELECT * FROM guild_config WHERE guild_id=?", (guild_id,)).fetchone()
    conn.close()
    return row


def set_config(guild_id: int, key: str, value):
    allowed = {
        "prefix", "log_channel", "modlog_channel", "welcome_channel", "welcome_message",
        "autorole_id", "verify_channel", "verify_role", "unverified_role",
        "ticket_category", "ticket_support_role", "suggestion_channel", "level_channel",
        "anti_links", "anti_invites", "anti_slurs", "anti_spam", "anti_caps",
        "anti_mentions", "max_mentions", "spam_messages", "spam_window", "punishment",
        "timeout_seconds", "warn_threshold", "leveling", "welcome_enabled",
        "autorole_enabled", "verification_enabled", "tickets_enabled"
    }
    if key not in allowed:
        raise ValueError("invalid config key")
    conn = db()
    conn.execute(f"UPDATE guild_config SET {key}=? WHERE guild_id=?", (value, guild_id))
    conn.commit()
    conn.close()


def add_case(guild_id, user_id, moderator_id, action, reason=""):
    conn = db()
    cur = conn.execute(
        "INSERT INTO cases(guild_id,user_id,moderator_id,action,reason,created_at) VALUES(?,?,?,?,?,?)",
        (guild_id, user_id, moderator_id, action, reason, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    case_id = cur.lastrowid
    conn.close()
    return case_id


def add_warning(guild_id, user_id, moderator_id, reason):
    conn = db()
    cur = conn.execute(
        "INSERT INTO warnings(guild_id,user_id,moderator_id,reason,created_at) VALUES(?,?,?,?,?)",
        (guild_id, user_id, moderator_id, reason, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    warning_id = cur.lastrowid
    conn.close()
    return warning_id


def warning_count(guild_id, user_id):
    conn = db()
    row = conn.execute("SELECT COUNT(*) AS n FROM warnings WHERE guild_id=? AND user_id=?", (guild_id, user_id)).fetchone()
    conn.close()
    return row["n"]


def normalize(text: str) -> str:
    text = text.casefold()
    text = re.sub(r"[^\w\s]", "", text, flags=re.UNICODE)
    return re.sub(r"(.)\1{3,}", r"\1\1", text)


def member_is_staff(member: discord.Member) -> bool:
    p = member.guild_permissions
    return p.administrator or p.manage_guild or p.manage_messages


def is_protected(member: discord.Member) -> bool:
    return member_is_staff(member) or member.id == member.guild.owner_id


def has_url(text: str) -> bool:
    return bool(URL_RE.search(text))


def extract_domain(url: str) -> str:
    match = DOMAIN_RE.search(url)
    if not match:
        return ""
    host = match.group(0).lower()
    host = re.sub(r"^https?://", "", host).removeprefix("www.")
    return host.split("/")[0].split(":")[0]


def get_bad_words(guild_id: int):
    conn = db()
    rows = conn.execute("SELECT word FROM bad_words WHERE guild_id=?", (guild_id,)).fetchall()
    conn.close()
    return {row["word"].casefold() for row in rows} | DEFAULT_BAD_WORDS


def get_whitelist(guild_id: int):
    conn = db()
    rows = conn.execute("SELECT domain FROM whitelist_links WHERE guild_id=?", (guild_id,)).fetchall()
    conn.close()
    return {row["domain"].casefold() for row in rows}


def progress_bar(value: int, maximum: int, size: int = 10):
    maximum = max(1, maximum)
    filled = max(0, min(size, round((value / maximum) * size)))
    return "▰" * filled + "▱" * (size - filled)


async def safe_delete(message):
    try:
        await message.delete()
        return True
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        return False


async def animated_reply(send, title, steps, ephemeral=False):
    msg = await send(embed=discord.Embed(title=title, description="▰▱▱▱▱", color=PALETTE["main"]), ephemeral=ephemeral)
    for i, text in enumerate(steps, start=1):
        await asyncio.sleep(0.18)
        bar = progress_bar(i, len(steps), 10)
        try:
            await msg.edit(embed=discord.Embed(title=title, description=f"{bar}\n{text}", color=PALETTE["main"]))
        except discord.HTTPException:
            break
    return msg


async def log_event(guild, *, title, description, color=PALETTE["main"], channel_key="log_channel"):
    cfg = get_config(guild.id)
    channel_id = cfg[channel_key]
    if not channel_id:
        return
    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return
    embed = discord.Embed(title=title, description=description, color=color, timestamp=datetime.now(timezone.utc))
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        pass


def make_embed(title, description="", color=PALETTE["main"]):
    return discord.Embed(title=title, description=description, color=color, timestamp=datetime.now(timezone.utc))


async def dm_member(member: discord.Member, title, description):
    try:
        await member.send(embed=make_embed(title, description, PALETTE["main"]))
        return True
    except discord.HTTPException:
        return False


async def punish_message(message: discord.Message, reason: str, action=None):
    if is_protected(message.author):
        return False
    cfg = get_config(message.guild.id)
    action = action or cfg["punishment"]
    await safe_delete(message)
    try:
        if action == "warn":
            warning_id = add_warning(message.guild.id, message.author.id, bot.user.id, reason)
            await message.channel.send(f"{message.author.mention} warning issued. `#{warning_id}`", delete_after=6)
        elif action == "kick" and message.author.top_role < message.guild.me.top_role:
            await message.author.kick(reason=reason)
        elif action == "ban" and message.author.top_role < message.guild.me.top_role:
            await message.author.ban(reason=reason, delete_message_seconds=86400)
        elif message.author.top_role < message.guild.me.top_role:
            await message.author.timeout(timedelta(seconds=int(cfg["timeout_seconds"])), reason=reason)
    except discord.HTTPException:
        pass
    case_id = add_case(message.guild.id, message.author.id, bot.user.id, action, reason)
    await log_event(message.guild, title=f"AutoMod • {action.title()}", description=f"**User:** {message.author.mention} (`{message.author.id}`)\n**Reason:** {reason}\n**Case:** `#{case_id}`", color=PALETTE["danger"])
    return True


def get_level(guild_id, user_id):
    conn = db()
    row = conn.execute("SELECT * FROM levels WHERE guild_id=? AND user_id=?", (guild_id, user_id)).fetchone()
    conn.close()
    return row or {"xp": 0, "level": 0}


def add_xp(guild_id, user_id, amount):
    conn = db()
    conn.execute("INSERT OR IGNORE INTO levels(guild_id,user_id,xp,level) VALUES(?,?,0,0)", (guild_id, user_id))
    row = conn.execute("SELECT xp,level FROM levels WHERE guild_id=? AND user_id=?", (guild_id, user_id)).fetchone()
    xp = int(row["xp"]) + amount
    level = int(row["level"])
    required = 100 + (level * 50)
    leveled = False
    while xp >= required:
        xp -= required
        level += 1
        required = 100 + (level * 50)
        leveled = True
    conn.execute("UPDATE levels SET xp=?,level=? WHERE guild_id=? AND user_id=?", (xp, level, guild_id, user_id))
    conn.commit()
    conn.close()
    return xp, level, required, leveled


def can_manage(member: discord.Member, target: discord.Member):
    if target.id == member.id or target.id == member.guild.owner_id or target.id == bot.user.id:
        return False
    return target.top_role < member.top_role and target.top_role < member.guild.me.top_role


class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Verify", style=discord.ButtonStyle.success, emoji="✅", custom_id="rm:verify")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("This only works in a server.", ephemeral=True)
        cfg = get_config(guild.id)
        role = guild.get_role(cfg["verify_role"]) if cfg["verify_role"] else None
        member = interaction.user
        if not role:
            return await interaction.response.send_message("Verification isn't configured correctly. Ask an administrator.", ephemeral=True)
        if role in member.roles:
            return await interaction.response.send_message("You're already verified ✅", ephemeral=True)
        if role >= guild.me.top_role:
            return await interaction.response.send_message("My bot role must be above the verification role.", ephemeral=True)
        try:
            await member.add_roles(role, reason="RM verification")
            unverified = guild.get_role(cfg["unverified_role"]) if cfg["unverified_role"] else None
            if unverified and unverified in member.roles and unverified < guild.me.top_role:
                await member.remove_roles(unverified, reason="RM verification")
            await log_event(guild, title="✅ Member Verified", description=f"{member.mention} (`{member.id}`) verified.", color=PALETTE["success"])
            await interaction.response.send_message("Verification complete. Welcome in 🤝", ephemeral=True)
        except discord.HTTPException:
            await interaction.response.send_message("I couldn't update your roles.", ephemeral=True)


class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open Ticket", style=discord.ButtonStyle.primary, emoji="🎫", custom_id="rm:ticket_open")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)
        cfg = get_config(guild.id)
        conn = db()
        row = conn.execute("SELECT channel_id FROM tickets WHERE guild_id=? AND user_id=? AND closed_at IS NULL", (guild.id, interaction.user.id)).fetchone()
        conn.close()
        existing = guild.get_channel(row["channel_id"]) if row else None
        if existing:
            return await interaction.response.send_message(f"You already have a ticket: {existing.mention}", ephemeral=True)

        category = guild.get_channel(cfg["ticket_category"]) if cfg["ticket_category"] else None
        support_role = guild.get_role(cfg["ticket_support_role"]) if cfg["ticket_support_role"] else None
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, manage_messages=True),
        }
        if support_role:
            overwrites[support_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

        safe_name = re.sub(r"[^a-z0-9-]", "", interaction.user.name.lower())[:18] or "user"
        try:
            channel = await guild.create_text_channel(f"ticket-{safe_name}", category=category if isinstance(category, discord.CategoryChannel) else None, overwrites=overwrites, topic=f"RM Ticket • {interaction.user.id}", reason="RM ticket opened")
        except discord.HTTPException:
            return await interaction.response.send_message("I couldn't create the ticket. Check Manage Channels.", ephemeral=True)

        conn = db()
        conn.execute("INSERT INTO tickets(channel_id,guild_id,user_id,created_at) VALUES(?,?,?,?)", (channel.id, guild.id, interaction.user.id, datetime.now(timezone.utc).isoformat()))
        conn.commit()
        conn.close()

        embed = make_embed("🎫 Ticket opened", f"Hey {interaction.user.mention}! Tell staff what you need.\n\nA staff member will be with you shortly.")
        await channel.send(content=f"{interaction.user.mention} {support_role.mention if support_role else ''}", embed=embed, view=CloseTicketView())
        await interaction.response.send_message(f"Ticket created: {channel.mention}", ephemeral=True)


class CloseTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="rm:ticket_close")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("Invalid ticket.", ephemeral=True)
        conn = db()
        row = conn.execute("SELECT * FROM tickets WHERE channel_id=?", (interaction.channel.id,)).fetchone()
        conn.close()
        if row is None:
            return await interaction.response.send_message("This isn't an RM ticket.", ephemeral=True)
        allowed = interaction.user.id == row["user_id"] or member_is_staff(interaction.user) or interaction.user.id == interaction.guild.owner_id
        if not allowed:
            return await interaction.response.send_message("You can't close this ticket.", ephemeral=True)
        conn = db()
        conn.execute("UPDATE tickets SET closed_at=? WHERE channel_id=?", (datetime.now(timezone.utc).isoformat(), interaction.channel.id))
        conn.commit()
        conn.close()
        await interaction.response.send_message("🔒 Closing ticket in a moment...")
        await asyncio.sleep(1.2)
        try:
            await interaction.channel.delete(reason="RM ticket closed")
        except discord.HTTPException:
            pass


class ReactionRoleView(discord.ui.View):
    def __init__(self, role_id: int):
        super().__init__(timeout=None)
        self.role_id = role_id

    @discord.ui.button(label="Get Role", style=discord.ButtonStyle.secondary, emoji="🎟️", custom_id="rm:reaction_role")
    async def get_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        role = guild.get_role(self.role_id) if guild else None
        if not role:
            return await interaction.response.send_message("That role no longer exists.", ephemeral=True)
        if role >= guild.me.top_role:
            return await interaction.response.send_message("Move my role above the target role.", ephemeral=True)
        try:
            if role in interaction.user.roles:
                await interaction.user.remove_roles(role, reason="RM role toggle")
                await interaction.response.send_message(f"Removed **{role.name}**.", ephemeral=True)
            else:
                await interaction.user.add_roles(role, reason="RM role toggle")
                await interaction.response.send_message(f"Added **{role.name}**.", ephemeral=True)
        except discord.HTTPException:
            await interaction.response.send_message("Role update failed.", ephemeral=True)


def mod_only():
    async def predicate(ctx):
        return bool(ctx.guild and (ctx.author.guild_permissions.manage_messages or ctx.author.guild_permissions.manage_guild or ctx.author.guild_permissions.administrator))
    return commands.check(predicate)


def admin_only():
    async def predicate(ctx):
        return bool(ctx.guild and (ctx.author.guild_permissions.manage_guild or ctx.author.guild_permissions.administrator))
    return commands.check(predicate)


async def is_bot_owner(user):
    if OWNER_ID and user.id == OWNER_ID:
        return True
    try:
        return await bot.is_owner(user)
    except Exception:
        return False


async def dashboard_sync(action, data):
    if not (DASHBOARD_URL and BOT_SYNC_KEY and aiohttp):
        return
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(
                f"{DASHBOARD_URL}/api/public/bot/sync",
                json={"action": action, "data": data},
                headers={"x-bot-key": BOT_SYNC_KEY, "Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=8),
            )
    except Exception:
        pass


@tasks.loop(minutes=2)
async def dashboard_heartbeat():
    await dashboard_sync("heartbeat", {"server_count": len(bot.guilds), "member_count": sum(g.member_count or 0 for g in bot.guilds), "latency": round(bot.latency * 1000)})


@tasks.loop(seconds=20)
async def presence_loop():
    if not bot.user:
        return
    total_members = sum(g.member_count or 0 for g in bot.guilds)
    choices = [f"{PREFIX}help • {len(bot.guilds)} servers", f"/help • {total_members:,} members", "RM Control Center • secured", "RM • moderation + security"]
    try:
        await bot.change_presence(status=discord.Status.online, activity=discord.Activity(type=discord.ActivityType.watching, name=random.choice(choices)))
    except discord.HTTPException:
        pass


@bot.event
async def setup_hook():
    init_db()
    for guild in bot.guilds:
        ensure_guild(guild.id)
    bot.add_view(VerifyView())
    bot.add_view(TicketView())
    bot.add_view(CloseTicketView())
    conn = db()
    rows = conn.execute("SELECT role_id FROM reaction_roles").fetchall()
    conn.close()
    for row in rows:
        bot.add_view(ReactionRoleView(row["role_id"]))
    await bot.tree.sync()


@bot.event
async def on_ready():
    global ready_once
    print(f"Logged in as {bot.user} ({bot.user.id})")
    print(f"Connected to {len(bot.guilds)} server(s)")
    print(f"Slash commands synced • Prefix: {PREFIX}")
    if not ready_once:
        ready_once = True
        if not dashboard_heartbeat.is_running():
            dashboard_heartbeat.start()
        if not presence_loop.is_running():
            presence_loop.start()


@bot.event
async def on_guild_join(guild):
    ensure_guild(guild.id)
    await log_event(guild, title="✨ RM joined", description=f"RM is now online in **{guild.name}**.\nUse `/setup` or `{PREFIX}setup` to configure it.", color=PALETTE["success"])


@bot.event
async def on_member_join(member: discord.Member):
    if member.bot:
        return
    ensure_guild(member.guild.id)
    cfg = get_config(member.guild.id)
    now = time.monotonic()
    bucket = raid_join_buckets[member.guild.id]
    bucket.append(now)
    while bucket and now - bucket[0] > 10:
        bucket.popleft()
    if len(bucket) >= 8:
        await log_event(member.guild, title="🚨 Possible raid activity", description=f"{len(bucket)} members joined within ~10 seconds.", color=PALETTE["danger"])
    if cfg["autorole_enabled"] and cfg["autorole_id"]:
        role = member.guild.get_role(cfg["autorole_id"])
        if role and role < member.guild.me.top_role:
            try:
                await member.add_roles(role, reason="RM autorole")
            except discord.HTTPException:
                pass
    if cfg["verification_enabled"] and cfg["unverified_role"]:
        role = member.guild.get_role(cfg["unverified_role"])
        if role and role < member.guild.me.top_role:
            try:
                await member.add_roles(role, reason="RM verification gate")
            except discord.HTTPException:
                pass
    if cfg["welcome_enabled"] and cfg["welcome_channel"]:
        channel = member.guild.get_channel(cfg["welcome_channel"])
        if isinstance(channel, discord.TextChannel):
            text = cfg["welcome_message"] or "Welcome {member} to **{server}**!"
            text = text.replace("{member}", member.mention).replace("{server}", member.guild.name)
            embed = make_embed("👋 Welcome!", f"{text}\n\nYou are member **#{member.guild.member_count}**.", PALETTE["success"])
            embed.set_thumbnail(url=member.display_avatar.url)
            try:
                await channel.send(embed=embed)
            except discord.HTTPException:
                pass


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
    if cfg["anti_invites"] and INVITE_RE.search(text):
        await punish_message(message, "Discord invite link")
        return
    if cfg["anti_links"] and has_url(text):
        whitelist = get_whitelist(message.guild.id)
        domains = {extract_domain(u) for u in URL_RE.findall(text)}
        if not domains or not domains.issubset(whitelist):
            await punish_message(message, "Unapproved external link")
            return
    if cfg["anti_slurs"]:
        for word in get_bad_words(message.guild.id):
            if word and re.search(rf"(?<!\w){re.escape(word)}(?!\w)", normalized):
                await punish_message(message, "Blocked word filter")
                return
    if cfg["anti_mentions"]:
        mentions = len(message.mentions) + len(message.role_mentions)
        if message.mention_everyone:
            mentions += 10
        if mentions >= int(cfg["max_mentions"]):
            await punish_message(message, f"Mention spam ({mentions} mentions)")
            return
    if cfg["anti_caps"] and len(text) >= 12:
        letters = [c for c in text if c.isalpha()]
        if letters and sum(c.isupper() for c in letters) / len(letters) >= 0.85:
            await punish_message(message, "Excessive caps")
            return
    if cfg["anti_spam"]:
        key = (message.guild.id, member.id)
        now = time.monotonic()
        bucket = message_buckets[key]
        bucket.append(now)
        while bucket and now - bucket[0] > int(cfg["spam_window"]):
            bucket.popleft()
        if len(bucket) >= int(cfg["spam_messages"]):
            bucket.clear()
            await punish_message(message, f"Spam ({cfg['spam_messages']} messages in {cfg['spam_window']}s)")
            return
        dup = duplicate_buckets[key]
        dup.append((now, normalized[:300]))
        while dup and now - dup[0][0] > 12:
            dup.popleft()
        recent_same = sum(1 for _, value in dup if value and value == normalized[:300])
        if normalized and len(normalized) >= 4 and recent_same >= 3:
            dup.clear()
            await punish_message(message, "Repeated message spam")
            return
    if cfg["leveling"]:
        key = (message.guild.id, member.id)
        now = time.monotonic()
        if now - level_cooldowns.get(key, 0) >= 45 and len(text.strip()) >= 2:
            level_cooldowns[key] = now
            xp, level, required, leveled = add_xp(message.guild.id, member.id, random.randint(8, 15))
            if leveled:
                await message.channel.send(embed=make_embed("🎉 Level up!", f"{member.mention} reached **Level {level}**.\n`{progress_bar(xp, required)}` **{xp}/{required} XP**", PALETTE["success"]), delete_after=8)
    await bot.process_commands(message)


@bot.hybrid_command(name="ping", description="Check RM latency.")
async def ping(ctx):
    await ctx.reply(f"🏓 **Pong!** `{round(bot.latency * 1000)}ms`", mention_author=False)


@bot.hybrid_command(name="help", description="Open the RM command center.")
async def help_cmd(ctx):
    embed = make_embed("RM • Control Center", "One bot. One command system. `/command` and `-command` both work.")
    embed.add_field(name="🛡️ Moderation", value="`warn` `warnings` `clearwarns` `timeout` `untimeout` `kick` `ban` `unban` `purge` `lock` `unlock` `slowmode` `case`", inline=False)
    embed.add_field(name="⚙️ Server", value="`setup` `config` `automod` `filter` `setlog` `setmodlog` `welcome` `autorole` `verification` `tickets` `reactionrole`", inline=False)
    embed.add_field(name="✨ Community", value="`userinfo` `serverinfo` `rank` `leaderboard` `suggest`", inline=False)
    embed.set_footer(text=f"Prefix: {PREFIX} • Slash commands: enabled")
    await ctx.reply(embed=embed)


@bot.hybrid_command(name="warn", description="Warn a member.")
@mod_only()
async def warn(ctx, member: discord.Member, *, reason="No reason provided"):
    if is_protected(member) or not can_manage(ctx.author, member):
        return await ctx.reply("I can't moderate that member.", ephemeral=True)
    warning_id = add_warning(ctx.guild.id, member.id, ctx.author.id, reason)
    count = warning_count(ctx.guild.id, member.id)
    cfg = get_config(ctx.guild.id)
    if count >= cfg["warn_threshold"] and member.top_role < ctx.guild.me.top_role:
        try:
            await member.timeout(timedelta(seconds=int(cfg["timeout_seconds"])), reason=f"Warning threshold reached: {reason}")
        except discord.HTTPException:
            pass
    case_id = add_case(ctx.guild.id, member.id, ctx.author.id, "warn", reason)
    await dm_member(member, "⚠️ You received a warning", f"**Server:** {ctx.guild.name}\n**Reason:** {reason}\n**Warnings:** `{count}`")
    await ctx.reply(embed=make_embed("⚠️ Warning issued", f"{member.mention} was warned.\n**Warning:** `#{warning_id}`\n**Total:** `{count}`\n**Case:** `#{case_id}`", PALETTE["warning"]))
    await log_event(ctx.guild, title="Warning", description=f"**User:** {member.mention}\n**Moderator:** {ctx.author.mention}\n**Reason:** {reason}\n**Case:** `#{case_id}`", color=PALETTE["warning"], channel_key="modlog_channel")


@bot.hybrid_command(name="warnings", description="View a member's warnings.")
@mod_only()
async def warnings(ctx, member: discord.Member):
    conn = db()
    rows = conn.execute("SELECT * FROM warnings WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 10", (ctx.guild.id, member.id)).fetchall()
    conn.close()
    if not rows:
        return await ctx.reply(f"{member.mention} has no warnings.")
    lines = [f"`#{r['id']}` • {discord.utils.format_dt(datetime.fromisoformat(r['created_at']), 'R')} • {r['reason']}" for r in rows]
    await ctx.reply(embed=make_embed(f"Warnings • {member}", "\n".join(lines), PALETTE["warning"]))


@bot.hybrid_command(name="clearwarns", description="Clear all warnings for a member.")
@mod_only()
async def clearwarns(ctx, member: discord.Member):
    conn = db()
    conn.execute("DELETE FROM warnings WHERE guild_id=? AND user_id=?", (ctx.guild.id, member.id))
    conn.commit()
    conn.close()
    await ctx.reply(f"✅ Cleared warnings for {member.mention}.")


@bot.hybrid_command(name="timeout", description="Timeout a member.")
@mod_only()
async def timeout(ctx, member: discord.Member, minutes: int, *, reason="No reason provided"):
    if not 1 <= minutes <= 40320:
        return await ctx.reply("Minutes must be between `1` and `40320`.", ephemeral=True)
    if is_protected(member) or not can_manage(ctx.author, member):
        return await ctx.reply("I can't timeout that member.", ephemeral=True)
    await member.timeout(timedelta(minutes=minutes), reason=reason)
    case_id = add_case(ctx.guild.id, member.id, ctx.author.id, "timeout", reason)
    await dm_member(member, "⏱️ You were timed out", f"**Duration:** {minutes}m\n**Reason:** {reason}")
    await ctx.reply(f"⏱️ Timed out {member.mention} for **{minutes}m** • Case `#{case_id}`.")


@bot.hybrid_command(name="untimeout", description="Remove a timeout.")
@mod_only()
async def untimeout(ctx, member: discord.Member):
    if is_protected(member) or not can_manage(ctx.author, member):
        return await ctx.reply("I can't change that member.", ephemeral=True)
    await member.timeout(None, reason=f"Removed by {ctx.author}")
    await ctx.reply(f"✅ Removed timeout from {member.mention}.")


@bot.hybrid_command(name="kick", description="Kick a member.")
@mod_only()
async def kick(ctx, member: discord.Member, *, reason="No reason provided"):
    if is_protected(member) or not can_manage(ctx.author, member):
        return await ctx.reply("I can't kick that member.", ephemeral=True)
    await member.kick(reason=reason)
    case_id = add_case(ctx.guild.id, member.id, ctx.author.id, "kick", reason)
    await ctx.reply(f"👢 Kicked `{member}` • Case `#{case_id}`.")


@bot.hybrid_command(name="ban", description="Ban a member.")
@mod_only()
async def ban(ctx, member: discord.Member, *, reason="No reason provided"):
    if is_protected(member) or not can_manage(ctx.author, member):
        return await ctx.reply("I can't ban that member.", ephemeral=True)
    await member.ban(reason=reason, delete_message_seconds=86400)
    case_id = add_case(ctx.guild.id, member.id, ctx.author.id, "ban", reason)
    await ctx.reply(f"🔨 Banned `{member}` • Case `#{case_id}`.")


@bot.hybrid_command(name="unban", description="Unban a user by ID.")
@mod_only()
async def unban(ctx, user_id: int, *, reason="No reason provided"):
    try:
        user = await bot.fetch_user(user_id)
        await ctx.guild.unban(user, reason=reason)
        await ctx.reply(f"✅ Unbanned `{user}`.")
    except discord.HTTPException:
        await ctx.reply("I couldn't unban that user. Check the ID/permissions.", ephemeral=True)


@bot.hybrid_command(name="purge", description="Delete recent messages.")
@mod_only()
async def purge(ctx, amount: int):
    if not 1 <= amount <= 100:
        return await ctx.reply("Amount must be between `1` and `100`.", ephemeral=True)
    deleted = await ctx.channel.purge(limit=amount + 1)
    count = max(0, len(deleted) - 1)
    await ctx.reply(f"🧹 Deleted **{count}** messages.", delete_after=4)


@bot.hybrid_command(name="slowmode", description="Set channel slowmode.")
@mod_only()
async def slowmode(ctx, seconds: int = 0):
    if not 0 <= seconds <= 21600:
        return await ctx.reply("Seconds must be between `0` and `21600`.", ephemeral=True)
    await ctx.channel.edit(slowmode_delay=seconds)
    await ctx.reply(f"🐢 Slowmode set to **{seconds}s**.")


@bot.hybrid_command(name="lock", description="Lock the current channel.")
@mod_only()
async def lock(ctx):
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = False
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
    await ctx.reply("🔒 Channel locked.")


@bot.hybrid_command(name="unlock", description="Unlock the current channel.")
@mod_only()
async def unlock(ctx):
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = None
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
    await ctx.reply("🔓 Channel unlocked.")


@bot.hybrid_command(name="setup", description="Create a polished starter RM setup.")
@admin_only()
async def setup(ctx):
    guild = ctx.guild
    steps = ["Checking permissions…", "Creating RM channels…", "Configuring logs…", "Enabling protection…", "Finalizing Control Center…"]
    msg = await animated_reply(ctx.reply, "RM • Server Setup", steps)
    created = []
    log_channel = discord.utils.get(guild.text_channels, name="rm-logs")
    if not log_channel:
        try:
            log_channel = await guild.create_text_channel("rm-logs", reason="RM automatic setup")
            created.append(log_channel.name)
        except discord.HTTPException:
            pass
    welcome = discord.utils.get(guild.text_channels, name="welcome")
    if not welcome:
        try:
            welcome = await guild.create_text_channel("welcome", reason="RM automatic setup")
            created.append(welcome.name)
        except discord.HTTPException:
            pass
    set_config(guild.id, "log_channel", log_channel.id if log_channel else None)
    set_config(guild.id, "modlog_channel", log_channel.id if log_channel else None)
    set_config(guild.id, "welcome_channel", welcome.id if welcome else None)
    set_config(guild.id, "welcome_enabled", 1)
    await msg.edit(embed=make_embed("✅ RM setup complete", f"**Logs:** {log_channel.mention if log_channel else 'Not created'}\n**Welcome:** {welcome.mention if welcome else 'Not created'}\n**Created:** {', '.join(created) if created else 'Nothing new'}\n\nUse `{PREFIX}verification setup` or `/verification setup` next.", PALETTE["success"]))


@bot.hybrid_command(name="config", description="View RM server configuration.")
@admin_only()
async def config(ctx):
    cfg = get_config(ctx.guild.id)
    modules = [("Invite filter", cfg["anti_invites"]), ("Link filter", cfg["anti_links"]), ("Word filter", cfg["anti_slurs"]), ("Spam shield", cfg["anti_spam"]), ("Caps filter", cfg["anti_caps"]), ("Mention shield", cfg["anti_mentions"]), ("Leveling", cfg["leveling"]), ("Welcome", cfg["welcome_enabled"]), ("Autorole", cfg["autorole_enabled"]), ("Verification", cfg["verification_enabled"]), ("Tickets", cfg["tickets_enabled"])]
    embed = make_embed("⚙️ RM Configuration")
    embed.add_field(name="Security", value="\n".join(f"{'🟢' if value else '🔴'} {name}" for name, value in modules[:6]), inline=True)
    embed.add_field(name="Community", value="\n".join(f"{'🟢' if value else '🔴'} {name}" for name, value in modules[6:]), inline=True)
    embed.add_field(name="Punishment", value=f"`{cfg['punishment']}` • `{cfg['timeout_seconds']}s` timeout", inline=False)
    await ctx.reply(embed=embed)


@bot.hybrid_group(name="automod", description="Manage AutoMod.")
@admin_only()
async def automod(ctx):
    if ctx.invoked_subcommand is None:
        await ctx.reply("Use `automod toggle <module> <true|false>` or `automod punishment <action>`.")


@automod.command(name="toggle", description="Toggle an AutoMod module.")
@admin_only()
async def automod_toggle(ctx, module: str, enabled: bool):
    mapping = {"links": "anti_links", "invites": "anti_invites", "slurs": "anti_slurs", "spam": "anti_spam", "caps": "anti_caps", "mentions": "anti_mentions"}
    key = mapping.get(module.lower())
    if not key:
        return await ctx.reply("Modules: `links`, `invites`, `slurs`, `spam`, `caps`, `mentions`.", ephemeral=True)
    set_config(ctx.guild.id, key, int(enabled))
    await ctx.reply(f"{'✅ Enabled' if enabled else '⛔ Disabled'} **{module.lower()}** protection.")


@automod.command(name="punishment", description="Set AutoMod punishment.")
@admin_only()
async def automod_punishment(ctx, action: str):
    action = action.lower()
    if action not in {"warn", "timeout", "kick", "ban"}:
        return await ctx.reply("Use `warn`, `timeout`, `kick`, or `ban`.", ephemeral=True)
    set_config(ctx.guild.id, "punishment", action)
    await ctx.reply(f"✅ AutoMod punishment set to `{action}`.")


@bot.hybrid_group(name="filter", description="Manage blocked words.")
@admin_only()
async def filter_cmd(ctx):
    if ctx.invoked_subcommand is None:
        await ctx.reply("Use `filter add`, `filter remove`, or `filter list`.")


@filter_cmd.command(name="add", description="Add a blocked word.")
@admin_only()
async def filter_add(ctx, *, word: str):
    word = word.strip().casefold()
    if not word or len(word) > 100:
        return await ctx.reply("Invalid word.", ephemeral=True)
    conn = db()
    conn.execute("INSERT OR IGNORE INTO bad_words(guild_id,word) VALUES(?,?)", (ctx.guild.id, word))
    conn.commit()
    conn.close()
    await ctx.reply(f"✅ Added `{word}` to the custom filter.")


@filter_cmd.command(name="remove", description="Remove a blocked word.")
@admin_only()
async def filter_remove(ctx, *, word: str):
    word = word.strip().casefold()
    conn = db()
    conn.execute("DELETE FROM bad_words WHERE guild_id=? AND word=?", (ctx.guild.id, word))
    conn.commit()
    conn.close()
    await ctx.reply(f"✅ Removed `{word}` from the custom filter.")


@filter_cmd.command(name="list", description="List blocked words.")
@admin_only()
async def filter_list(ctx):
    words = sorted(get_bad_words(ctx.guild.id) - DEFAULT_BAD_WORDS)
    await ctx.reply(embed=make_embed("🧹 Custom filter", "\n".join(f"• `{w}`" for w in words) or "No custom words."))


@bot.hybrid_command(name="setlog", description="Set the main log channel.")
@admin_only()
async def setlog(ctx, channel: discord.TextChannel):
    set_config(ctx.guild.id, "log_channel", channel.id)
    await ctx.reply(f"✅ Main logs → {channel.mention}")


@bot.hybrid_command(name="setmodlog", description="Set moderation logs.")
@admin_only()
async def setmodlog(ctx, channel: discord.TextChannel):
    set_config(ctx.guild.id, "modlog_channel", channel.id)
    await ctx.reply(f"✅ Moderation logs → {channel.mention}")


@bot.hybrid_group(name="verification", description="Configure member verification.")
@admin_only()
async def verification(ctx):
    if ctx.invoked_subcommand is None:
        await ctx.reply("Use `/verification setup`, `/verification disable`, or `/verification status`.")


@verification.command(name="setup", description="Create a verification panel.")
@admin_only()
async def verification_setup(ctx, channel: discord.TextChannel, role: discord.Role, unverified_role: discord.Role = None):
    if role >= ctx.guild.me.top_role:
        return await ctx.reply("Move my bot role above the verification role.", ephemeral=True)
    if unverified_role and unverified_role >= ctx.guild.me.top_role:
        return await ctx.reply("Move my bot role above the unverified role.", ephemeral=True)
    set_config(ctx.guild.id, "verify_channel", channel.id)
    set_config(ctx.guild.id, "verify_role", role.id)
    set_config(ctx.guild.id, "unverified_role", unverified_role.id if unverified_role else None)
    set_config(ctx.guild.id, "verification_enabled", 1)
    embed = make_embed("🔐 Server Verification", "Click the button below to verify yourself and unlock the server.\n\nAlready verified? You're good.")
    embed.set_footer(text="RM Verification • protected by RM")
    await channel.send(embed=embed, view=VerifyView())
    await ctx.reply(f"✅ Verification panel deployed in {channel.mention}.")


@verification.command(name="disable", description="Disable verification.")
@admin_only()
async def verification_disable(ctx):
    set_config(ctx.guild.id, "verification_enabled", 0)
    await ctx.reply("⛔ Verification disabled.")


@verification.command(name="status", description="View verification settings.")
@admin_only()
async def verification_status(ctx):
    cfg = get_config(ctx.guild.id)
    channel = f"<#{cfg['verify_channel']}>" if cfg["verify_channel"] else "Not set"
    role = f"<@&{cfg['verify_role']}>" if cfg["verify_role"] else "Not set"
    unverified = f"<@&{cfg['unverified_role']}>" if cfg["unverified_role"] else "Not set"
    await ctx.reply(embed=make_embed("🔐 Verification Status", f"**Enabled:** {'Yes' if cfg['verification_enabled'] else 'No'}\n**Channel:** {channel}\n**Verified role:** {role}\n**Unverified role:** {unverified}"))


@bot.hybrid_command(name="welcome", description="Configure the welcome system.")
@admin_only()
async def welcome_cmd(ctx, channel: discord.TextChannel, *, message="Welcome {member} to **{server}**!"):
    set_config(ctx.guild.id, "welcome_channel", channel.id)
    set_config(ctx.guild.id, "welcome_message", message[:1000])
    set_config(ctx.guild.id, "welcome_enabled", 1)
    await ctx.reply(embed=make_embed("👋 Welcome system enabled", f"**Channel:** {channel.mention}\n**Preview:** {message.replace('{member}', ctx.author.mention).replace('{server}', ctx.guild.name)}", PALETTE["success"]))


@bot.hybrid_command(name="autorole", description="Set the automatic member role.")
@admin_only()
async def autorole(ctx, role: discord.Role):
    if role >= ctx.guild.me.top_role:
        return await ctx.reply("Move my role above the autorole.", ephemeral=True)
    set_config(ctx.guild.id, "autorole_id", role.id)
    set_config(ctx.guild.id, "autorole_enabled", 1)
    await ctx.reply(f"✅ Autorole enabled → {role.mention}")


@bot.hybrid_group(name="tickets", description="Configure the ticket system.")
@admin_only()
async def tickets(ctx):
    if ctx.invoked_subcommand is None:
        await ctx.reply("Use `/tickets setup <channel> [category] [support role]` or `/tickets disable`.")


@tickets.command(name="setup", description="Create a ticket panel.")
@admin_only()
async def tickets_setup(ctx, channel: discord.TextChannel, category: discord.CategoryChannel = None, support_role: discord.Role = None):
    if support_role and support_role >= ctx.guild.me.top_role:
        return await ctx.reply("Move my bot role above the support role.", ephemeral=True)
    set_config(ctx.guild.id, "ticket_category", category.id if category else None)
    set_config(ctx.guild.id, "ticket_support_role", support_role.id if support_role else None)
    set_config(ctx.guild.id, "tickets_enabled", 1)
    embed = make_embed("🎫 Need help?", "Open a private support ticket with the button below.\nPlease include your issue and relevant details.")
    await channel.send(embed=embed, view=TicketView())
    await ctx.reply(f"✅ Ticket panel deployed in {channel.mention}.")


@tickets.command(name="disable", description="Disable tickets.")
@admin_only()
async def tickets_disable(ctx):
    set_config(ctx.guild.id, "tickets_enabled", 0)
    await ctx.reply("⛔ Ticket creation disabled.")


@bot.hybrid_command(name="reactionrole", description="Create a button role panel.")
@admin_only()
async def reactionrole(ctx, channel: discord.TextChannel, role: discord.Role, *, label="Get Role"):
    if role >= ctx.guild.me.top_role:
        return await ctx.reply("Move my bot role above the reaction role.", ephemeral=True)
    message = await channel.send(embed=make_embed("🎟️ Self Role", f"Click **{label}** to toggle {role.mention}."), view=ReactionRoleView(role.id))
    conn = db()
    conn.execute("INSERT OR REPLACE INTO reaction_roles(message_id,guild_id,channel_id,role_id,label,emoji) VALUES(?,?,?,?,?,?)", (message.id, ctx.guild.id, channel.id, role.id, label, "🎟️"))
    conn.commit()
    conn.close()
    await ctx.reply(f"✅ Role panel created in {channel.mention}.")


@bot.hybrid_command(name="rank", description="View your rank.")
async def rank(ctx, member: discord.Member = None):
    member = member or ctx.author
    row = get_level(ctx.guild.id, member.id)
    xp = int(row["xp"])
    level = int(row["level"])
    required = 100 + (level * 50)
    conn = db()
    position = conn.execute("SELECT COUNT(*)+1 AS pos FROM levels WHERE guild_id=? AND (level > ? OR (level = ? AND xp > ?))", (ctx.guild.id, level, level, xp)).fetchone()["pos"]
    conn.close()
    embed = make_embed(f"📈 {member.display_name}", f"**Level:** `{level}`\n**XP:** `{xp}/{required}`\n`{progress_bar(xp, required)}`\n\n**Server rank:** `#{position}`")
    embed.set_thumbnail(url=member.display_avatar.url)
    await ctx.reply(embed=embed)


@bot.hybrid_command(name="leaderboard", description="View the server XP leaderboard.")
async def leaderboard(ctx):
    conn = db()
    rows = conn.execute("SELECT user_id, xp, level FROM levels WHERE guild_id=? ORDER BY level DESC, xp DESC LIMIT 10", (ctx.guild.id,)).fetchall()
    conn.close()
    lines = []
    for i, row in enumerate(rows, start=1):
        user = ctx.guild.get_member(row["user_id"])
        name = user.display_name if user else f"User {row['user_id']}"
        lines.append(f"**{i}.** {name} — Level `{row['level']}` • `{row['xp']} XP`")
    await ctx.reply(embed=make_embed("🏆 XP Leaderboard", "\n".join(lines) or "Nobody has XP yet."))


@bot.hybrid_command(name="suggest", description="Send a server suggestion.")
async def suggest(ctx, *, content: str):
    cfg = get_config(ctx.guild.id)
    channel = ctx.guild.get_channel(cfg["suggestion_channel"]) if cfg["suggestion_channel"] else None
    if not isinstance(channel, discord.TextChannel):
        return await ctx.reply("Suggestions aren't configured yet.", ephemeral=True)
    conn = db()
    cur = conn.execute("INSERT INTO suggestions(guild_id,user_id,content,created_at) VALUES(?,?,?,?)", (ctx.guild.id, ctx.author.id, content[:1800], datetime.now(timezone.utc).isoformat()))
    suggestion_id = cur.lastrowid
    conn.commit()
    conn.close()
    embed = make_embed(f"💡 Suggestion #{suggestion_id}", content[:1800])
    embed.set_author(name=str(ctx.author), icon_url=ctx.author.display_avatar.url)
    embed.add_field(name="Status", value="🟡 Pending", inline=False)
    message = await channel.send(embed=embed)
    await message.add_reaction("✅")
    await message.add_reaction("❌")
    conn = db()
    conn.execute("UPDATE suggestions SET message_id=? WHERE id=?", (message.id, suggestion_id))
    conn.commit()
    conn.close()
    await ctx.reply(f"✅ Suggestion **#{suggestion_id}** submitted.", ephemeral=True)


@bot.hybrid_command(name="setsuggestions", description="Set the suggestion channel.")
@admin_only()
async def setsuggestions(ctx, channel: discord.TextChannel):
    set_config(ctx.guild.id, "suggestion_channel", channel.id)
    await ctx.reply(f"✅ Suggestions → {channel.mention}")


@bot.hybrid_command(name="userinfo", description="View member information.")
async def userinfo(ctx, member: discord.Member = None):
    member = member or ctx.author
    roles = [r.mention for r in member.roles[1:]][-8:]
    embed = make_embed(f"👤 {member}")
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="ID", value=f"`{member.id}`")
    embed.add_field(name="Joined", value=discord.utils.format_dt(member.joined_at, "R") if member.joined_at else "Unknown")
    embed.add_field(name="Created", value=discord.utils.format_dt(member.created_at, "R"))
    embed.add_field(name="Top role", value=member.top_role.mention)
    embed.add_field(name="Roles", value=" ".join(roles) if roles else "None", inline=False)
    await ctx.reply(embed=embed)


@bot.hybrid_command(name="serverinfo", description="View server information.")
async def serverinfo(ctx):
    guild = ctx.guild
    embed = make_embed(guild.name)
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.add_field(name="Members", value=str(guild.member_count))
    embed.add_field(name="Channels", value=str(len(guild.channels)))
    embed.add_field(name="Roles", value=str(len(guild.roles)))
    embed.add_field(name="Boosts", value=str(guild.premium_subscription_count))
    embed.add_field(name="Owner", value=guild.owner.mention if guild.owner else f"<@{guild.owner_id}>")
    await ctx.reply(embed=embed)


@bot.hybrid_command(name="case", description="View a moderation case.")
@mod_only()
async def case(ctx, case_id: int):
    conn = db()
    row = conn.execute("SELECT * FROM cases WHERE guild_id=? AND id=?", (ctx.guild.id, case_id)).fetchone()
    conn.close()
    if not row:
        return await ctx.reply("Case not found.", ephemeral=True)
    embed = make_embed(f"Case #{case_id}")
    embed.add_field(name="Action", value=row["action"])
    embed.add_field(name="User", value=f"<@{row['user_id']}>")
    embed.add_field(name="Moderator", value=f"<@{row['moderator_id']}>")
    embed.add_field(name="Reason", value=row["reason"] or "No reason")
    embed.add_field(name="Created", value=discord.utils.format_dt(datetime.fromisoformat(row["created_at"]), "F"))
    await ctx.reply(embed=embed)


@bot.hybrid_command(name="chatted", description="Start the private Chatted relay.")
async def chatted(ctx):
    if not ctx.guild:
        return await ctx.reply("Use this inside a server.")
    if not (ctx.author.id == ctx.guild.owner_id or await is_bot_owner(ctx.author)):
        return await ctx.reply("Only the server owner or bot owner can use this.", ephemeral=True)
    if ctx.author.id in chatted_sessions:
        return await ctx.reply("You already have a Chatted session.", ephemeral=True)
    try:
        await ctx.author.send(embed=make_embed("Chatted", f"Reply in DMs with a channel name, mention, or ID.\nExample: `{ctx.channel.name}`\nType `CANCEL` to stop."))
    except discord.Forbidden:
        return await ctx.reply("I can't DM you. Enable server DMs and try again.", ephemeral=True)
    def check(message):
        return message.author.id == ctx.author.id and message.guild is None
    try:
        target_msg = await bot.wait_for("message", check=check, timeout=120)
        raw = target_msg.content.strip()
        if raw.casefold() == "cancel":
            return await ctx.author.send("Chatted setup cancelled.")
        target = None
        match = re.fullmatch(r"<#(\d+)>", raw)
        if match:
            target = ctx.guild.get_channel(int(match.group(1)))
        elif raw.isdigit():
            target = ctx.guild.get_channel(int(raw))
        else:
            target = discord.utils.get(ctx.guild.text_channels, name=raw.lstrip("#"))
        if not isinstance(target, discord.TextChannel):
            return await ctx.author.send("I couldn't find that channel.")
        permissions = target.permissions_for(ctx.guild.me)
        if not permissions.view_channel or not permissions.send_messages:
            return await ctx.author.send("I can't send in that channel.")
        chatted_sessions[ctx.author.id] = {"guild_id": ctx.guild.id, "channel_id": target.id}
        await ctx.author.send(f"✅ Chatted live → {target.mention}\nType `DONE` to finish.")
        while True:
            incoming = await bot.wait_for("message", check=check, timeout=900)
            text = incoming.content.strip()
            if text.casefold() == "done":
                await ctx.author.send("Chatted session ended.")
                break
            if text.casefold() == "cancel":
                await ctx.author.send("Chatted session cancelled.")
                break
            files = []
            for attachment in incoming.attachments:
                try:
                    data = await attachment.read()
                    files.append(discord.File(io.BytesIO(data), filename=attachment.filename))
                except (discord.HTTPException, OSError):
                    pass
            if text or files:
                await target.send(content=incoming.content or None, files=files)
                try:
                    await incoming.add_reaction("✅")
                except discord.HTTPException:
                    pass
    except asyncio.TimeoutError:
        try:
            await ctx.author.send("Chatted timed out.")
        except discord.HTTPException:
            pass
    finally:
        chatted_sessions.pop(ctx.author.id, None)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CheckFailure):
        return await ctx.reply("🚫 You don't have permission to use that.", ephemeral=True)
    if isinstance(error, commands.MissingRequiredArgument):
        return await ctx.reply(f"Usage: `{PREFIX}{ctx.command.qualified_name} {ctx.command.signature}`", ephemeral=True)
    if isinstance(error, commands.BadArgument):
        return await ctx.reply("❌ Invalid member, role, channel, or number.", ephemeral=True)
    if isinstance(error, commands.CommandInvokeError) and isinstance(error.original, discord.Forbidden):
        return await ctx.reply("❌ Discord denied that action. Check my permissions and role position.", ephemeral=True)
    print(f"Command error in {getattr(ctx.command, 'qualified_name', 'unknown')}: {error!r}")
    try:
        await ctx.reply("❌ Something went wrong while running that command.", ephemeral=True)
    except discord.HTTPException:
        pass


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    print(f"Slash command error: {error!r}")
    message = "❌ Something went wrong. Check my permissions and try again."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        pass


# ==================== RM PLUS ====================

import os
import json
import re
import sys
import time
import asyncio
import shutil
import traceback
import subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict, deque

import discord
from discord.ext import commands, tasks

AI_OWNER_ID = 1394708156818391110
HF_TOKEN = os.getenv("HF_TOKEN", "").strip()
HF_MODEL = os.getenv("HF_MODEL", "openai/gpt-oss-120b:fastest")
HF_URL = os.getenv("HF_URL", "https://router.huggingface.co/v1/chat/completions")
AI_WORKSPACE = Path(os.getenv("RM_AI_WORKSPACE", ".")).resolve()
AI_LOG = AI_WORKSPACE / "rm_ai_runtime.log"
BACKUP_DIR = AI_WORKSPACE / "rm_backups"
BACKUP_DIR.mkdir(exist_ok=True)

invite_cache = {}
raid_buckets = defaultdict(deque)
ai_history = defaultdict(lambda: deque(maxlen=12))
pending_ai_actions = {}


def plus_db():
    conn = db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS rm_backups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rm_custom_commands (
            guild_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            response TEXT NOT NULL,
            owner_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            enabled INTEGER DEFAULT 1,
            PRIMARY KEY(guild_id, name)
        );
        CREATE TABLE IF NOT EXISTS rm_schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            run_at TEXT NOT NULL,
            repeat_seconds INTEGER DEFAULT 0,
            enabled INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS rm_temp_bans (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            expires_at TEXT NOT NULL,
            reason TEXT,
            PRIMARY KEY(guild_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS rm_temp_roles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rm_temp_channels (
            guild_id INTEGER NOT NULL,
            channel_id INTEGER PRIMARY KEY,
            expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rm_staff_activity (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            messages INTEGER DEFAULT 0,
            commands INTEGER DEFAULT 0,
            mod_actions INTEGER DEFAULT 0,
            last_active TEXT,
            PRIMARY KEY(guild_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS rm_member_analytics (
            guild_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            joins INTEGER DEFAULT 0,
            leaves INTEGER DEFAULT 0,
            messages INTEGER DEFAULT 0,
            PRIMARY KEY(guild_id, day)
        );
        CREATE TABLE IF NOT EXISTS rm_invites (
            guild_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            inviter_id INTEGER,
            uses INTEGER DEFAULT 0,
            PRIMARY KEY(guild_id, code)
        );
        CREATE TABLE IF NOT EXISTS rm_verification_questions (
            guild_id INTEGER PRIMARY KEY,
            question TEXT NOT NULL,
            expected TEXT DEFAULT '',
            enabled INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS rm_raid_mode (
            guild_id INTEGER PRIMARY KEY,
            enabled INTEGER DEFAULT 0,
            activated_at TEXT
        );
        """
    )
    conn.commit()
    conn.close()


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def parse_iso(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def plus_log(text):
    try:
        AI_LOG.parent.mkdir(parents=True, exist_ok=True)
        with AI_LOG.open("a", encoding="utf-8") as f:
            f.write(f"[{utcnow().isoformat()}] {text}\n")
    except OSError:
        pass


def owner_only(ctx):
    return getattr(ctx.author, "id", 0) == AI_OWNER_ID


def day_key():
    return utcnow().date().isoformat()


def inc_staff(guild_id, user_id, field, amount=1):
    allowed = {"messages", "commands", "mod_actions"}
    if field not in allowed:
        return
    conn = db()
    conn.execute("INSERT OR IGNORE INTO rm_staff_activity(guild_id,user_id) VALUES(?,?)", (guild_id, user_id))
    conn.execute(f"UPDATE rm_staff_activity SET {field}={field}+?, last_active=? WHERE guild_id=? AND user_id=?", (amount, iso(utcnow()), guild_id, user_id))
    conn.commit()
    conn.close()


def inc_analytics(guild_id, field, amount=1):
    if field not in {"joins", "leaves", "messages"}:
        return
    conn = db()
    conn.execute("INSERT OR IGNORE INTO rm_member_analytics(guild_id,day) VALUES(?,?)", (guild_id, day_key()))
    conn.execute(f"UPDATE rm_member_analytics SET {field}={field}+? WHERE guild_id=? AND day=?", (amount, guild_id, day_key()))
    conn.commit()
    conn.close()


def get_question(guild_id):
    conn = db()
    row = conn.execute("SELECT * FROM rm_verification_questions WHERE guild_id=? AND enabled=1", (guild_id,)).fetchone()
    conn.close()
    return row


class ConfirmView(discord.ui.View):
    def __init__(self, author_id, yes_callback):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.yes_callback = yes_callback
        self.done = False

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This confirmation isn't for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success, emoji="✓")
    async def confirm(self, interaction, button):
        if self.done:
            return
        self.done = True
        await self.yes_callback(interaction)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="×")
    async def cancel(self, interaction, button):
        self.done = True
        await interaction.response.edit_message(embed=make_embed("RM • Cancelled", "No changes were made.", PALETTE["warning"]), view=None)
        self.stop()


class ConfigView(discord.ui.View):
    def __init__(self, author_id, guild_id):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.guild_id = guild_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Open your own RM config panel.", ephemeral=True)
            return False
        return True

    async def refresh(self, interaction, title="RM • Control Panel"):
        cfg = get_config(self.guild_id)
        security = [("links", cfg["anti_links"]), ("invites", cfg["anti_invites"]), ("spam", cfg["anti_spam"]), ("mentions", cfg["anti_mentions"]), ("caps", cfg["anti_caps"])]
        community = [("welcome", cfg["welcome_enabled"]), ("leveling", cfg["leveling"]), ("autorole", cfg["autorole_enabled"]), ("verification", cfg["verification_enabled"]), ("tickets", cfg["tickets_enabled"])]
        description = "\n".join(f"{'🟢' if v else '⚫'} **{k}**" for k,v in security + community)
        embed = make_embed(title, description)
        embed.set_footer(text="RM Control Center • changes save instantly")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.select(placeholder="Toggle a RM module…", options=[
        discord.SelectOption(label="Links", value="anti_links", emoji="🔗"),
        discord.SelectOption(label="Invites", value="anti_invites", emoji="🛡️"),
        discord.SelectOption(label="Spam", value="anti_spam", emoji="⚡"),
        discord.SelectOption(label="Mentions", value="anti_mentions", emoji="@"),
        discord.SelectOption(label="Caps", value="anti_caps", emoji="🔠"),
        discord.SelectOption(label="Welcome", value="welcome_enabled", emoji="👋"),
        discord.SelectOption(label="Leveling", value="leveling", emoji="📈"),
        discord.SelectOption(label="Autorole", value="autorole_enabled", emoji="🎭"),
        discord.SelectOption(label="Verification", value="verification_enabled", emoji="🔐"),
        discord.SelectOption(label="Tickets", value="tickets_enabled", emoji="🎫"),
    ])
    async def select_module(self, interaction, select):
        key = select.values[0]
        cfg = get_config(self.guild_id)
        new_value = 0 if int(cfg[key]) else 1
        set_config(self.guild_id, key, new_value)
        await self.refresh(interaction, f"RM • {'Enabled' if new_value else 'Disabled'} {key}")

    @discord.ui.button(label="Reload", style=discord.ButtonStyle.secondary, emoji="↻")
    async def reload(self, interaction, button):
        await self.refresh(interaction)

    @discord.ui.button(label="Raid Mode", style=discord.ButtonStyle.danger, emoji="⚠")
    async def raid(self, interaction, button):
        await interaction.response.send_message("Use the Raid Mode button only when you intentionally want to lock the server.", ephemeral=True)


async def create_full_setup(guild):
    created = []
    category = discord.utils.get(guild.categories, name="RM・CONTROL")
    if not category:
        category = await guild.create_category("RM・CONTROL", reason="RM full setup")
        created.append(category.name)
    text_names = ["rm-logs", "rm-modlogs", "welcome", "verify", "suggestions"]
    channels = {}
    for name in text_names:
        channel = discord.utils.get(guild.text_channels, name=name)
        if not channel:
            channel = await guild.create_text_channel(name, category=category, reason="RM full setup")
            created.append(channel.name)
        channels[name] = channel
    ticket_category = discord.utils.get(guild.categories, name="RM・TICKETS")
    if not ticket_category:
        ticket_category = await guild.create_category("RM・TICKETS", reason="RM full setup")
        created.append(ticket_category.name)

    verified = discord.utils.get(guild.roles, name="RM Verified")
    if not verified:
        verified = await guild.create_role(name="RM Verified", reason="RM full setup")
        created.append(verified.name)
    unverified = discord.utils.get(guild.roles, name="RM Unverified")
    if not unverified:
        unverified = await guild.create_role(name="RM Unverified", reason="RM full setup")
        created.append(unverified.name)

    set_config(guild.id, "log_channel", channels["rm-logs"].id)
    set_config(guild.id, "modlog_channel", channels["rm-modlogs"].id)
    set_config(guild.id, "welcome_channel", channels["welcome"].id)
    set_config(guild.id, "verify_channel", channels["verify"].id)
    set_config(guild.id, "verify_role", verified.id)
    set_config(guild.id, "unverified_role", unverified.id)
    set_config(guild.id, "suggestion_channel", channels["suggestions"].id)
    set_config(guild.id, "ticket_category", ticket_category.id)
    for key in ["anti_links", "anti_invites", "anti_slurs", "anti_spam", "anti_mentions", "leveling", "welcome_enabled", "verification_enabled", "tickets_enabled"]:
        set_config(guild.id, key, 1)

    try:
        await channels["verify"].send(embed=make_embed("🔐 RM Verification", "Verify below to unlock the server.\n\nRM can require a verification question if the owner enables one."), view=VerifyView())
    except discord.HTTPException:
        pass
    try:
        await channels["welcome"].send(embed=make_embed("👋 RM is ready", "Your server is now running the RM control stack."))
    except discord.HTTPException:
        pass
    return created, channels, verified, unverified, ticket_category


async def full_setup(ctx):
    steps = ["Scanning server permissions", "Building RM control structure", "Creating security channels", "Configuring verification", "Enabling protection", "Finalizing RM Control Center"]
    msg = await animated_reply(ctx.reply, "RM • Full Server Deployment", steps)
    try:
        created, channels, verified, unverified, ticket_category = await create_full_setup(ctx.guild)
        desc = (
            f"**Status**  `DEPLOYED`\n"
            f"**Security**  🟢 enabled\n"
            f"**Verification**  {channels['verify'].mention}\n"
            f"**Logs**  {channels['rm-logs'].mention}\n"
            f"**Mod Logs**  {channels['rm-modlogs'].mention}\n"
            f"**Welcome**  {channels['welcome'].mention}\n"
            f"**Tickets**  `{ticket_category.name}`\n\n"
            f"**Created:** {', '.join(created) if created else 'Nothing new'}"
        )
        await msg.edit(embed=make_embed("RM • Deployment Complete", desc, PALETTE["success"]))
    except discord.Forbidden:
        await msg.edit(embed=make_embed("RM • Setup Blocked", "I need Manage Channels + Manage Roles to complete the full deployment.", PALETTE["danger"]))
    except discord.HTTPException as exc:
        await msg.edit(embed=make_embed("RM • Setup Error", f"Discord rejected part of the deployment.\n`{exc}`", PALETTE["danger"]))


async def backup_guild(guild, name):
    roles = []
    for role in guild.roles:
        if role.is_default() or role.managed:
            continue
        roles.append({
            "name": role.name, "permissions": role.permissions.value, "color": role.color.value,
            "hoist": role.hoist, "mentionable": role.mentionable, "position": role.position
        })
    channels = []
    for channel in guild.channels:
        data = {
            "name": channel.name, "type": channel.__class__.__name__,
            "position": channel.position, "category": channel.category.name if channel.category else None,
        }
        if isinstance(channel, discord.TextChannel):
            data.update({"topic": channel.topic, "slowmode": channel.slowmode_delay, "nsfw": channel.nsfw})
        if isinstance(channel, discord.VoiceChannel):
            data.update({"bitrate": channel.bitrate, "user_limit": channel.user_limit})
        channels.append(data)
    payload = {"guild": {"name": guild.name, "verification_level": guild.verification_level.value, "default_notifications": guild.default_notifications.value}, "roles": roles, "channels": channels}
    conn = db()
    cur = conn.execute("INSERT INTO rm_backups(guild_id,name,data,created_at) VALUES(?,?,?,?)", (guild.id, name[:80], json.dumps(payload), iso(utcnow())))
    conn.commit()
    backup_id = cur.lastrowid
    conn.close()
    try:
        (BACKUP_DIR / f"{guild.id}-{backup_id}-{re.sub(r'[^a-zA-Z0-9._-]+','_',name[:40])}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        pass
    return backup_id, payload


async def restore_guild(guild, payload):
    role_map = {}
    for item in payload.get("roles", []):
        role = discord.utils.get(guild.roles, name=item["name"])
        if not role:
            try:
                role = await guild.create_role(name=item["name"], permissions=discord.Permissions(item["permissions"]), color=discord.Color(item["color"]), hoist=item["hoist"], mentionable=item["mentionable"], reason="RM backup restore")
            except discord.HTTPException:
                continue
        role_map[item["name"]] = role
    made = 0
    categories = [c for c in payload.get("channels", []) if c["type"] == "CategoryChannel"]
    others = [c for c in payload.get("channels", []) if c["type"] != "CategoryChannel"]
    category_map = {}
    for item in categories:
        cat = discord.utils.get(guild.categories, name=item["name"])
        if not cat:
            try:
                cat = await guild.create_category(item["name"], reason="RM backup restore")
                made += 1
            except discord.HTTPException:
                continue
        category_map[item["name"]] = cat
    for item in others:
        if discord.utils.get(guild.channels, name=item["name"]):
            continue
        cat = category_map.get(item.get("category"))
        try:
            if item["type"] == "TextChannel":
                await guild.create_text_channel(item["name"], category=cat, topic=item.get("topic"), slowmode_delay=item.get("slowmode", 0), nsfw=item.get("nsfw", False), reason="RM backup restore")
            elif item["type"] == "VoiceChannel":
                await guild.create_voice_channel(item["name"], category=cat, bitrate=item.get("bitrate"), user_limit=item.get("user_limit", 0), reason="RM backup restore")
            else:
                continue
            made += 1
        except discord.HTTPException:
            pass
    return made


class EmbedBuilderModal(discord.ui.Modal, title="RM • Embed Builder"):
    title_input = discord.ui.TextInput(label="Title", max_length=256)
    description = discord.ui.TextInput(label="Description", style=discord.TextStyle.paragraph, max_length=4000)
    color = discord.ui.TextInput(label="Color hex (optional)", placeholder="#5865F2", required=False, max_length=7)
    footer = discord.ui.TextInput(label="Footer (optional)", required=False, max_length=200)

    def __init__(self, channel):
        super().__init__()
        self.channel = channel

    async def on_submit(self, interaction):
        raw = self.color.value.strip().lstrip("#") or "5865F2"
        try:
            c = discord.Color(int(raw, 16))
        except ValueError:
            c = PALETTE["main"]
        embed = discord.Embed(title=self.title_input.value, description=self.description.value, color=c)
        if self.footer.value:
            embed.set_footer(text=self.footer.value)
        await self.channel.send(embed=embed)
        await interaction.response.send_message("✅ Embed deployed.", ephemeral=True)


@bot.hybrid_command(name="embedbuilder", description="Open the RM interactive embed builder.")
@admin_only()
async def embedbuilder(ctx, channel: discord.TextChannel = None):
    channel = channel or ctx.channel
    button = discord.ui.Button(label="Open Embed Builder", style=discord.ButtonStyle.primary, emoji="✦")
    view = discord.ui.View(timeout=120)
    async def open_modal(interaction):
        if interaction.user.id != ctx.author.id:
            return await interaction.response.send_message("This builder belongs to the person who opened it.", ephemeral=True)
        await interaction.response.send_modal(EmbedBuilderModal(channel))
    button.callback = open_modal
    view.add_item(button)
    await ctx.reply(embed=make_embed("RM • Embed Studio", f"Target: {channel.mention}\n\nPress the button to build a custom embed."), view=view)


@bot.hybrid_group(name="server", description="RM server backup and restore tools.")
@admin_only()
async def server_group(ctx):
    if ctx.invoked_subcommand is None:
        await ctx.reply("Use `server backup`, `server backups`, or `server restore <id>`. ")


@server_group.command(name="backup", description="Save the server configuration.")
@admin_only()
async def server_backup(ctx, *, name="manual"):
    await ctx.defer()
    try:
        backup_id, payload = await backup_guild(ctx.guild, name)
        await ctx.followup.send(embed=make_embed("RM • Backup Saved", f"**Backup:** `#{backup_id}`\n**Name:** `{name}`\n**Roles:** `{len(payload['roles'])}`\n**Channels:** `{len(payload['channels'])}`\n\nThis snapshot is stored in RM's database.", PALETTE["success"]))
    except Exception as exc:
        plus_log(traceback.format_exc())
        await ctx.followup.send(embed=make_embed("RM • Backup Error", f"`{exc}`", PALETTE["danger"]), ephemeral=True)


@server_group.command(name="backups", description="List saved server backups.")
@admin_only()
async def server_backups(ctx):
    conn = db()
    rows = conn.execute("SELECT id,name,created_at FROM rm_backups WHERE guild_id=? ORDER BY id DESC LIMIT 20", (ctx.guild.id,)).fetchall()
    conn.close()
    lines = [f"`#{r['id']}` • **{r['name']}** • {discord.utils.format_dt(parse_iso(r['created_at']), 'R')}" for r in rows]
    await ctx.reply(embed=make_embed("RM • Saved Backups", "\n".join(lines) or "No backups yet."))


@server_group.command(name="restore", description="Restore a saved server configuration.")
@admin_only()
async def server_restore(ctx, backup_id: int):
    conn = db()
    row = conn.execute("SELECT * FROM rm_backups WHERE guild_id=? AND id=?", (ctx.guild.id, backup_id)).fetchone()
    conn.close()
    if not row:
        return await ctx.reply("Backup not found.", ephemeral=True)
    payload = json.loads(row["data"])
    async def yes(interaction):
        await interaction.response.defer()
        made = await restore_guild(ctx.guild, payload)
        await interaction.followup.send(embed=make_embed("RM • Restore Complete", f"Backup `#{backup_id}` restored.\n**Created:** `{made}` missing objects.", PALETTE["success"]), ephemeral=True)
    await ctx.reply(embed=make_embed("RM • Restore Confirmation", "Restore creates missing roles/categories/channels. Existing objects are not deleted."), view=ConfirmView(ctx.author.id, yes), ephemeral=True)


@bot.hybrid_group(name="custom", description="Build RM custom server systems.")
@admin_only()
async def custom_group(ctx):
    if ctx.invoked_subcommand is None:
        await ctx.reply("Use `custom command create/list/delete`. Use `/embedbuilder` for the visual embed studio.")


@custom_group.command(name="command", description="Create, list, or delete a custom command.")
@admin_only()
async def custom_command(ctx, action: str, name: str = None, *, response: str = None):
    action = action.lower()
    if action == "list":
        conn = db()
        rows = conn.execute("SELECT name FROM rm_custom_commands WHERE guild_id=? AND enabled=1 ORDER BY name", (ctx.guild.id,)).fetchall()
        conn.close()
        return await ctx.reply(embed=make_embed("RM • Custom Commands", "\n".join(f"`{r['name']}`" for r in rows) or "None yet."))
    if not name:
        return await ctx.reply("Give the custom command name.", ephemeral=True)
    name = re.sub(r"[^a-z0-9_-]", "", name.lower())[:32]
    if action == "delete":
        conn = db(); conn.execute("DELETE FROM rm_custom_commands WHERE guild_id=? AND name=?", (ctx.guild.id, name)); conn.commit(); conn.close()
        return await ctx.reply(f"✅ Deleted `{name}`.")
    if action == "create":
        if not response:
            return await ctx.reply("Give a response after the command name.", ephemeral=True)
        conn = db(); conn.execute("INSERT OR REPLACE INTO rm_custom_commands(guild_id,name,response,owner_id,created_at,enabled) VALUES(?,?,?,?,?,1)", (ctx.guild.id,name,response[:1900],ctx.author.id,iso(utcnow()))); conn.commit(); conn.close()
        return await ctx.reply(embed=make_embed("✅ Custom command created", f"**Trigger:** `{PREFIX}{name}`\n**Response:** {response[:1000]}", PALETTE["success"]))
    await ctx.reply("Actions: `create`, `list`, `delete`.", ephemeral=True)


@bot.hybrid_command(name="schedule", description="Schedule an announcement in a channel.")
@admin_only()
async def schedule(ctx, channel: discord.TextChannel, minutes: float, *, content: str):
    if minutes <= 0 or minutes > 525600:
        return await ctx.reply("Minutes must be between `0.1` and `525600`.", ephemeral=True)
    run_at = utcnow() + timedelta(minutes=minutes)
    conn = db(); cur = conn.execute("INSERT INTO rm_schedules(guild_id,channel_id,content,run_at) VALUES(?,?,?,?)", (ctx.guild.id,channel.id,content[:1900],iso(run_at))); conn.commit(); schedule_id=cur.lastrowid; conn.close()
    await ctx.reply(embed=make_embed("🗓️ RM • Announcement Scheduled", f"**ID:** `#{schedule_id}`\n**Channel:** {channel.mention}\n**Runs:** {discord.utils.format_dt(run_at,'R')}\n\n`{content[:500]}`", PALETTE["success"]))


@bot.hybrid_command(name="tempban", description="Temporarily ban a member.")
@mod_only()
async def tempban(ctx, member: discord.Member, minutes: int, *, reason="No reason provided"):
    if minutes < 1 or minutes > 525600:
        return await ctx.reply("Minutes must be between `1` and `525600`.", ephemeral=True)
    if member.id in {ctx.guild.owner_id, bot.user.id} or member.top_role >= ctx.guild.me.top_role:
        return await ctx.reply("I can't temp-ban that member.", ephemeral=True)
    expires = utcnow() + timedelta(minutes=minutes)
    await member.ban(reason=f"RM temporary ban: {reason}")
    conn=db(); conn.execute("INSERT OR REPLACE INTO rm_temp_bans(guild_id,user_id,expires_at,reason) VALUES(?,?,?,?)", (ctx.guild.id,member.id,iso(expires),reason[:500])); conn.commit(); conn.close()
    await ctx.reply(embed=make_embed("⏳ Temporary Ban", f"**User:** {member}\n**Expires:** {discord.utils.format_dt(expires,'R')}\n**Reason:** {reason}", PALETTE["warning"]))


@bot.hybrid_command(name="temprole", description="Temporarily give a role to a member.")
@mod_only()
async def temprole(ctx, member: discord.Member, role: discord.Role, minutes: int):
    if role >= ctx.guild.me.top_role or minutes < 1:
        return await ctx.reply("Check the role hierarchy and duration.", ephemeral=True)
    expires = utcnow() + timedelta(minutes=minutes)
    await member.add_roles(role, reason="RM temporary role")
    conn=db(); conn.execute("INSERT INTO rm_temp_roles(guild_id,user_id,role_id,expires_at) VALUES(?,?,?,?)", (ctx.guild.id,member.id,role.id,iso(expires))); conn.commit(); conn.close()
    await ctx.reply(f"✅ {role.mention} → {member.mention} until {discord.utils.format_dt(expires,'R')}.")


@bot.hybrid_command(name="tempchannel", description="Create a temporary text channel.")
@admin_only()
async def tempchannel(ctx, name: str, minutes: int):
    if minutes < 1 or minutes > 43200:
        return await ctx.reply("Minutes must be between `1` and `43200`.", ephemeral=True)
    channel = await ctx.guild.create_text_channel(f"tmp-{re.sub(r'[^a-z0-9-]','-',name.lower())[:80]}", reason="RM temporary channel")
    expires = utcnow() + timedelta(minutes=minutes)
    conn=db(); conn.execute("INSERT OR REPLACE INTO rm_temp_channels(guild_id,channel_id,expires_at) VALUES(?,?,?)", (ctx.guild.id,channel.id,iso(expires))); conn.commit(); conn.close()
    await ctx.reply(f"✅ Created {channel.mention} — expires {discord.utils.format_dt(expires,'R')}.")


@bot.hybrid_command(name="staffactivity", description="View staff activity statistics.")
@admin_only()
async def staffactivity(ctx, member: discord.Member = None):
    conn=db()
    if member:
        rows=conn.execute("SELECT * FROM rm_staff_activity WHERE guild_id=? AND user_id=?", (ctx.guild.id,member.id)).fetchall()
    else:
        rows=conn.execute("SELECT * FROM rm_staff_activity WHERE guild_id=? ORDER BY (messages+commands+mod_actions) DESC LIMIT 10", (ctx.guild.id,)).fetchall()
    conn.close()
    if member:
        r=rows[0] if rows else None
        text=f"**Staff:** {member.mention}\n**Messages:** `{r['messages'] if r else 0}`\n**Commands:** `{r['commands'] if r else 0}`\n**Mod actions:** `{r['mod_actions'] if r else 0}`\n**Last active:** {discord.utils.format_dt(parse_iso(r['last_active']),'R') if r and r['last_active'] else 'Never'}"
    else:
        text="\n".join(f"**{i}.** <@{r['user_id']}> • `{r['messages']}` msgs • `{r['commands']}` cmds • `{r['mod_actions']}` mod" for i,r in enumerate(rows,1)) or "No activity recorded yet."
    await ctx.reply(embed=make_embed("RM • Staff Activity", text))


@bot.hybrid_command(name="analytics", description="View join/leave and message analytics.")
@admin_only()
async def analytics(ctx, days: int = 7):
    days=max(1,min(days,31))
    since=(utcnow()-timedelta(days=days-1)).date().isoformat()
    conn=db(); rows=conn.execute("SELECT day,joins,leaves,messages FROM rm_member_analytics WHERE guild_id=? AND day>=? ORDER BY day DESC", (ctx.guild.id,since)).fetchall(); conn.close()
    joins=sum(r['joins'] for r in rows); leaves=sum(r['leaves'] for r in rows); messages=sum(r['messages'] for r in rows)
    lines=[f"`{r['day']}`  +{r['joins']} / -{r['leaves']} / `{r['messages']}` msgs" for r in rows[:10]]
    await ctx.reply(embed=make_embed(f"RM • Analytics • {days}d", f"**Joins:** `{joins}`\n**Leaves:** `{leaves}`\n**Messages:** `{messages}`\n**Net:** `{joins-leaves}`\n\n"+"\n".join(lines)))


@bot.hybrid_command(name="invites", description="View the invite leaderboard.")
@admin_only()
async def invites(ctx):
    conn=db(); rows=conn.execute("SELECT inviter_id,SUM(uses) total FROM rm_invites WHERE guild_id=? GROUP BY inviter_id ORDER BY total DESC LIMIT 10", (ctx.guild.id,)).fetchall(); conn.close()
    lines=[f"**{i}.** <@{r['inviter_id']}> — `{r['total']}` invites" for i,r in enumerate(rows,1) if r['inviter_id']]
    await ctx.reply(embed=make_embed("RM • Invite Leaderboard", "\n".join(lines) or "No invite data yet."))


@bot.hybrid_command(name="verifyquestions", description="Set a member verification question.")
@admin_only()
async def verifyquestions(ctx, action: str, *, value: str = ""):
    action=action.lower()
    if action == "set":
        parts=value.split("||",1)
        question=parts[0].strip()[:500]
        expected=parts[1].strip().casefold()[:200] if len(parts)>1 else ""
        conn=db(); conn.execute("INSERT OR REPLACE INTO rm_verification_questions(guild_id,question,expected,enabled) VALUES(?,?,?,1)", (ctx.guild.id,question,expected)); conn.commit(); conn.close()
        return await ctx.reply(embed=make_embed("🔐 Verification Question Set", f"**Question:** {question}\n**Expected answer:** {'Configured' if expected else 'Any non-empty answer'}", PALETTE["success"]))
    if action == "off":
        conn=db(); conn.execute("UPDATE rm_verification_questions SET enabled=0 WHERE guild_id=?", (ctx.guild.id,)); conn.commit(); conn.close(); return await ctx.reply("✅ Verification question disabled.")
    row=get_question(ctx.guild.id)
    await ctx.reply(embed=make_embed("🔐 Verification Question", row['question'] if row else "Not configured."))


async def apply_raid_mode(guild, enabled=True):
    conn=db(); conn.execute("INSERT OR REPLACE INTO rm_raid_mode(guild_id,enabled,activated_at) VALUES(?,?,?)", (guild.id,int(enabled),iso(utcnow()) if enabled else None)); conn.commit(); conn.close()
    for channel in guild.text_channels:
        try:
            overwrite=channel.overwrites_for(guild.default_role)
            overwrite.send_messages=False if enabled else None
            await channel.set_permissions(guild.default_role, overwrite=overwrite, reason="RM Raid Mode")
        except discord.HTTPException:
            pass
    return enabled


@bot.hybrid_command(name="raidmode", description="Lock or unlock the server against raids.")
@admin_only()
async def raidmode(ctx, state: str="on"):
    enabled=state.lower() in {"on","enable","enabled","lock"}
    await apply_raid_mode(ctx.guild,enabled)
    await ctx.reply(embed=make_embed(f"RM • Raid Mode {'ACTIVE' if enabled else 'OFFLINE'}", "The server channels are locked for new traffic." if enabled else "Normal channel permissions restored where RM controls them.", PALETTE["danger"] if enabled else PALETTE["success"]))


async def scheduled_worker():
    plus_db()
    while True:
        try:
            now=utcnow()
            conn=db(); rows=conn.execute("SELECT * FROM rm_schedules WHERE enabled=1 AND run_at<=?", (iso(now),)).fetchall()
            conn.close()
            for row in rows:
                guild=bot.get_guild(row['guild_id']); channel=guild.get_channel(row['channel_id']) if guild else None
                if channel:
                    try: await channel.send(row['content'])
                    except discord.HTTPException: pass
                conn=db()
                if int(row['repeat_seconds'] or 0)>0:
                    conn.execute("UPDATE rm_schedules SET run_at=? WHERE id=?", (iso(now+timedelta(seconds=int(row['repeat_seconds']))),row['id']))
                else:
                    conn.execute("UPDATE rm_schedules SET enabled=0 WHERE id=?", (row['id'],))
                conn.commit(); conn.close()
            await expire_temporary_items(now)
        except Exception:
            plus_log(traceback.format_exc())
        await asyncio.sleep(15)


async def expire_temporary_items(now):
    conn=db(); bans=conn.execute("SELECT * FROM rm_temp_bans WHERE expires_at<=?", (iso(now),)).fetchall(); roles=conn.execute("SELECT * FROM rm_temp_roles WHERE expires_at<=?", (iso(now),)).fetchall(); channels=conn.execute("SELECT * FROM rm_temp_channels WHERE expires_at<=?", (iso(now),)).fetchall(); conn.close()
    for row in bans:
        guild=bot.get_guild(row['guild_id'])
        if guild:
            try: await guild.unban(discord.Object(id=int(row['user_id'])), reason="RM temporary ban expired")
            except discord.HTTPException: pass
        conn=db(); conn.execute("DELETE FROM rm_temp_bans WHERE guild_id=? AND user_id=?",(row['guild_id'],row['user_id'])); conn.commit(); conn.close()
    for row in roles:
        guild=bot.get_guild(row['guild_id']); member=guild.get_member(row['user_id']) if guild else None; role=guild.get_role(row['role_id']) if guild else None
        if member and role:
            try: await member.remove_roles(role,reason="RM temporary role expired")
            except discord.HTTPException: pass
        conn=db(); conn.execute("DELETE FROM rm_temp_roles WHERE id=?",(row['id'],)); conn.commit(); conn.close()
    for row in channels:
        guild=bot.get_guild(row['guild_id']); channel=guild.get_channel(row['channel_id']) if guild else None
        if channel:
            try: await channel.delete(reason="RM temporary channel expired")
            except discord.HTTPException: pass
        conn=db(); conn.execute("DELETE FROM rm_temp_channels WHERE channel_id=?",(row['channel_id'],)); conn.commit(); conn.close()


async def auto_raid_check(member):
    guild=member.guild
    now=time.monotonic(); bucket=raid_buckets[guild.id]; bucket.append(now)
    while bucket and now-bucket[0]>15: bucket.popleft()
    if len(bucket)>=int(os.getenv("RM_RAID_JOIN_THRESHOLD","8")):
        conn=db(); row=conn.execute("SELECT enabled FROM rm_raid_mode WHERE guild_id=?",(guild.id,)).fetchone(); conn.close()
        if not row or not row['enabled']:
            await apply_raid_mode(guild,True)
            try: await guild.system_channel.send(embed=make_embed("⚠ RM • RAID SHIELD ACTIVATED", f"RM detected `{len(bucket)}` joins in 15 seconds and locked server channels.", PALETTE['danger']))
            except discord.HTTPException: pass


def discover_invites(guild):
    return asyncio.create_task(_discover_invites(guild))


async def _discover_invites(guild):
    try:
        invites=await guild.invites()
    except discord.HTTPException:
        return
    cache={i.code:(i.uses or 0, i.inviter.id if i.inviter else None) for i in invites}
    invite_cache[guild.id]=cache
    conn=db()
    for code,(uses,inviter) in cache.items():
        conn.execute("INSERT OR REPLACE INTO rm_invites(guild_id,code,inviter_id,uses) VALUES(?,?,?,?)",(guild.id,code,inviter,uses))
    conn.commit(); conn.close()


async def handle_join(member):
    inc_analytics(member.guild.id,"joins")
    await auto_raid_check(member)
    try:
        new=await member.guild.invites()
    except discord.HTTPException:
        new=[]
    old=invite_cache.get(member.guild.id,{})
    chosen=None
    for inv in new:
        old_uses=old.get(inv.code,(0,None))[0]
        if (inv.uses or 0)>old_uses:
            chosen=inv; break
    invite_cache[member.guild.id]={i.code:((i.uses or 0),i.inviter.id if i.inviter else None) for i in new}
    if chosen and chosen.inviter:
        conn=db(); conn.execute("INSERT OR REPLACE INTO rm_invites(guild_id,code,inviter_id,uses) VALUES(?,?,?,?)",(member.guild.id,chosen.code,chosen.inviter.id,chosen.uses or 0)); conn.commit(); conn.close()


@bot.listen("on_ready")
async def plus_ready():
    plus_db()
    try:
        for guild in bot.guilds:
            await _discover_invites(guild)
    except Exception:
        plus_log(traceback.format_exc())
    if not getattr(plus_ready, "worker_started", False):
        plus_ready.worker_started=True
        bot.loop.create_task(scheduled_worker())
        bot.loop.create_task(ai_watchdog())


@bot.listen("on_member_join")
async def plus_member_join(member):
    try: await handle_join(member)
    except Exception: plus_log(traceback.format_exc())


@bot.listen("on_member_remove")
async def plus_member_remove(member):
    try: inc_analytics(member.guild.id,"leaves")
    except Exception: plus_log(traceback.format_exc())


@bot.listen("on_message")
async def plus_message(message):
    try:
        if message.guild:
            inc_analytics(message.guild.id,"messages")
            if isinstance(message.author, discord.Member) and (message.author.guild_permissions.manage_messages or message.author.guild_permissions.manage_guild or message.author.guild_permissions.administrator):
                inc_staff(message.guild.id,message.author.id,"messages")
        if message.guild and not message.author.bot:
            conn=db(); row=conn.execute("SELECT response FROM rm_custom_commands WHERE guild_id=? AND name=? AND enabled=1",(message.guild.id,message.content.strip().split()[0].removeprefix(PREFIX).lower() if message.content.strip() else "__none__")).fetchone(); conn.close()
            if row and message.content.strip().startswith(PREFIX):
                text=message.content.strip().split(maxsplit=1)[0].removeprefix(PREFIX).lower()
                if text and message.content.strip().split(maxsplit=1)[0].startswith(PREFIX):
                    response=row['response'].replace("{user}",message.author.mention).replace("{server}",message.guild.name).replace("{channel}",message.channel.mention)
                    await message.channel.send(response)
    except Exception:
        plus_log(traceback.format_exc())


@bot.listen("on_command_completion")
async def plus_command_complete(ctx):
    try:
        if ctx.guild and isinstance(ctx.author, discord.Member) and (ctx.author.guild_permissions.manage_messages or ctx.author.guild_permissions.manage_guild or ctx.author.guild_permissions.administrator):
            inc_staff(ctx.guild.id,ctx.author.id,"commands")
    except Exception: pass


async def hf_chat(messages):
    if not HF_TOKEN:
        raise RuntimeError("HF_TOKEN is not configured.")
    if not hasattr(bot, "_rm_http") or bot._rm_http.closed:
        import aiohttp
        bot._rm_http=aiohttp.ClientSession()
    payload={"model":HF_MODEL,"messages":messages,"temperature":0.2,"max_tokens":1800}
    async with bot._rm_http.post(HF_URL,headers={"Authorization":f"Bearer {HF_TOKEN}","Content-Type":"application/json"},json=payload,timeout=90) as resp:
        text=await resp.text()
        if resp.status>=400: raise RuntimeError(f"Hugging Face HTTP {resp.status}: {text[:500]}")
        data=json.loads(text)
        return data["choices"][0]["message"]["content"]


AI_SYSTEM="""You are RM Core, a private Discord bot engineering and server-operations agent. Only the bot owner can access you. Be concise, practical, and cautious. Never expose secrets. Before any code or server change, assess impact. For code changes, return a JSON object with: summary, risk ('low'|'medium'|'high'), needs_confirmation (boolean), actions (array). Each action is one of: inspect {path}, replace {path,find,replace}, append {path,content}, command {cmd} (only py_compile/git status/pwd allowed). Only use files inside the RM workspace. Prefer small reversible changes. Never edit .env or secrets. Never delete files. Never invent successful execution. If asked to make a feature, produce the smallest working patch and include a validation plan. Do not reveal private chain-of-thought; give a short rationale instead."""


def allowed_path(path):
    p=(AI_WORKSPACE / path).resolve()
    try: p.relative_to(AI_WORKSPACE)
    except ValueError: return None
    if p.name==".env" or p.suffix in {".db", ".sqlite", ".sqlite3"}: return None
    if p.name.startswith(".env") and p.name not in {".env.example"}: return None
    return p


def run_validation():
    py_files=[str(p) for p in AI_WORKSPACE.glob("*.py") if p.is_file()]
    if not py_files: return True,"No Python files found."
    proc=subprocess.run([sys.executable,"-m","py_compile",*py_files],cwd=str(AI_WORKSPACE),capture_output=True,text=True,timeout=45)
    return proc.returncode==0,(proc.stdout+proc.stderr).strip()[:4000]


def apply_actions(actions):
    backups=[]
    touched=[]
    try:
        for action in actions:
            typ=action.get("type")
            if typ=="inspect":
                continue
            path=allowed_path(action.get("path",""))
            if not path: raise ValueError("AI tried to access a protected or invalid path.")
            if not path.exists() and typ=="replace": raise ValueError(f"File does not exist: {path.name}")
            if path.exists():
                backup=path.with_suffix(path.suffix+f".rm-ai-{int(time.time()*1000)}.bak")
                shutil.copy2(path,backup); backups.append((path,backup))
            if typ=="replace":
                content=path.read_text(encoding="utf-8")
                find=action.get("find","")
                if not find or find not in content: raise ValueError(f"Replacement anchor not found in {path.name}")
                if content.count(find)!=1: raise ValueError(f"Replacement anchor is not unique in {path.name}")
                path.write_text(content.replace(find,action.get("replace",""),1),encoding="utf-8")
            elif typ=="append":
                with path.open("a",encoding="utf-8") as f: f.write("\n\n"+action.get("content","")[:20000])
            elif typ=="command":
                cmd=action.get("cmd","")
                if not (cmd.startswith("python -m py_compile") or cmd.startswith("git status") or cmd=="pwd"):
                    raise ValueError("Command not allowed.")
            else:
                raise ValueError(f"Unsupported action: {typ}")
            touched.append(path)
        ok,out=run_validation()
        if not ok: raise RuntimeError("Validation failed:\n"+out)
        for _,backup in backups:
            try: backup.unlink()
            except OSError: pass
        return True,"Validation passed: Python compilation is clean."
    except Exception:
        for path,backup in reversed(backups):
            try: shutil.copy2(backup,path)
            except OSError: pass
            try: backup.unlink()
            except OSError: pass
        raise


async def ai_watchdog():
    while True:
        try:
            ok,out=run_validation()
            if not ok: plus_log("WATCHDOG compile failure: "+out)
            if bot.is_closed(): plus_log("WATCHDOG detected closed bot session.")
        except Exception: plus_log(traceback.format_exc())
        await asyncio.sleep(120)


async def ai_reply(ctx, text, *, include_context=True):
    if include_context:
        location="DM" if ctx.guild is None else f"guild={ctx.guild.name} ({ctx.guild.id})"
    else: location="unknown"
    history=ai_history[ctx.author.id]
    history.append({"role":"user","content":text})
    system=AI_SYSTEM+f"\nCurrent context: {location}\nWorkspace: {AI_WORKSPACE}"
    messages=[{"role":"system","content":system},*history]
    return await hf_chat(messages)


@bot.command(name="ai", hidden=True)
async def ai_command(ctx, *, prompt: str = "help"):
    if not owner_only(ctx):
        return
    if prompt.strip().lower()=="help":
        embed=make_embed("RM Core • Private AI", "Private developer/operations agent. This command is intentionally absent from public command browsers.", PALETTE["main"])
        embed.add_field(name="Chat", value=f"`{PREFIX}ai <message>`\nUse it in DMs for a private coding/ops conversation.", inline=False)
        embed.add_field(name="Control", value=f"`{PREFIX}ai help` • `{PREFIX}ai status` • `{PREFIX}ai diagnose` • `{PREFIX}ai inspect <file>` • `{PREFIX}ai fix` • `{PREFIX}ai restart`", inline=False)
        embed.add_field(name="Build", value="Ask RM Core to create commands, repair features, refactor code, add UI, inspect errors, or plan an implementation. Risky file changes require confirmation and pass a compile check before they stick.", inline=False)
        return await ctx.reply(embed=embed, mention_author=False)
    command=prompt.strip()
    if command.lower()=="status":
        ok,out=run_validation(); return await ctx.reply(embed=make_embed("RM Core • Status", f"**Bot:** `{'online' if not bot.is_closed() else 'closed'}`\n**Latency:** `{round(bot.latency*1000)}ms`\n**Python validation:** `{'PASS' if ok else 'FAIL'}`\n\n{out[:1200]}" , PALETTE["success"] if ok else PALETTE["danger"]))
    if command.lower()=="diagnose":
        ok,out=run_validation(); recent="";
        try: recent=AI_LOG.read_text(encoding="utf-8")[-4000:]
        except OSError: pass
        prompt=f"Diagnose the current RM bot. Compile={'PASS' if ok else 'FAIL'}. Compiler output: {out}. Recent runtime log: {recent}. Give likely causes and a concise repair plan. Do not edit files yet."
        result=await ai_reply(ctx,prompt); return await ctx.reply(embed=make_embed("RM Core • Diagnostic", result[:3900], PALETTE["warning"] if not ok else PALETTE["success"]), mention_author=False)
    if command.lower().startswith("inspect "):
        path=allowed_path(command.split(" ",1)[1].strip())
        if not path or not path.exists(): return await ctx.reply("I can only inspect an existing non-secret file in the RM workspace.", ephemeral=True)
        content=path.read_text(encoding="utf-8")[:28000]
        result=await ai_reply(ctx,f"Inspect this file and report bugs, architecture issues, and safe improvements. File={path.name}\n```text\n{content}\n```")
        return await ctx.reply(embed=make_embed(f"RM Core • Inspect • {path.name}", result[:3900]), mention_author=False)
    if command.lower()=="restart":
        async def do_restart(interaction):
            await interaction.response.edit_message(embed=make_embed("RM Core • Restarting", "Restart approved. Saving state and replacing the current process…", PALETTE["warning"]), view=None)
            await asyncio.sleep(0.5)
            os.execv(sys.executable,[sys.executable,*sys.argv])
        return await ctx.reply(embed=make_embed("RM Core • Restart Confirmation", "This restarts the bot process. Your database is preserved."),view=ConfirmView(ctx.author.id,do_restart),ephemeral=True)
    try:
        result=await ai_reply(ctx,command)
        # The AI may return structured actions; only apply after explicit confirmation.
        cleaned=result.strip()
        if cleaned.startswith("{") and '"actions"' in cleaned:
            try:
                plan=json.loads(cleaned)
                actions=plan.get("actions",[])
                if actions:
                    pending_ai_actions[ctx.author.id]=actions
                    summary=plan.get("summary","Planned code change")
                    risk=plan.get("risk","unknown")
                    async def apply_plan(interaction):
                        pending_ai_actions.pop(ctx.author.id,None)
                        await interaction.response.edit_message(embed=make_embed("RM Core • Applying", "Applying changes, creating automatic backups, then validating…", PALETTE["warning"]),view=None)
                        try:
                            ok,msg=apply_actions(actions)
                            await interaction.followup.send(embed=make_embed("RM Core • Change Applied", f"{summary}\n\n**Validation:** {msg}", PALETTE["success"]),ephemeral=True)
                        except Exception as exc:
                            plus_log(traceback.format_exc())
                            await interaction.followup.send(embed=make_embed("RM Core • Rolled Back", f"Nothing unsafe was left applied.\n`{exc}`", PALETTE["danger"]),ephemeral=True)
                    return await ctx.reply(embed=make_embed("RM Core • Proposed Change", f"**Summary:** {summary}\n**Risk:** `{risk}`\n**Actions:** `{len(actions)}`\n\nNo changes have been applied yet."),view=ConfirmView(ctx.author.id,apply_plan),mention_author=False)
            except json.JSONDecodeError:
                pass
        return await ctx.reply(embed=make_embed("RM Core", cleaned[:3900]),mention_author=False)
    except Exception as exc:
        plus_log(traceback.format_exc())
        await ctx.reply(embed=make_embed("RM Core • Error", f"`{exc}`", PALETTE["danger"]),mention_author=False)


@bot.event
async def _rm_plus_command_error(ctx, error):
    # This event is intentionally not used; core.py owns the global error handler.
    return


plus_db()


# ==================== RM UI ====================

import discord
from discord.ext import commands



def remove_old(name):
    bot.remove_command(name)
    try:
        bot.tree.remove_command(name, type=discord.AppCommandType.chat_input)
    except Exception:
        try:
            bot.tree.remove_command(name)
        except Exception:
            pass


for _name in ("setup", "config", "help"):
    remove_old(_name)


@bot.hybrid_group(name="setup", description="Deploy the complete RM server stack.")
@admin_only()
async def setup_group(ctx):
    await ctx.reply(embed=make_embed("RM • Setup", f"Use `{PREFIX}setup all` or `/setup all` for the complete automatic deployment."))


@setup_group.command(name="all", description="Automatically configure the entire server for RM.")
@admin_only()
async def setup_all(ctx):
    await full_setup(ctx)


@bot.hybrid_group(name="config", description="Open the RM server control center.")
@admin_only()
async def config_group(ctx):
    cfg = get_config(ctx.guild.id)
    modules = [
        ("Invite filter", cfg["anti_invites"]), ("Link filter", cfg["anti_links"]),
        ("Word filter", cfg["anti_slurs"]), ("Spam shield", cfg["anti_spam"]),
        ("Caps filter", cfg["anti_caps"]), ("Mention shield", cfg["anti_mentions"]),
        ("Leveling", cfg["leveling"]), ("Welcome", cfg["welcome_enabled"]),
        ("Autorole", cfg["autorole_enabled"]), ("Verification", cfg["verification_enabled"]),
        ("Tickets", cfg["tickets_enabled"]),
    ]
    embed = make_embed("⚙ RM • Configuration", "Use `/config interactive` for the live control panel.")
    embed.add_field(name="Security", value="\n".join(f"{'🟢' if value else '⚫'} {name}" for name, value in modules[:6]), inline=True)
    embed.add_field(name="Community", value="\n".join(f"{'🟢' if value else '⚫'} {name}" for name, value in modules[6:]), inline=True)
    embed.add_field(name="Punishment", value=f"`{cfg['punishment']}` • `{cfg['timeout_seconds']}s` timeout", inline=False)
    await ctx.reply(embed=embed)


@config_group.command(name="interactive", description="Open the animated RM configuration control panel.")
@admin_only()
async def config_interactive(ctx):
    cfg = get_config(ctx.guild.id)
    enabled = sum(int(cfg[k]) for k in ("anti_links", "anti_invites", "anti_spam", "anti_mentions", "anti_caps", "welcome_enabled", "leveling", "autorole_enabled", "verification_enabled", "tickets_enabled"))
    embed = make_embed("RM • Control Center", f"**Modules active:** `{enabled}/10`\n\nUse the dropdown to toggle modules. Changes are saved immediately.")
    embed.set_footer(text="RM • live server controls")
    await ctx.reply(embed=embed, view=ConfigView(ctx.author.id, ctx.guild.id))


HELP_PAGES = [
    ("🛡 Moderation", "`warn` · `warnings` · `clearwarns` · `timeout` · `untimeout` · `kick` · `ban` · `unban` · `purge` · `lock` · `unlock` · `slowmode` · `case` · `tempban`"),
    ("⚙ Server", "`setup all` · `config interactive` · `server backup` · `server backups` · `server restore` · `raidmode` · `schedule` · `tempchannel` · `temprole`"),
    ("✨ Community", "`rank` · `leaderboard` · `suggest` · `userinfo` · `serverinfo` · `invites` · `analytics` · `staffactivity` · `custom command` · `embedbuilder`"),
    ("🔐 Security", "`automod` · `filter` · `verification` · `verifyquestions` · `tickets` · `reactionrole` · raid shield · verification gate · persistent logs"),
]


class HelpView(discord.ui.View):
    def __init__(self, author_id):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.page = 0
        self.page_label = discord.ui.Button(label="1 / 4", style=discord.ButtonStyle.secondary, disabled=True)
        self.prev_button = discord.ui.Button(label="Prev", style=discord.ButtonStyle.secondary, emoji="◀")
        self.next_button = discord.ui.Button(label="Next", style=discord.ButtonStyle.secondary, emoji="▶")
        self.home_button = discord.ui.Button(label="Home", style=discord.ButtonStyle.primary, emoji="⌂")
        self.prev_button.callback = self.prev
        self.next_button.callback = self.next
        self.home_button.callback = self.home
        self.add_item(self.prev_button)
        self.add_item(self.page_label)
        self.add_item(self.next_button)
        self.add_item(self.home_button)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This help browser belongs to the person who opened it.", ephemeral=True)
            return False
        return True

    async def render(self, interaction):
        title, body = HELP_PAGES[self.page]
        embed = make_embed(f"RM • Command Browser • {title}", body)
        embed.add_field(name="Navigation", value="Use the buttons to browse RM's command systems.")
        embed.set_footer(text=f"Page {self.page + 1}/{len(HELP_PAGES)} • Prefix: {PREFIX} • Slash commands enabled")
        self.page_label.label = f"{self.page + 1} / {len(HELP_PAGES)}"
        await interaction.response.edit_message(embed=embed, view=self)

    async def prev(self, interaction):
        self.page = (self.page - 1) % len(HELP_PAGES)
        await self.render(interaction)

    async def next(self, interaction):
        self.page = (self.page + 1) % len(HELP_PAGES)
        await self.render(interaction)

    async def home(self, interaction):
        self.page = 0
        await self.render(interaction)


@bot.hybrid_command(name="help", description="Open the RM interactive command browser.")
async def help_cmd(ctx):
    title, body = HELP_PAGES[0]
    embed = make_embed(f"RM • Command Browser • {title}", body)
    embed.add_field(name="Navigation", value="Use the buttons to browse moderation, server, community, and security systems.")
    embed.set_footer(text=f"Page 1/{len(HELP_PAGES)} • Prefix: {PREFIX} • Slash commands enabled")
    await ctx.reply(embed=embed, view=HelpView(ctx.author.id))


# ==================== RM VERIFICATION ====================

import discord



class VerifyQuestionModal(discord.ui.Modal, title="RM • Verification"):
    answer = discord.ui.TextInput(label="Answer", style=discord.TextStyle.short, max_length=300)

    def __init__(self, member):
        super().__init__()
        self.member = member

    async def on_submit(self, interaction):
        question = get_question(interaction.guild.id) if interaction.guild else None
        if not question:
            return await interaction.response.send_message("Verification is not configured.", ephemeral=True)
        expected = (question["expected"] or "").strip().casefold()
        answer = self.answer.value.strip().casefold()
        if expected and answer != expected:
            await interaction.response.send_message("❌ That answer wasn't accepted. Try again.", ephemeral=True)
            return
        guild = interaction.guild
        cfg = get_config(guild.id)
        role = guild.get_role(cfg["verify_role"]) if cfg["verify_role"] else None
        if not role or role >= guild.me.top_role:
            return await interaction.response.send_message("Verification role is not configured correctly.", ephemeral=True)
        try:
            await self.member.add_roles(role, reason="RM question verification")
            unverified = guild.get_role(cfg["unverified_role"]) if cfg["unverified_role"] else None
            if unverified and unverified in self.member.roles and unverified < guild.me.top_role:
                await self.member.remove_roles(unverified, reason="RM question verification")
            await interaction.response.send_message("✅ Verified. Welcome in.", ephemeral=True)
        except discord.HTTPException as exc:
            plus_log(f"Question verification role update failed: {exc}")
            await interaction.response.send_message("I couldn't update your roles.", ephemeral=True)


class QuestionVerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Start Verification", style=discord.ButtonStyle.success, emoji="✓", custom_id="rm:question_verify")
    async def start(self, interaction, button):
        question = get_question(interaction.guild.id) if interaction.guild else None
        if not question:
            return await interaction.response.send_message("No verification question is configured. Ask an administrator.", ephemeral=True)
        await interaction.response.send_modal(VerifyQuestionModal(interaction.user))


@bot.hybrid_command(name="verifychallenge", description="Deploy the RM member verification question panel.")
async def verifychallenge(ctx, channel: discord.TextChannel = None):
    if not ctx.guild or not (ctx.author.guild_permissions.manage_guild or ctx.author.guild_permissions.administrator):
        return await ctx.reply("You need Manage Server to deploy this.")
    question = get_question(ctx.guild.id)
    if not question:
        return await ctx.reply("Set a question first: `verifyquestions set Question || expected-answer`.")
    channel = channel or ctx.channel
    embed = make_embed("🔐 RM • Verification Challenge", f"**Question:** {question['question']}\n\nPress **Start Verification** to answer privately.")
    await channel.send(embed=embed, view=QuestionVerifyView())
    await ctx.reply(f"✅ Verification challenge deployed in {channel.mention}.")

# Flattened from rm_plus.py, rm_ui.py, and rm_verify.py.

VerifyView = QuestionVerifyView

if __name__ == "__main__":
    bot.run(TOKEN)
