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


# -----------------------------
# Database
# -----------------------------

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
        """INSERT INTO cases
        (guild_id,user_id,moderator_id,action,reason,created_at)
        VALUES (?,?,?,?,?,?)""",
        (guild_id, user_id, moderator_id, action, reason, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    case_id = cur.lastrowid
    conn.close()
    return case_id


def add_warning(guild_id, user_id, moderator_id, reason):
    conn = db()
    cur = conn.execute(
        """INSERT INTO warnings
        (guild_id,user_id,moderator_id,reason,created_at)
        VALUES (?,?,?,?,?)""",
        (guild_id, user_id, moderator_id, reason, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    warning_id = cur.lastrowid
    conn.close()
    return warning_id


def warning_count(guild_id, user_id):
    conn = db()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM warnings WHERE guild_id=? AND user_id=?",
        (guild_id, user_id),
    ).fetchone()
    conn.close()
    return row["n"]


# -----------------------------
# Utilities / UI
# -----------------------------

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


async def animated_reply(send, title, steps, final_color=PALETTE["main"], ephemeral=False):
    msg = await send(embed=discord.Embed(title=title, description="▰▱▱▱▱", color=PALETTE["main"]), ephemeral=ephemeral)
    for i, text in enumerate(steps, start=1):
        await asyncio.sleep(0.18)
        bar = progress_bar(i, len(steps), 10)
        try:
            await msg.edit(embed=discord.Embed(title=title, description=f"{bar}\n{text}", color=PALETTE["main"]))
        except discord.HTTPException:
            break
    await asyncio.sleep(0.12)
    return msg


async def log_event(guild, *, title, description, color=PALETTE["main"], channel_key="log_channel"):
    cfg = get_config(guild.id)
    channel_id = cfg[channel_key]
    if not channel_id:
        return
    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
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
            await message.channel.send(
                f"{message.author.mention} warning issued. `#{warning_id}`",
                delete_after=6,
            )
        elif action == "kick" and message.author.top_role < message.guild.me.top_role:
            await message.author.kick(reason=reason)
        elif action == "ban" and message.author.top_role < message.guild.me.top_role:
            await message.author.ban(reason=reason, delete_message_seconds=86400)
        elif message.author.top_role < message.guild.me.top_role:
            await message.author.timeout(
                timedelta(seconds=int(cfg["timeout_seconds"])),
                reason=reason,
            )
    except discord.HTTPException:
        pass
    case_id = add_case(message.guild.id, message.author.id, bot.user.id, action, reason)
    await log_event(
        message.guild,
        title=f"AutoMod • {action.title()}",
        description=(
            f"**User:** {message.author.mention} (`{message.author.id}`)\n"
            f"**Reason:** {reason}\n**Case:** `#{case_id}`"
        ),
        color=PALETTE["danger"],
    )
    return True


def get_level(guild_id, user_id):
    conn = db()
    row = conn.execute(
        "SELECT * FROM levels WHERE guild_id=? AND user_id=?",
        (guild_id, user_id),
    ).fetchone()
    conn.close()
    return row or {"xp": 0, "level": 0}


def add_xp(guild_id, user_id, amount):
    conn = db()
    conn.execute(
        "INSERT OR IGNORE INTO levels(guild_id,user_id,xp,level) VALUES(?,?,0,0)",
        (guild_id, user_id),
    )
    row = conn.execute(
        "SELECT xp,level FROM levels WHERE guild_id=? AND user_id=?",
        (guild_id, user_id),
    ).fetchone()
    xp = int(row["xp"]) + amount
    level = int(row["level"])
    required = 100 + (level * 50)
    leveled = False
    while xp >= required:
        xp -= required
        level += 1
        required = 100 + (level * 50)
        leveled = True
    conn.execute(
        "UPDATE levels SET xp=?,level=? WHERE guild_id=? AND user_id=?",
        (xp, level, guild_id, user_id),
    )
    conn.commit()
    conn.close()
    return xp, level, required, leveled


def can_manage(member: discord.Member, target: discord.Member):
    if target.id == member.id or target.id == member.guild.owner_id:
        return False
    if target.id == bot.user.id:
        return False
    return target.top_role < member.top_role and target.top_role < member.guild.me.top_role


# -----------------------------
# Persistent verification
# -----------------------------

class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Verify",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id="rm:verify",
    )
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("This only works in a server.", ephemeral=True)
        cfg = get_config(guild.id)
        role_id = cfg["verify_role"]
        role = guild.get_role(role_id) if role_id else None
        member = interaction.user
        if not role:
            return await interaction.response.send_message(
                "Verification isn't configured correctly. Ask an administrator.",
                ephemeral=True,
            )
        if role in member.roles:
            return await interaction.response.send_message("You're already verified ✅", ephemeral=True)
        if role >= guild.me.top_role:
            return await interaction.response.send_message(
                "My bot role must be above the verification role.",
                ephemeral=True,
            )
        try:
            await member.add_roles(role, reason="RM verification")
            unverified_id = cfg["unverified_role"]
            unverified = guild.get_role(unverified_id) if unverified_id else None
            if unverified and unverified in member.roles and unverified < guild.me.top_role:
                await member.remove_roles(unverified, reason="RM verification")
            await log_event(
                guild,
                title="✅ Member Verified",
                description=f"{member.mention} (`{member.id}`) verified.",
                color=PALETTE["success"],
            )
            await interaction.response.send_message("Verification complete. Welcome in 🤝", ephemeral=True)
        except discord.HTTPException:
            await interaction.response.send_message("I couldn't update your roles.", ephemeral=True)


class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Open Ticket",
        style=discord.ButtonStyle.primary,
        emoji="🎫",
        custom_id="rm:ticket_open",
    )
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)
        cfg = get_config(guild.id)
        existing = None
        conn = db()
        row = conn.execute(
            "SELECT channel_id FROM tickets WHERE guild_id=? AND user_id=? AND closed_at IS NULL",
            (guild.id, interaction.user.id),
        ).fetchone()
        conn.close()
        if row:
            existing = guild.get_channel(row["channel_id"])
        if existing:
            return await interaction.response.send_message(
                f"You already have a ticket: {existing.mention}",
                ephemeral=True,
            )

        category = guild.get_channel(cfg["ticket_category"]) if cfg["ticket_category"] else None
        support_role = guild.get_role(cfg["ticket_support_role"]) if cfg["ticket_support_role"] else None
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True, attach_files=True
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, manage_channels=True, manage_messages=True
            ),
        }
        if support_role:
            overwrites[support_role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            )

        safe_name = re.sub(r"[^a-z0-9-]", "", interaction.user.name.lower())[:18] or "user"
        try:
            channel = await guild.create_text_channel(
                f"ticket-{safe_name}",
                category=category if isinstance(category, discord.CategoryChannel) else None,
                overwrites=overwrites,
                topic=f"RM Ticket • {interaction.user.id}",
                reason="RM ticket opened",
            )
        except discord.HTTPException:
            return await interaction.response.send_message(
                "I couldn't create the ticket. Check my Manage Channels permission.",
                ephemeral=True,
            )

        conn = db()
        conn.execute(
            "INSERT INTO tickets(channel_id,guild_id,user_id,created_at) VALUES(?,?,?,?)",
            (channel.id, guild.id, interaction.user.id, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()

        embed = make_embed(
            "🎫 Ticket opened",
            f"Hey {interaction.user.mention}! Tell staff what you need.\n\nA staff member will be with you shortly.",
        )
        await channel.send(
            content=f"{interaction.user.mention} {support_role.mention if support_role else ''}",
            embed=embed,
            view=CloseTicketView(),
        )
        await interaction.response.send_message(f"Ticket created: {channel.mention}", ephemeral=True)


class CloseTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Close Ticket",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
        custom_id="rm:ticket_close",
    )
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("Invalid ticket.", ephemeral=True)

        conn = db()
        row = conn.execute(
            "SELECT * FROM tickets WHERE channel_id=?",
            (interaction.channel.id,),
        ).fetchone()
        conn.close()
        if row is None:
            return await interaction.response.send_message("This isn't an RM ticket.", ephemeral=True)

        allowed = (
            interaction.user.id == row["user_id"]
            or member_is_staff(interaction.user)
            or interaction.user.id == interaction.guild.owner_id
        )
        if not allowed:
            return await interaction.response.send_message("You can't close this ticket.", ephemeral=True)

        conn = db()
        conn.execute(
            "UPDATE tickets SET closed_at=? WHERE channel_id=?",
            (datetime.now(timezone.utc).isoformat(), interaction.channel.id),
        )
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

    @discord.ui.button(
        label="Get Role",
        style=discord.ButtonStyle.secondary,
        emoji="🎟️",
        custom_id="rm:reaction_role",
    )
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


# -----------------------------
# Permissions / checks
# -----------------------------

def mod_only():
    async def predicate(ctx):
        return bool(ctx.guild and (ctx.author.guild_permissions.manage_messages or
                                   ctx.author.guild_permissions.manage_guild or
                                   ctx.author.guild_permissions.administrator))
    return commands.check(predicate)


def admin_only():
    async def predicate(ctx):
        return bool(ctx.guild and (ctx.author.guild_permissions.manage_guild or
                                   ctx.author.guild_permissions.administrator))
    return commands.check(predicate)


async def is_bot_owner(user):
    if OWNER_ID and user.id == OWNER_ID:
        return True
    try:
        return await bot.is_owner(user)
    except Exception:
        return False


# -----------------------------
# Dashboard heartbeat
# -----------------------------

async def dashboard_sync(action, data):
    if not (DASHBOARD_URL and BOT_SYNC_KEY and aiohttp):
        return
    url = f"{DASHBOARD_URL}/api/public/bot/sync"
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(
                url,
                json={"action": action, "data": data},
                headers={"x-bot-key": BOT_SYNC_KEY, "Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=8),
            )
    except Exception:
        pass


@tasks.loop(minutes=2)
async def dashboard_heartbeat():
    await dashboard_sync(
        "heartbeat",
        {
            "server_count": len(bot.guilds),
            "member_count": sum(g.member_count or 0 for g in bot.guilds),
            "latency": round(bot.latency * 1000),
        },
    )


@tasks.loop(seconds=20)
async def presence_loop():
    if not bot.user:
        return
    total_members = sum(g.member_count or 0 for g in bot.guilds)
    choices = [
        f"{PREFIX}help • {len(bot.guilds)} servers",
        f"/help • {total_members:,} members",
        "RM Control Center • secured",
        "Carl-style tools • RM-style polish",
    ]
    try:
        await bot.change_presence(
            status=discord.Status.online,
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=random.choice(choices),
            ),
        )
    except discord.HTTPException:
        pass


# -----------------------------
# Lifecycle
# -----------------------------

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
    await log_event(
        guild,
        title="✨ RM joined",
        description=f"RM is now online in **{guild.name}**.\nUse `/setup` or `{PREFIX}setup` to configure it.",
        color=PALETTE["success"],
    )


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
        await log_event(
            member.guild,
            title="🚨 Possible raid activity",
            description=f"{len(bucket)} members joined within ~10 seconds.",
            color=PALETTE["danger"],
        )

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
            embed = make_embed(
                "👋 Welcome!",
                f"{text}\n\nYou are member **#{member.guild.member_count}**.",
                PALETTE["success"],
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            try:
                await channel.send(embed=embed)
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
            await punish_message(
                message,
                f"Spam ({cfg['spam_messages']} messages in {cfg['spam_window']}s)",
            )
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
                await message.channel.send(
                    embed=make_embed(
                        "🎉 Level up!",
                        f"{member.mention} reached **Level {level}**.\n"
                        f"`{progress_bar(xp, required)}` **{xp}/{required} XP**",
                        PALETTE["success"],
                    ),
                    delete_after=8,
                )

    await bot.process_commands(message)


# -----------------------------
# Core commands
# -----------------------------

@bot.hybrid_command(name="ping", description="Check RM latency.")
async def ping(ctx: commands.Context):
    await ctx.reply(f"🏓 **Pong!** `{round(bot.latency * 1000)}ms`", mention_author=False)


@bot.hybrid_command(name="help", description="Open the RM command center.")
async def help_cmd(ctx):
    embed = make_embed(
        "RM • Control Center",
        "One bot. One command system. `/command` and `-command` both work.",
    )
    embed.add_field(
        name="🛡️ Moderation",
        value="`warn` `warnings` `clearwarns` `timeout` `untimeout` `kick` `ban` `unban` `purge` `lock` `unlock` `slowmode` `case`",
        inline=False,
    )
    embed.add_field(
        name="⚙️ Server",
        value="`setup` `config` `automod` `filter` `logs` `welcome` `autorole` `verification` `tickets` `reactionrole`",
        inline=False,
    )
    embed.add_field(
        name="✨ Community",
        value="`userinfo` `serverinfo` `rank` `leaderboard` `suggest`",
        inline=False,
    )
    embed.set_footer(text=f"Prefix: {PREFIX}  •  Slash commands: enabled")
    await ctx.reply(embed=embed)


# -----------------------------
# Moderation
# -----------------------------

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
            await member.timeout(
                timedelta(seconds=int(cfg["timeout_seconds"])),
                reason=f"Warning threshold reached: {reason}",
            )
        except discord.HTTPException:
            pass

    case_id = add_case(ctx.guild.id, member.id, ctx.author.id, "warn", reason)
    await dm_member(
        member,
        "⚠️ You received a warning",
        f"**Server:** {ctx.guild.name}\n**Reason:** {reason}\n**Warnings:** `{count}`",
    )
    await ctx.reply(
        embed=make_embed(
            "⚠️ Warning issued",
            f"{member.mention} was warned.\n**Warning:** `#{warning_id}`\n**Total:** `{count}`\n**Case:** `#{case_id}`",
            PALETTE["warning"],
        )
    )
    await log_event(
        ctx.guild,
        title="Warning",
        description=f"**User:** {member.mention}\n**Moderator:** {ctx.author.mention}\n**Reason:** {reason}\n**Case:** `#{case_id}`",
        color=PALETTE["warning"],
        channel_key="modlog_channel",
    )


@bot.hybrid_command(name="warnings", description="View a member's warnings.")
@mod_only()
async def warnings(ctx, member: discord.Member):
    conn = db()
    rows = conn.execute(
        "SELECT * FROM warnings WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 10",
        (ctx.guild.id, member.id),
    ).fetchall()
    conn.close()

    if not rows:
        return await ctx.reply(f"{member.mention} has no warnings.")

    lines = []
    for row in rows:
        lines.append(
            f"`#{row['id']}` • {discord.utils.format_dt(datetime.fromisoformat(row['created_at']), 'R')} • {row['reason']}"
        )
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


# -----------------------------
# Setup / automod
# -----------------------------

@bot.hybrid_command(name="setup", description="Create a polished starter RM setup.")
@admin_only()
async def setup(ctx):
    guild = ctx.guild
    ensure_guild(guild.id)
    steps = [
        "Checking server permissions…",
        "Creating RM channels…",
        "Configuring logs…",
        "Enabling protection defaults…",
        "Finalizing Control Center…",
    ]
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

    final = make_embed(
        "✅ RM setup complete",
        "Your core RM configuration is live.\n\n"
        f"**Logs:** {log_channel.mention if log_channel else 'Not created'}\n"
        f"**Welcome:** {welcome.mention if welcome else 'Not created'}\n"
        f"**Created:** {', '.join(created) if created else 'Nothing new'}\n\n"
        f"Use `{PREFIX}verification setup` or `/verification setup` next.",
        PALETTE["success"],
    )
    await msg.edit(embed=final)


@bot.hybrid_command(name="config", description="View RM server configuration.")
@admin_only()
async def config(ctx):
    cfg = get_config(ctx.guild.id)
    embed = make_embed("⚙️ RM Configuration")
    modules = [
        ("Invite filter", cfg["anti_invites"]),
        ("Link filter", cfg["anti_links"]),
        ("Word filter", cfg["anti_slurs"]),
        ("Spam shield", cfg["anti_spam"]),
        ("Caps filter", cfg["anti_caps"]),
        ("Mention shield", cfg["anti_mentions"]),
        ("Leveling", cfg["leveling"]),
        ("Welcome", cfg["welcome_enabled"]),
        ("Autorole", cfg["autorole_enabled"]),
        ("Verification", cfg["verification_enabled"]),
        ("Tickets", cfg["tickets_enabled"]),
    ]
    embed.add_field(
        name="Security",
        value="\n".join(f"{'🟢' if value else '🔴'} {name}" for name, value in modules[:6]),
        inline=True,
    )
    embed.add_field(
        name="Community",
        value="\n".join(f"{'🟢' if value else '🔴'} {name}" for name, value in modules[6:]),
        inline=True,
    )
    embed.add_field(name="Punishment", value=f"`{cfg['punishment']}` • `{cfg['timeout_seconds']}s` timeout", inline=False)
    await ctx.reply(embed=embed)


@bot.hybrid_group(name="automod", description="Manage AutoMod.")
@admin_only()
async def automod(ctx):
    if ctx.invoked_subcommand is None:
        await ctx.reply("Modules: `links`, `invites`, `slurs`, `spam`, `caps`, `mentions`.\nUse `/automod toggle <module> <true|false>`.")


@automod.command(name="toggle", description="Toggle an AutoMod module.")
@admin_only()
async def automod_toggle(ctx, module: str, enabled: bool):
    mapping = {
        "links": "anti_links",
        "invites": "anti_invites",
        "slurs": "anti_slurs",
        "spam": "anti_spam",
        "caps": "anti_caps",
        "mentions": "anti_mentions",
    }
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


# -----------------------------
# Filters / logs
# -----------------------------

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


# -----------------------------
# Verification
# -----------------------------

@bot.hybrid_group(name="verification", description="Configure member verification.")
@admin_only()
async def verification(ctx):
    if ctx.invoked_subcommand is None:
        await ctx.reply("Use `/verification setup`, `/verification disable`, or `/verification status`.")


@verification.command(name="setup", description="Create a verification panel.")
@admin_only()
async def verification_setup(
    ctx,
    channel: discord.TextChannel,
    role: discord.Role,
    unverified_role: discord.Role = None,
):
    if role >= ctx.guild.me.top_role:
        return await ctx.reply("Move my bot role above the verification role.", ephemeral=True)
    if unverified_role and unverified_role >= ctx.guild.me.top_role:
        return await ctx.reply("Move my bot role above the unverified role.", ephemeral=True)

    set_config(ctx.guild.id, "verify_channel", channel.id)
    set_config(ctx.guild.id, "verify_role", role.id)
    set_config(ctx.guild.id, "unverified_role", unverified_role.id if unverified_role else None)
    set_config(ctx.guild.id, "verification_enabled", 1)

    embed = make_embed(
        "🔐 Server Verification",
        "Click the button below to verify yourself and unlock the server.\n\nAlready verified? You're good.",
        PALETTE["main"],
    )
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
    await ctx.reply(
        embed=make_embed(
            "🔐 Verification Status",
            f"**Enabled:** {'Yes' if cfg['verification_enabled'] else 'No'}\n"
            f"**Channel:** {channel}\n"
            f"**Verified role:** {role}\n"
            f"**Unverified role:** {unverified}",
        )
    )


# -----------------------------
# Welcome / autorole
# -----------------------------

@bot.hybrid_command(name="welcome", description="Configure the welcome system.")
@admin_only()
async def welcome_cmd(ctx, channel: discord.TextChannel, *, message="Welcome {member} to **{server}**!"):
    set_config(ctx.guild.id, "welcome_channel", channel.id)
    set_config(ctx.guild.id, "welcome_message", message[:1000])
    set_config(ctx.guild.id, "welcome_enabled", 1)
    await ctx.reply(
        embed=make_embed(
            "👋 Welcome system enabled",
            f"**Channel:** {channel.mention}\n**Preview:** {message.replace('{member}', ctx.author.mention).replace('{server}', ctx.guild.name)}",
            PALETTE["success"],
        )
    )


@bot.hybrid_command(name="autorole", description="Set the automatic member role.")
@admin_only()
async def autorole(ctx, role: discord.Role):
    if role >= ctx.guild.me.top_role:
        return await ctx.reply("Move my role above the autorole.", ephemeral=True)
    set_config(ctx.guild.id, "autorole_id", role.id)
    set_config(ctx.guild.id, "autorole_enabled", 1)
    await ctx.reply(f"✅ Autorole enabled → {role.mention}")


# -----------------------------
# Tickets
# -----------------------------

@bot.hybrid_group(name="tickets", description="Configure the ticket system.")
@admin_only()
async def tickets(ctx):
    if ctx.invoked_subcommand is None:
        await ctx.reply("Use `/tickets setup <channel> [category] [support role]` or `/tickets disable`.")


@tickets.command(name="setup", description="Create a ticket panel.")
@admin_only()
async def tickets_setup(
    ctx,
    channel: discord.TextChannel,
    category: discord.CategoryChannel = None,
    support_role: discord.Role = None,
):
    if support_role and support_role >= ctx.guild.me.top_role:
        return await ctx.reply("Move my bot role above the support role.", ephemeral=True)

    set_config(ctx.guild.id, "ticket_category", category.id if category else None)
    set_config(ctx.guild.id, "ticket_support_role", support_role.id if support_role else None)
    set_config(ctx.guild.id, "tickets_enabled", 1)

    embed = make_embed(
        "🎫 Need help?",
        "Open a private support ticket with the button below.\nPlease include your issue and relevant details.",
    )
    await channel.send(embed=embed, view=TicketView())
    await ctx.reply(f"✅ Ticket panel deployed in {channel.mention}.")


@tickets.command(name="disable", description="Disable tickets.")
@admin_only()
async def tickets_disable(ctx):
    set_config(ctx.guild.id, "tickets_enabled", 0)
    await ctx.reply("⛔ Ticket creation disabled.")


# -----------------------------
# Reaction roles
# -----------------------------

@bot.hybrid_command(name="reactionrole", description="Create a button role panel.")
@admin_only()
async def reactionrole(ctx, channel: discord.TextChannel, role: discord.Role, *, label="Get Role"):
    if role >= ctx.guild.me.top_role:
        return await ctx.reply("Move my bot role above the reaction role.", ephemeral=True)

    embed = make_embed("🎟️ Self Role", f"Click **{label}** to toggle {role.mention}.")
    message = await channel.send(embed=embed, view=ReactionRoleView(role.id))

    conn = db()
    conn.execute(
        """INSERT OR REPLACE INTO reaction_roles
        (message_id,guild_id,channel_id,role_id,label,emoji)
        VALUES(?,?,?,?,?,?)""",
        (message.id, ctx.guild.id, channel.id, role.id, label, "🎟️"),
    )
    conn.commit()
    conn.close()
    await ctx.reply(f"✅ Role panel created in {channel.mention}.")


# -----------------------------
# Community
# -----------------------------

@bot.hybrid_command(name="rank", description="View your rank.")
async def rank(ctx, member: discord.Member = None):
    member = member or ctx.author
    row = get_level(ctx.guild.id, member.id)
    xp = int(row["xp"])
    level = int(row["level"])
    required = 100 + (level * 50)
    conn = db()
    position = conn.execute(
        """SELECT COUNT(*)+1 AS pos
        FROM levels
        WHERE guild_id=? AND xp > ?""",
        (ctx.guild.id, xp),
    ).fetchone()["pos"]
    conn.close()

    embed = make_embed(
        f"📈 {member.display_name}",
        f"**Level:** `{level}`\n**XP:** `{xp}/{required}`\n`{progress_bar(xp, required)}`\n\n**Server rank:** `#{position}`",
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    await ctx.reply(embed=embed)


@bot.hybrid_command(name="leaderboard", description="View the server XP leaderboard.")
async def leaderboard(ctx):
    conn = db()
    rows = conn.execute(
        """SELECT user_id, xp, level
        FROM levels
        WHERE guild_id=?
        ORDER BY level DESC, xp DESC
        LIMIT 10""",
        (ctx.guild.id,),
    ).fetchall()
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
    cur = conn.execute(
        """INSERT INTO suggestions(guild_id,user_id,content,created_at)
        VALUES(?,?,?,?)""",
        (ctx.guild.id, ctx.author.id, content[:1800], datetime.now(timezone.utc).isoformat()),
    )
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


# -----------------------------
# Information
# -----------------------------

@bot.hybrid_command(name="userinfo", description="View member information.")
async def userinfo(ctx, member: discord.Member = None):
    member = member or ctx.author
    roles = [r.mention for r in member.roles[1:]][-8:]
    embed = make_embed(f"👤 {member}", color=PALETTE["main"])
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
    row = conn.execute(
        "SELECT * FROM cases WHERE guild_id=? AND id=?",
        (ctx.guild.id, case_id),
    ).fetchone()
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


# -----------------------------
# Owner-only chatted relay
# -----------------------------

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


# -----------------------------
# Error handling
# -----------------------------

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


if __name__ == "__main__":
    bot.run(TOKEN)
