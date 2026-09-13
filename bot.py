"""
RM — Discord bot core.

Run on your VPS:
    python3 -m pip install -r requirements.txt
    cp .env.example .env      # then paste your bot token + sync key
    python3 bot.py

The bot talks to the dashboard through one HTTPS endpoint
(/api/public/bot/sync) using the shared BOT_SYNC_KEY, so no database
credentials ever live on the VPS.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import math
import os
import platform
import random
import re
import string
import time
import unicodedata
import urllib.parse
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import aiohttp
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# >>> PASTE YOUR BOT TOKEN IN bot/.env AS DISCORD_BOT_TOKEN <<<
TOKEN = os.getenv("DISCORD_BOT_TOKEN", "PUT_YOUR_BOT_TOKEN_HERE")

DASHBOARD_URL = os.getenv("DASHBOARD_URL", "https://rmxyz.vercel.app").rstrip("/")
SYNC_KEY = os.getenv("BOT_SYNC_KEY", "")
SYNC_URL = f"{DASHBOARD_URL}/api/public/bot/sync"

DEFAULT_PREFIXES = [
    p.strip() for p in os.getenv("BOT_PREFIXES", "rm!,rm?").split(",") if p.strip()
]
OWNER_IDS = {
    int(x)
    for x in os.getenv("BOT_OWNER_IDS", "").replace(" ", "").split(",")
    if x.isdigit()
}

VERSION = "2.0.0"
BRAND = 0xEF4444
OK = 0x22C55E
BAD = 0xEF4444
STARTED_AT = datetime.now(timezone.utc)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

INVITE_RE = re.compile(r"(discord\.(gg|io|me|li)|discord(app)?\.com/invite)/\S+", re.I)
LINK_RE = re.compile(r"https?://\S+", re.I)
EMOJI_RE = re.compile(r"<a?:\w+:\d+>|[\U0001F300-\U0001FAFF\u2600-\u27BF]")
ZALGO_RE = re.compile(r"[\u0300-\u036f\u0489]")
SCAM_HINTS = (
    "free-nitro",
    "freenitro",
    "steamcommunity.ru",
    "discordgift",
    "discord-gift",
    "nitro-drop",
    "gift-nitro",
    "airdrop-claim",
)
IP_GRABBER_HINTS = ("grabify.link", "iplogger.", "2no.co", "yip.su", "blasze.")

LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "$": "s", "@": "a"})


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = ZALGO_RE.sub("", text)
    text = text.lower().translate(LEET)
    return re.sub(r"(.)\1{2,}", r"\1\1", text)


def parse_duration(raw: str | None) -> int | None:
    """`10m`, `2h30m`, `7d` -> seconds."""
    if not raw:
        return None
    total = 0
    found = False
    for value, unit in re.findall(r"(\d+)\s*([smhdw])", raw.lower()):
        found = True
        total += int(value) * {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
    return total if found else None


def human_delta(seconds: int) -> str:
    seconds = int(seconds)
    parts = []
    for label, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if seconds >= size:
            parts.append(f"{seconds // size}{label}")
            seconds %= size
    return " ".join(parts[:3]) or "0s"


def prefix_for(bot: "RM", message: discord.Message):
    prefixes = list(DEFAULT_PREFIXES)
    if message.guild:
        prefixes = bot.prefix_cache.get(str(message.guild.id), prefixes)
    return commands.when_mentioned_or(*prefixes)(bot, message)


# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------


class RM(commands.Bot):
    def __init__(self) -> None:
        super().__init__(
            command_prefix=prefix_for,
            intents=intents,
            help_command=None,
            case_insensitive=True,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False),
        )
        self.session: aiohttp.ClientSession | None = None
        self.prefix_cache: dict[str, list[str]] = {}
        self.config_cache: dict[str, dict] = {}
        self.filter_cache: dict[str, list[dict]] = {}
        self.commands_processed = 0
        # runtime trackers
        self.msg_times: dict[tuple[int, int], deque] = defaultdict(lambda: deque(maxlen=25))
        self.msg_hashes: dict[tuple[int, int], deque] = defaultdict(lambda: deque(maxlen=8))
        self.join_times: dict[int, deque] = defaultdict(lambda: deque(maxlen=60))
        self.nuke_counters: dict[tuple[int, int, str], deque] = defaultdict(lambda: deque(maxlen=40))
        self.raid_mode: set[int] = set()
        self.afk: dict[int, str] = {}
        self.snipes: dict[int, tuple[str, str, str]] = {}
        self.starting = True

    async def setup_hook(self) -> None:
        self.session = aiohttp.ClientSession()
        self.heartbeat_loop.start()
        self.guild_loop.start()
        self.task_loop.start()

    # -- dashboard bridge ---------------------------------------------------
    async def sync(self, action: str, data: dict | None = None) -> dict | None:
        if not SYNC_KEY or self.session is None:
            return None
        try:
            async with self.session.post(
                SYNC_URL,
                json={"action": action, "data": data or {}},
                headers={"x-bot-key": SYNC_KEY},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    print(f"[sync:{action}] HTTP {resp.status}: {await resp.text()}")
                    return None
                return await resp.json()
        except Exception as exc:  # network hiccups must never kill the bot
            print(f"[sync:{action}] failed: {exc}")
            return None

    async def guild_config(self, guild_id: int, force: bool = False) -> dict:
        key = str(guild_id)
        cached = self.config_cache.get(key)
        if cached and not force and cached["_at"] > time.time() - 60:
            return cached
        result = await self.sync("config", {"guild_id": key})
        config: dict = {"_at": time.time()}
        for row in (result or {}).get("config", []):
            config[row["module"]] = {"enabled": row["enabled"], **(row["settings"] or {})}
        self.filter_cache[key] = (result or {}).get("words", []) or []
        core = config.get("core") or {}
        prefixes = core.get("prefixes") or DEFAULT_PREFIXES
        self.prefix_cache[key] = list(prefixes)
        self.config_cache[key] = config
        return config

    def module(self, config: dict, name: str) -> dict:
        return config.get(name) or {}

    def enabled(self, config: dict, name: str, default: bool = True) -> bool:
        mod = config.get(name)
        if mod is None:
            return default
        return bool(mod.get("enabled", default))

    async def log_case(
        self,
        guild: discord.Guild,
        action: str,
        target: discord.abc.User,
        moderator: discord.abc.User,
        reason: str | None,
        duration: int | None = None,
    ) -> int | None:
        result = await self.sync(
            "case",
            {
                "guild_id": str(guild.id),
                "action_type": action,
                "target_id": str(target.id),
                "target_tag": str(target),
                "moderator_id": str(moderator.id),
                "moderator_tag": str(moderator),
                "reason": reason,
                "duration_seconds": duration,
                "source": "bot",
            },
        )
        return (result or {}).get("case_number")

    async def log_event(self, guild_id: int, category: str, event_type: str, summary: str, **extra):
        await self.sync(
            "log",
            {
                "guild_id": str(guild_id),
                "category": category,
                "event_type": event_type,
                "summary": summary[:500],
                **extra,
            },
        )

    async def log_security(self, guild_id: int, system: str, event_type: str, severity: str,
                           actor: discord.abc.User | None, action_taken: str):
        await self.sync(
            "security",
            {
                "guild_id": str(guild_id),
                "system": system,
                "event_type": event_type,
                "severity": severity,
                "actor_id": str(actor.id) if actor else None,
                "actor_tag": str(actor) if actor else None,
                "action_taken": action_taken,
            },
        )

    async def alert(self, guild: discord.Guild, module: str, embed: discord.Embed):
        config = await self.guild_config(guild.id)
        channel_id = self.module(config, module).get("alert_channel_id")
        if not channel_id:
            channel_id = (self.module(config, "logging").get("channels") or {}).get("security")
        if not channel_id:
            return
        channel = guild.get_channel(int(channel_id))
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.send(embed=embed)
            except discord.HTTPException:
                pass

    # -- background loops ---------------------------------------------------
    @tasks.loop(seconds=45)
    async def heartbeat_loop(self):
        await self.wait_until_ready()
        mem = None
        cpu = None
        try:
            import psutil

            proc = psutil.Process()
            mem = round(proc.memory_info().rss / 1_048_576, 1)
            cpu = round(psutil.cpu_percent(interval=None), 1)
        except Exception:
            pass
        await self.sync(
            "heartbeat",
            {
                "shard_id": 0,
                "status": "online",
                "latency_ms": round(self.latency * 1000) if self.latency else None,
                "guild_count": len(self.guilds),
                "user_count": sum(g.member_count or 0 for g in self.guilds),
                "commands_processed": self.commands_processed,
                "memory_mb": mem,
                "cpu_percent": cpu,
                "started_at": STARTED_AT.isoformat(),
                "version": VERSION,
            },
        )

    @tasks.loop(minutes=5)
    async def guild_loop(self):
        await self.wait_until_ready()
        rows = [
            {
                "id": str(g.id),
                "name": g.name,
                "icon": g.icon.key if g.icon else None,
                "owner_id": str(g.owner_id) if g.owner_id else None,
                "member_count": g.member_count or 0,
                "channel_count": len(g.channels),
                "role_count": len(g.roles),
            }
            for g in self.guilds
        ]
        if rows:
            await self.sync("guilds", {"guilds": rows})

    @tasks.loop(seconds=20)
    async def task_loop(self):
        await self.wait_until_ready()
        result = await self.sync("tasks")
        for task in (result or {}).get("tasks", []):
            err = None
            try:
                await self.run_task(task)
            except Exception as exc:
                err = str(exc)[:400]
            await self.sync("task_done", {"id": task["id"], "error": err})

    async def run_task(self, task: dict):
        guild = self.get_guild(int(task["guild_id"])) if task.get("guild_id") else None
        kind = task["task_type"]
        payload = task.get("payload") or {}
        if kind == "reload_config" and guild:
            await self.guild_config(guild.id, force=True)
        elif kind == "lockdown" and guild:
            await set_lockdown(guild, True, payload.get("reason") or "Dashboard lockdown")
        elif kind == "unlockdown" and guild:
            await set_lockdown(guild, False, "Dashboard unlock")
        elif kind == "raidmode_on" and guild:
            self.raid_mode.add(guild.id)
        elif kind == "raidmode_off" and guild:
            self.raid_mode.discard(guild.id)
        elif kind == "sync_slash":
            await self.tree.sync()
        elif kind == "announce" and guild:
            channel = guild.get_channel(int(payload.get("channel_id", 0) or 0))
            if isinstance(channel, discord.TextChannel):
                await channel.send(
                    embed=discord.Embed(
                        title=payload.get("title") or "Announcement",
                        description=payload.get("message") or "",
                        color=BRAND,
                    )
                )


bot = RM()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def embed(description: str, title: str | None = None, color: int = BRAND) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=color)


def ok(description: str) -> discord.Embed:
    return embed(f"✅ {description}", color=OK)


def err(description: str) -> discord.Embed:
    return embed(f"❌ {description}", color=BAD)


async def is_trusted(guild: discord.Guild, member: discord.abc.User) -> bool:
    if member.id == guild.owner_id or member.id in OWNER_IDS or member.id == bot.user.id:
        return True
    return False


def above(actor: discord.Member, target: discord.Member) -> bool:
    if actor.id == actor.guild.owner_id:
        return True
    return actor.top_role > target.top_role


async def set_lockdown(guild: discord.Guild, on: bool, reason: str) -> int:
    changed = 0
    everyone = guild.default_role
    for channel in guild.text_channels:
        try:
            overwrite = channel.overwrites_for(everyone)
            overwrite.send_messages = False if on else None
            await channel.set_permissions(everyone, overwrite=overwrite, reason=reason)
            changed += 1
        except discord.HTTPException:
            continue
    await bot.log_security(
        guild.id, "lockdown", "enabled" if on else "disabled", "high", None, f"{changed} channels"
    )
    return changed


async def punish(guild: discord.Guild, member: discord.Member, action: str, reason: str,
                 seconds: int | None = None):
    try:
        if action == "ban":
            await guild.ban(member, reason=reason, delete_message_days=0)
        elif action == "kick":
            await guild.kick(member, reason=reason)
        elif action in ("timeout", "mute"):
            until = discord.utils.utcnow() + timedelta(seconds=seconds or 3600)
            await member.timeout(until, reason=reason)
        elif action == "strip":
            keep = [r for r in member.roles if r.is_default() or r.managed]
            await member.edit(roles=keep, reason=reason)
    except discord.Forbidden:
        return False
    except discord.HTTPException:
        return False
    return True


# ---------------------------------------------------------------------------
# AutoMod
# ---------------------------------------------------------------------------


async def run_automod(message: discord.Message) -> bool:
    """Returns True when the message was actioned."""
    guild = message.guild
    if guild is None or message.author.bot:
        return False
    member = message.author
    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.manage_messages or member.id == guild.owner_id:
        return False

    config = await bot.guild_config(guild.id)
    automod = bot.module(config, "automod")
    filt = bot.module(config, "wordfilter")
    exempt_channels = {str(c) for c in (automod.get("exempt_channels") or [])}
    exempt_roles = {str(r) for r in (automod.get("exempt_roles") or [])}
    if str(message.channel.id) in exempt_channels:
        return False
    if exempt_roles & {str(r.id) for r in member.roles}:
        return False

    content = message.content or ""
    lowered = normalize(content)
    mods = automod.get("modules") or {}

    async def act(rule: str, cfg: dict, note: str):
        action = cfg.get("action", "delete")
        if action != "log":
            try:
                await message.delete()
            except discord.HTTPException:
                pass
        if action in ("timeout", "kick", "ban", "mute"):
            await punish(guild, member, action, f"AutoMod: {rule}", cfg.get("duration_seconds"))
        await bot.log_event(guild.id, "automod", rule, f"{member} — {note}", actor_id=str(member.id),
                            channel_id=str(message.channel.id))
        try:
            await message.channel.send(embed=err(f"{member.mention} — {note}"), delete_after=6)
        except discord.HTTPException:
            pass

    # word filter
    if bot.enabled(config, "wordfilter", True):
        words = bot.filter_cache.get(str(guild.id), [])
        for row in words:
            word = normalize(str(row.get("word", "")))
            if not word:
                continue
            hit = word in lowered if filt.get("partial_match", True) else word in lowered.split()
            if hit:
                await act("wordfilter", {"action": filt.get("action", "delete")},
                          filt.get("warn_message") or "That word isn't allowed here.")
                return True

    if not bot.enabled(config, "automod", True):
        return False

    key = (guild.id, member.id)
    now = time.time()
    bot.msg_times[key].append(now)

    # spam / flood
    for rule in ("spam", "flood"):
        cfg = mods.get(rule) or {}
        if cfg.get("enabled"):
            window = float(cfg.get("seconds", 5))
            limit = int(cfg.get("messages", 6))
            recent = [t for t in bot.msg_times[key] if t > now - window]
            if len(recent) >= limit:
                bot.msg_times[key].clear()
                await act(rule, cfg, "Slow down — too many messages.")
                return True

    # duplicate / repeated messages
    digest = hashlib.sha1(lowered.encode()).hexdigest()
    bot.msg_hashes[key].append(digest)
    for rule in ("duplicate_messages", "repeated_messages"):
        cfg = mods.get(rule) or {}
        if cfg.get("enabled") and content.strip():
            limit = int(cfg.get("limit", 3))
            if list(bot.msg_hashes[key]).count(digest) >= limit:
                bot.msg_hashes[key].clear()
                await act(rule, cfg, "Stop repeating the same message.")
                return True

    # mentions
    mention_count = len(message.mentions) + len(message.role_mentions)
    for rule in ("mention_spam", "mass_mentions"):
        cfg = mods.get(rule) or {}
        if cfg.get("enabled") and mention_count >= int(cfg.get("limit", 6)):
            await act(rule, cfg, "Too many mentions.")
            return True

    # invites
    cfg = mods.get("invites") or {}
    if cfg.get("enabled") and INVITE_RE.search(content):
        await act("invites", cfg, "Invite links aren't allowed.")
        return True

    links = LINK_RE.findall(content)

    cfg = mods.get("scam_links") or {}
    if cfg.get("enabled") and any(h in lowered for h in SCAM_HINTS):
        await act("scam_links", cfg, "That link looks like a scam.")
        return True

    cfg = mods.get("ip_grabbers") or {}
    if cfg.get("enabled") and any(h in lowered for h in IP_GRABBER_HINTS):
        await act("ip_grabbers", cfg, "IP-logger links are blocked.")
        return True

    cfg = mods.get("link_spam") or {}
    if cfg.get("enabled") and len(links) >= int(cfg.get("limit", 3)):
        allowed = [d.lower() for d in (automod.get("allowed_domains") or [])]
        if not all(any(d in link.lower() for d in allowed) for link in links):
            await act("link_spam", cfg, "Too many links.")
            return True

    # caps
    cfg = mods.get("caps") or {}
    letters = [c for c in content if c.isalpha()]
    if cfg.get("enabled") and len(letters) >= int(cfg.get("min_length", 12)):
        upper = sum(1 for c in letters if c.isupper()) / len(letters) * 100
        if upper >= float(cfg.get("percent", 70)):
            await act("caps", cfg, "Please don't shout.")
            return True

    # emoji / sticker / character spam
    cfg = mods.get("emoji_spam") or {}
    if cfg.get("enabled") and len(EMOJI_RE.findall(content)) >= int(cfg.get("limit", 12)):
        await act("emoji_spam", cfg, "Too many emoji.")
        return True

    cfg = mods.get("sticker_spam") or {}
    if cfg.get("enabled") and len(message.stickers) >= int(cfg.get("limit", 4)):
        await act("sticker_spam", cfg, "Too many stickers.")
        return True

    cfg = mods.get("character_spam") or {}
    if cfg.get("enabled") and re.search(r"(.)\1{%d,}" % int(cfg.get("limit", 15)), content):
        await act("character_spam", cfg, "Character spam isn't allowed.")
        return True

    cfg = mods.get("zalgo") or {}
    if cfg.get("enabled") and len(ZALGO_RE.findall(content)) > 8:
        await act("zalgo", cfg, "Zalgo text isn't allowed.")
        return True

    return False


# ---------------------------------------------------------------------------
# Anti-Raid
# ---------------------------------------------------------------------------


@bot.event
async def on_member_join(member: discord.Member):
    guild = member.guild
    config = await bot.guild_config(guild.id)

    # autorole + welcome
    welcome = bot.module(config, "welcome")
    if welcome.get("welcome_enabled") and welcome.get("welcome_channel_id"):
        channel = guild.get_channel(int(welcome["welcome_channel_id"]))
        if isinstance(channel, discord.TextChannel):
            text = str(welcome.get("welcome_message") or "Welcome {mention}!")
            text = (
                text.replace("{mention}", member.mention)
                .replace("{username}", member.name)
                .replace("{server}", guild.name)
                .replace("{membercount}", str(guild.member_count or 0))
            )
            try:
                if welcome.get("welcome_embed", True):
                    await channel.send(embed=embed(text, title=f"Welcome to {guild.name}"))
                else:
                    await channel.send(text)
            except discord.HTTPException:
                pass
    for role_id in welcome.get("autoroles") or []:
        role = guild.get_role(int(role_id))
        if role:
            try:
                await member.add_roles(role, reason="Autorole")
            except discord.HTTPException:
                pass

    await bot.log_event(guild.id, "member", "member_join", f"{member} joined",
                        actor_id=str(member.id))

    if not bot.enabled(config, "antiraid", True):
        return
    antiraid = bot.module(config, "antiraid")
    exempt = {str(r) for r in (antiraid.get("exempt_roles") or [])}
    if exempt & {str(r.id) for r in member.roles}:
        return

    action = antiraid.get("action", "kick")
    reasons: list[str] = []

    # account age
    min_age = int(antiraid.get("min_account_age_days", 7) or 0)
    age_days = (discord.utils.utcnow() - member.created_at).days
    if min_age and age_days < min_age:
        reasons.append(f"account {age_days}d old (min {min_age}d)")

    if antiraid.get("detect_default_avatars") and member.avatar is None:
        reasons.append("no avatar")
    if antiraid.get("detect_bot_raids") and member.bot:
        reasons.append("bot account")

    # join burst
    now = time.time()
    bot.join_times[guild.id].append(now)
    window = float(antiraid.get("join_window_seconds", 10) or 10)
    threshold = int(antiraid.get("join_threshold", 8) or 8)
    burst = [t for t in bot.join_times[guild.id] if t > now - window]
    raiding = len(burst) >= threshold

    if antiraid.get("detect_similar_names"):
        similar = [
            m for m in guild.members
            if m.id != member.id and m.name[:5].lower() == member.name[:5].lower()
        ]
        if len(similar) >= 4:
            reasons.append("similar names to recent joins")

    if raiding:
        reasons.append(f"{len(burst)} joins in {int(window)}s")
        if guild.id not in bot.raid_mode and antiraid.get("raid_mode", "auto") == "auto":
            bot.raid_mode.add(guild.id)
            await bot.log_security(guild.id, "antiraid", "raid_mode_on", "critical", None,
                                   "raid mode enabled automatically")
            await bot.alert(guild, "antiraid",
                            embed(f"Raid detected — {len(burst)} joins in {int(window)}s. Raid mode is on.",
                                  title="Anti-Raid", color=BAD))
            if antiraid.get("restrict_new_members"):
                try:
                    await guild.edit(verification_level=discord.VerificationLevel.high,
                                     reason="Anti-raid")
                except discord.HTTPException:
                    pass
            minutes = int(antiraid.get("lockdown_minutes", 0) or 0)
            if minutes:
                await set_lockdown(guild, True, "Anti-raid lockdown")
                await asyncio.sleep(0)
                bot.loop.create_task(_auto_unlock(guild, minutes))

    if guild.id in bot.raid_mode and not reasons:
        reasons.append("joined during raid mode")

    if reasons:
        done = await punish(guild, member, action, f"Anti-Raid: {', '.join(reasons)}")
        await bot.log_security(guild.id, "antiraid", "member_blocked",
                               "high" if raiding else "medium", member,
                               f"{action if done else 'failed'} — {', '.join(reasons)}")
        if done and action in ("kick", "ban"):
            await bot.log_case(guild, action, member, bot.user, f"Anti-Raid: {', '.join(reasons)}")


async def _auto_unlock(guild: discord.Guild, minutes: int):
    await asyncio.sleep(minutes * 60)
    await set_lockdown(guild, False, "Anti-raid lockdown expired")
    bot.raid_mode.discard(guild.id)


@bot.event
async def on_member_remove(member: discord.Member):
    config = await bot.guild_config(member.guild.id)
    welcome = bot.module(config, "welcome")
    if welcome.get("goodbye_enabled") and welcome.get("goodbye_channel_id"):
        channel = member.guild.get_channel(int(welcome["goodbye_channel_id"]))
        if isinstance(channel, discord.TextChannel):
            text = str(welcome.get("goodbye_message") or "{username} left.")
            text = text.replace("{username}", member.name).replace("{server}", member.guild.name)
            try:
                await channel.send(embed=embed(text))
            except discord.HTTPException:
                pass
    await bot.log_event(member.guild.id, "member", "member_leave", f"{member} left",
                        actor_id=str(member.id))


# ---------------------------------------------------------------------------
# Anti-Nuke
# ---------------------------------------------------------------------------


async def antinuke_check(guild: discord.Guild, rule: str, audit_action: discord.AuditLogAction):
    config = await bot.guild_config(guild.id)
    if not bot.enabled(config, "antinuke", True):
        return
    antinuke = bot.module(config, "antinuke")
    watch = (antinuke.get("watch") or {}).get(rule) or {}
    if not watch.get("enabled", True):
        return

    actor: discord.abc.User | None = None
    try:
        async for entry in guild.audit_logs(limit=5, action=audit_action):
            if (discord.utils.utcnow() - entry.created_at).total_seconds() < 20:
                actor = entry.user
                break
    except discord.Forbidden:
        return
    if actor is None or await is_trusted(guild, actor):
        return

    now = time.time()
    key = (guild.id, actor.id, rule)
    bot.nuke_counters[key].append(now)
    window = float(watch.get("seconds", 10) or 10)
    limit = int(watch.get("limit", 3) or 3)
    recent = [t for t in bot.nuke_counters[key] if t > now - window]
    if len(recent) < limit:
        return
    bot.nuke_counters[key].clear()

    action = antinuke.get("punishment", "ban")
    member = guild.get_member(actor.id)
    done = False
    if member:
        if antinuke.get("strip_dangerous_permissions"):
            await punish(guild, member, "strip", f"Anti-Nuke: {rule}")
        done = await punish(guild, member, action, f"Anti-Nuke: {rule} x{len(recent)}")
    await bot.log_security(guild.id, "antinuke", rule, "critical", actor,
                           f"{action if done else 'detected'} — {len(recent)} in {int(window)}s")
    await bot.alert(guild, "antinuke",
                    embed(f"**{actor}** triggered `{rule}` {len(recent)} times in {int(window)}s.\n"
                          f"Action taken: **{action if done else 'none (missing permissions)'}**",
                          title="Anti-Nuke", color=BAD))
    if done:
        await bot.log_case(guild, action, actor, bot.user, f"Anti-Nuke: {rule}")
    if antinuke.get("auto_lockdown"):
        await set_lockdown(guild, True, "Anti-nuke auto lockdown")


@bot.event
async def on_guild_channel_delete(channel):
    await antinuke_check(channel.guild, "channel_delete", discord.AuditLogAction.channel_delete)
    await bot.log_event(channel.guild.id, "server", "channel_delete", f"#{channel} deleted")


@bot.event
async def on_guild_channel_create(channel):
    await antinuke_check(channel.guild, "channel_create", discord.AuditLogAction.channel_create)


@bot.event
async def on_guild_role_delete(role):
    await antinuke_check(role.guild, "role_delete", discord.AuditLogAction.role_delete)
    await bot.log_event(role.guild.id, "server", "role_delete", f"Role {role.name} deleted")


@bot.event
async def on_guild_role_create(role):
    await antinuke_check(role.guild, "role_create", discord.AuditLogAction.role_create)


@bot.event
async def on_guild_role_update(before, after):
    dangerous = discord.Permissions(administrator=True, manage_guild=True, manage_roles=True,
                                    manage_channels=True, ban_members=True)
    if not before.permissions.value & dangerous.value and after.permissions.value & dangerous.value:
        await antinuke_check(after.guild, "dangerous_permissions", discord.AuditLogAction.role_update)


@bot.event
async def on_member_ban(guild, user):
    await antinuke_check(guild, "member_ban", discord.AuditLogAction.ban)
    await bot.log_event(guild.id, "mod", "ban", f"{user} was banned", target_id=str(user.id))


@bot.event
async def on_member_unban(guild, user):
    await bot.log_event(guild.id, "mod", "unban", f"{user} was unbanned", target_id=str(user.id))


@bot.event
async def on_webhooks_update(channel):
    await antinuke_check(channel.guild, "webhook_create", discord.AuditLogAction.webhook_create)


@bot.event
async def on_guild_update(before, after):
    await antinuke_check(after, "guild_update", discord.AuditLogAction.guild_update)


# ---------------------------------------------------------------------------
# Core events
# ---------------------------------------------------------------------------


@bot.event
async def on_ready():
    print(f"RM v{VERSION} online as {bot.user} in {len(bot.guilds)} guilds")
    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.watching,
                                  name=f"{len(bot.guilds)} servers | rm!help")
    )
    if bot.starting:
        bot.starting = False
        await bot.sync("catalog", {"commands": [
            {"name": c.qualified_name, "description": c.help or "", "category": c.cog_name or "General"}
            for c in bot.walk_commands()
        ]})
        try:
            await bot.tree.sync()
        except Exception as exc:
            print(f"slash sync failed: {exc}")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or message.guild is None:
        return await bot.process_commands(message)

    # AFK clear + ping
    if message.author.id in bot.afk:
        bot.afk.pop(message.author.id, None)
        try:
            await message.channel.send(embed=ok("Welcome back, AFK removed."), delete_after=5)
        except discord.HTTPException:
            pass
    for user in message.mentions:
        if user.id in bot.afk:
            await message.channel.send(embed=embed(f"**{user.name}** is AFK: {bot.afk[user.id]}"))
            break

    if await run_automod(message):
        return

    # leveling
    config = await bot.guild_config(message.guild.id)
    if bot.enabled(config, "leveling", False):
        await bot.sync("command", {"guild_id": str(message.guild.id), "command": "_xp",
                                   "user_id": str(message.author.id), "success": True})

    await bot.process_commands(message)


@bot.event
async def on_message_delete(message: discord.Message):
    if message.guild and not message.author.bot:
        bot.snipes[message.channel.id] = (str(message.author), message.content or "(embed)",
                                          message.author.display_avatar.url)
        await bot.log_event(message.guild.id, "message", "message_delete",
                            f"{message.author}: {(message.content or '')[:200]}",
                            channel_id=str(message.channel.id))


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if before.guild and not before.author.bot and before.content != after.content:
        await bot.log_event(before.guild.id, "message", "message_edit",
                            f"{before.author}: {before.content[:100]} → {after.content[:100]}",
                            channel_id=str(before.channel.id))
        await run_automod(after)


@bot.event
async def on_command_completion(ctx: commands.Context):
    bot.commands_processed += 1
    await bot.sync("command", {
        "guild_id": str(ctx.guild.id) if ctx.guild else None,
        "command": ctx.command.qualified_name,
        "user_id": str(ctx.author.id),
        "success": True,
    })


@bot.event
async def on_command_error(ctx: commands.Context, error: Exception):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingPermissions):
        return await ctx.send(embed=err("You don't have permission to do that."))
    if isinstance(error, commands.BotMissingPermissions):
        return await ctx.send(embed=err("I'm missing the permissions for that."))
    if isinstance(error, commands.MissingRequiredArgument):
        return await ctx.send(embed=err(f"Usage: `{ctx.prefix}{ctx.command.qualified_name} "
                                        f"{ctx.command.signature}`"))
    if isinstance(error, commands.CommandOnCooldown):
        return await ctx.send(embed=err(f"Cooldown — try again in {error.retry_after:.1f}s."))
    if isinstance(error, (commands.BadArgument, commands.MemberNotFound, commands.UserNotFound)):
        return await ctx.send(embed=err(str(error)))
    print(f"[error] {ctx.command}: {error!r}")
    await bot.sync("error", {
        "guild_id": str(ctx.guild.id) if ctx.guild else None,
        "command": ctx.command.qualified_name if ctx.command else None,
        "message": repr(error),
    })
    await ctx.send(embed=err("Something went wrong. The owners have been notified."))


# ---------------------------------------------------------------------------
# Moderation
# ---------------------------------------------------------------------------


class Moderation(commands.Cog):
    """Warnings, bans, timeouts and channel control."""

    @commands.command(help="Warn a member")
    @commands.has_permissions(moderate_members=True)
    async def warn(self, ctx, member: discord.Member, *, reason: str = "No reason provided"):
        number = await bot.log_case(ctx.guild, "warn", member, ctx.author, reason)
        try:
            await member.send(embed=embed(f"You were warned in **{ctx.guild.name}**: {reason}"))
        except discord.HTTPException:
            pass
        await ctx.send(embed=ok(f"Warned {member.mention}" + (f" — case #{number}" if number else "")))

    @commands.command(help="Kick a member")
    @commands.has_permissions(kick_members=True)
    async def kick(self, ctx, member: discord.Member, *, reason: str = "No reason provided"):
        if not above(ctx.author, member):
            return await ctx.send(embed=err("That member is above you in the role list."))
        await member.kick(reason=f"{ctx.author}: {reason}")
        await bot.log_case(ctx.guild, "kick", member, ctx.author, reason)
        await ctx.send(embed=ok(f"Kicked {member}"))

    @commands.command(help="Ban a member")
    @commands.has_permissions(ban_members=True)
    async def ban(self, ctx, user: discord.User, *, reason: str = "No reason provided"):
        await ctx.guild.ban(user, reason=f"{ctx.author}: {reason}", delete_message_days=0)
        await bot.log_case(ctx.guild, "ban", user, ctx.author, reason)
        await ctx.send(embed=ok(f"Banned {user}"))

    @commands.command(help="Ban then unban to purge a member's messages")
    @commands.has_permissions(ban_members=True)
    async def softban(self, ctx, member: discord.Member, *, reason: str = "Softban"):
        await ctx.guild.ban(member, reason=reason, delete_message_days=1)
        await ctx.guild.unban(member, reason=reason)
        await bot.log_case(ctx.guild, "softban", member, ctx.author, reason)
        await ctx.send(embed=ok(f"Softbanned {member}"))

    @commands.command(help="Unban a user by ID")
    @commands.has_permissions(ban_members=True)
    async def unban(self, ctx, user_id: int, *, reason: str = "No reason provided"):
        user = await bot.fetch_user(user_id)
        await ctx.guild.unban(user, reason=f"{ctx.author}: {reason}")
        await bot.log_case(ctx.guild, "unban", user, ctx.author, reason)
        await ctx.send(embed=ok(f"Unbanned {user}"))

    @commands.command(help="Timeout a member (e.g. 10m, 2h)")
    @commands.has_permissions(moderate_members=True)
    async def timeout(self, ctx, member: discord.Member, duration: str = "10m", *,
                      reason: str = "No reason provided"):
        seconds = parse_duration(duration) or 600
        await member.timeout(discord.utils.utcnow() + timedelta(seconds=seconds),
                            reason=f"{ctx.author}: {reason}")
        await bot.log_case(ctx.guild, "timeout", member, ctx.author, reason, seconds)
        await ctx.send(embed=ok(f"Timed out {member} for {human_delta(seconds)}"))

    @commands.command(aliases=["untimeout"], help="Remove a timeout")
    @commands.has_permissions(moderate_members=True)
    async def unmute(self, ctx, member: discord.Member):
        await member.timeout(None, reason=f"{ctx.author}: unmute")
        await ctx.send(embed=ok(f"Timeout removed for {member}"))

    @commands.command(help="Mute a member (alias of timeout)")
    @commands.has_permissions(moderate_members=True)
    async def mute(self, ctx, member: discord.Member, duration: str = "1h", *, reason: str = "Muted"):
        await self.timeout(ctx, member, duration, reason=reason)

    @commands.command(aliases=["clear"], help="Bulk delete messages")
    @commands.has_permissions(manage_messages=True)
    async def purge(self, ctx, amount: int = 10, member: discord.Member | None = None):
        amount = max(1, min(amount, 200))
        check = (lambda m: m.author == member) if member else None
        deleted = await ctx.channel.purge(limit=amount + 1, check=check)
        await ctx.send(embed=ok(f"Deleted {len(deleted)} messages"), delete_after=5)

    @commands.command(help="Delete only bot messages")
    @commands.has_permissions(manage_messages=True)
    async def purgebots(self, ctx, amount: int = 20):
        deleted = await ctx.channel.purge(limit=amount, check=lambda m: m.author.bot)
        await ctx.send(embed=ok(f"Deleted {len(deleted)} bot messages"), delete_after=5)

    @commands.command(help="Delete messages containing text")
    @commands.has_permissions(manage_messages=True)
    async def purgecontains(self, ctx, *, text: str):
        deleted = await ctx.channel.purge(limit=100, check=lambda m: text.lower() in m.content.lower())
        await ctx.send(embed=ok(f"Deleted {len(deleted)} messages"), delete_after=5)

    @commands.command(help="Set channel slowmode in seconds")
    @commands.has_permissions(manage_channels=True)
    async def slowmode(self, ctx, seconds: int = 0):
        await ctx.channel.edit(slowmode_delay=max(0, min(seconds, 21600)))
        await ctx.send(embed=ok(f"Slowmode set to {seconds}s"))

    @commands.command(help="Lock a channel")
    @commands.has_permissions(manage_channels=True)
    async def lock(self, ctx, channel: discord.TextChannel | None = None):
        channel = channel or ctx.channel
        await channel.set_permissions(ctx.guild.default_role, send_messages=False)
        await ctx.send(embed=ok(f"Locked {channel.mention}"))

    @commands.command(help="Unlock a channel")
    @commands.has_permissions(manage_channels=True)
    async def unlock(self, ctx, channel: discord.TextChannel | None = None):
        channel = channel or ctx.channel
        await channel.set_permissions(ctx.guild.default_role, send_messages=None)
        await ctx.send(embed=ok(f"Unlocked {channel.mention}"))

    @commands.command(help="Hide a channel from everyone")
    @commands.has_permissions(manage_channels=True)
    async def hide(self, ctx, channel: discord.TextChannel | None = None):
        channel = channel or ctx.channel
        await channel.set_permissions(ctx.guild.default_role, view_channel=False)
        await ctx.send(embed=ok(f"Hid {channel.mention}"))

    @commands.command(help="Unhide a channel")
    @commands.has_permissions(manage_channels=True)
    async def unhide(self, ctx, channel: discord.TextChannel | None = None):
        channel = channel or ctx.channel
        await channel.set_permissions(ctx.guild.default_role, view_channel=None)
        await ctx.send(embed=ok(f"Unhid {channel.mention}"))

    @commands.command(help="Change a member's nickname")
    @commands.has_permissions(manage_nicknames=True)
    async def nick(self, ctx, member: discord.Member, *, nickname: str = ""):
        await member.edit(nick=nickname or None)
        await ctx.send(embed=ok(f"Nickname updated for {member}"))

    @commands.command(help="Strip hoisting characters from nicknames")
    @commands.has_permissions(manage_nicknames=True)
    async def dehoist(self, ctx):
        count = 0
        for member in ctx.guild.members:
            if member.display_name[:1] in "!?-_.=+*#~":
                try:
                    await member.edit(nick=member.display_name.lstrip("!?-_.=+*#~") or "member")
                    count += 1
                except discord.HTTPException:
                    continue
        await ctx.send(embed=ok(f"Dehoisted {count} members"))

    @commands.command(help="Add a role to a member")
    @commands.has_permissions(manage_roles=True)
    async def role(self, ctx, member: discord.Member, *, role: discord.Role):
        await member.add_roles(role, reason=str(ctx.author))
        await ctx.send(embed=ok(f"Gave {role.name} to {member}"))

    @commands.command(help="Remove a role from a member")
    @commands.has_permissions(manage_roles=True)
    async def removerole(self, ctx, member: discord.Member, *, role: discord.Role):
        await member.remove_roles(role, reason=str(ctx.author))
        await ctx.send(embed=ok(f"Removed {role.name} from {member}"))

    @commands.command(help="Give a role to every member")
    @commands.has_permissions(administrator=True)
    async def roleall(self, ctx, *, role: discord.Role):
        count = 0
        for member in ctx.guild.members:
            if role not in member.roles:
                try:
                    await member.add_roles(role, reason="roleall")
                    count += 1
                except discord.HTTPException:
                    continue
        await ctx.send(embed=ok(f"Added {role.name} to {count} members"))

    @commands.command(help="Create a role")
    @commands.has_permissions(manage_roles=True)
    async def createrole(self, ctx, *, name: str):
        role = await ctx.guild.create_role(name=name, reason=str(ctx.author))
        await ctx.send(embed=ok(f"Created {role.mention}"))

    @commands.command(help="Delete a role")
    @commands.has_permissions(manage_roles=True)
    async def deleterole(self, ctx, *, role: discord.Role):
        await role.delete(reason=str(ctx.author))
        await ctx.send(embed=ok("Role deleted"))

    @commands.command(help="Move a member to a voice channel")
    @commands.has_permissions(move_members=True)
    async def voicemove(self, ctx, member: discord.Member, *, channel: discord.VoiceChannel):
        await member.move_to(channel)
        await ctx.send(embed=ok(f"Moved {member} to {channel.name}"))

    @commands.command(help="Disconnect a member from voice")
    @commands.has_permissions(move_members=True)
    async def voicekick(self, ctx, member: discord.Member):
        await member.move_to(None)
        await ctx.send(embed=ok(f"Disconnected {member}"))

    @commands.command(help="Server-mute a member in voice")
    @commands.has_permissions(mute_members=True)
    async def voicemute(self, ctx, member: discord.Member):
        await member.edit(mute=True)
        await ctx.send(embed=ok(f"Voice-muted {member}"))

    @commands.command(help="Un-server-mute a member")
    @commands.has_permissions(mute_members=True)
    async def voiceunmute(self, ctx, member: discord.Member):
        await member.edit(mute=False)
        await ctx.send(embed=ok(f"Voice-unmuted {member}"))

    @commands.command(help="Deafen a member in voice")
    @commands.has_permissions(deafen_members=True)
    async def voicedeafen(self, ctx, member: discord.Member):
        await member.edit(deafen=True)
        await ctx.send(embed=ok(f"Deafened {member}"))

    @commands.command(help="Undeafen a member")
    @commands.has_permissions(deafen_members=True)
    async def voiceundeafen(self, ctx, member: discord.Member):
        await member.edit(deafen=False)
        await ctx.send(embed=ok(f"Undeafened {member}"))

    @commands.command(help="Pin a message by ID")
    @commands.has_permissions(manage_messages=True)
    async def pin(self, ctx, message_id: int):
        msg = await ctx.channel.fetch_message(message_id)
        await msg.pin()
        await ctx.send(embed=ok("Pinned"))

    @commands.command(help="Unpin a message by ID")
    @commands.has_permissions(manage_messages=True)
    async def unpin(self, ctx, message_id: int):
        msg = await ctx.channel.fetch_message(message_id)
        await msg.unpin()
        await ctx.send(embed=ok("Unpinned"))

    @commands.command(help="Show the last deleted message")
    async def snipe(self, ctx):
        data = bot.snipes.get(ctx.channel.id)
        if not data:
            return await ctx.send(embed=err("Nothing to snipe here."))
        author, content, avatar = data
        e = embed(content, title=f"Deleted message from {author}")
        e.set_thumbnail(url=avatar)
        await ctx.send(embed=e)

    @commands.command(help="Show recent cases for a member (dashboard link)")
    @commands.has_permissions(moderate_members=True)
    async def cases(self, ctx, member: discord.Member | None = None):
        target = member or ctx.author
        await ctx.send(embed=embed(
            f"Full case history for {target.mention} lives on the dashboard:\n"
            f"{DASHBOARD_URL}/dashboard/{ctx.guild.id}/moderation", title="Cases"))

    @commands.command(help="Set a case reason on the dashboard")
    @commands.has_permissions(moderate_members=True)
    async def reason(self, ctx, case_number: int, *, reason: str):
        await bot.log_event(ctx.guild.id, "mod", "case_reason",
                            f"Case #{case_number} reason set by {ctx.author}: {reason}")
        await ctx.send(embed=ok(f"Logged a reason update for case #{case_number}"))


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


class Security(commands.Cog):
    """Lockdown, raid mode, anti-nuke controls."""

    @commands.command(help="Lock every channel in the server")
    @commands.has_permissions(administrator=True)
    async def lockdown(self, ctx, *, reason: str = "Manual lockdown"):
        msg = await ctx.send(embed=embed("Locking the server…"))
        count = await set_lockdown(ctx.guild, True, f"{ctx.author}: {reason}")
        await msg.edit(embed=ok(f"Locked {count} channels"))

    @commands.command(help="Lift an active lockdown")
    @commands.has_permissions(administrator=True)
    async def unlockdown(self, ctx):
        msg = await ctx.send(embed=embed("Unlocking the server…"))
        count = await set_lockdown(ctx.guild, False, str(ctx.author))
        await msg.edit(embed=ok(f"Unlocked {count} channels"))

    @commands.command(help="Turn raid mode on or off")
    @commands.has_permissions(administrator=True)
    async def raidmode(self, ctx, state: str = "status"):
        if state.lower() in ("on", "enable", "true"):
            bot.raid_mode.add(ctx.guild.id)
            await bot.log_security(ctx.guild.id, "antiraid", "raid_mode_on", "high", ctx.author, "manual")
            return await ctx.send(embed=ok("Raid mode is **on** — new joins are screened hard."))
        if state.lower() in ("off", "disable", "false"):
            bot.raid_mode.discard(ctx.guild.id)
            return await ctx.send(embed=ok("Raid mode is **off**."))
        await ctx.send(embed=embed(f"Raid mode: **{'on' if ctx.guild.id in bot.raid_mode else 'off'}**"))

    @commands.command(help="Anti-nuke status")
    @commands.has_permissions(administrator=True)
    async def antinuke(self, ctx):
        config = await bot.guild_config(ctx.guild.id, force=True)
        an = bot.module(config, "antinuke")
        watch = an.get("watch") or {}
        active = [k for k, v in watch.items() if (v or {}).get("enabled")]
        await ctx.send(embed=embed(
            f"Enabled: **{bot.enabled(config, 'antinuke', True)}**\n"
            f"Punishment: **{an.get('punishment', 'ban')}**\n"
            f"Watching {len(active)} action types.\n"
            f"Tune it at {DASHBOARD_URL}/dashboard/{ctx.guild.id}/settings",
            title="Anti-Nuke"))

    @commands.command(help="Anti-raid status")
    @commands.has_permissions(administrator=True)
    async def antiraid(self, ctx):
        config = await bot.guild_config(ctx.guild.id, force=True)
        ar = bot.module(config, "antiraid")
        await ctx.send(embed=embed(
            f"Enabled: **{bot.enabled(config, 'antiraid', True)}**\n"
            f"Raid mode: **{'on' if ctx.guild.id in bot.raid_mode else 'off'}**\n"
            f"Threshold: **{ar.get('join_threshold', 8)} joins / "
            f"{ar.get('join_window_seconds', 10)}s**\n"
            f"Minimum account age: **{ar.get('min_account_age_days', 7)}d**\n"
            f"Action: **{ar.get('action', 'kick')}**",
            title="Anti-Raid"))

    @commands.command(help="Security summary")
    @commands.has_permissions(moderate_members=True)
    async def security(self, ctx):
        config = await bot.guild_config(ctx.guild.id, force=True)
        lines = []
        for key in ("automod", "wordfilter", "antinuke", "antiraid", "logging"):
            lines.append(f"{'🟢' if bot.enabled(config, key, True) else '⚪'} {key}")
        await ctx.send(embed=embed("\n".join(lines) +
                                   f"\n\nDashboard: {DASHBOARD_URL}/dashboard/{ctx.guild.id}",
                                   title="Security status"))

    @commands.command(help="Reload this server's settings from the dashboard")
    @commands.has_permissions(administrator=True)
    async def reload(self, ctx):
        await bot.guild_config(ctx.guild.id, force=True)
        await ctx.send(embed=ok("Settings reloaded from the dashboard."))

    @commands.command(help="Toggle an AutoMod rule quickly")
    @commands.has_permissions(administrator=True)
    async def automod(self, ctx, rule: str | None = None):
        config = await bot.guild_config(ctx.guild.id, force=True)
        mods = (bot.module(config, "automod").get("modules") or {})
        if rule is None:
            on = [k for k, v in mods.items() if (v or {}).get("enabled")]
            return await ctx.send(embed=embed(
                f"Active rules ({len(on)}): " + ", ".join(f"`{r}`" for r in on) +
                f"\n\nChange them at {DASHBOARD_URL}/dashboard/{ctx.guild.id}/settings",
                title="AutoMod"))
        cfg = mods.get(rule)
        if cfg is None:
            return await ctx.send(embed=err(f"Unknown rule `{rule}`."))
        await ctx.send(embed=embed(
            f"`{rule}` → enabled: **{cfg.get('enabled')}**, action: **{cfg.get('action')}**\n"
            f"Edit on the dashboard to change it.", title="AutoMod"))

    @commands.command(help="Show blocked words")
    @commands.has_permissions(manage_messages=True)
    async def filter(self, ctx):
        await bot.guild_config(ctx.guild.id, force=True)
        words = bot.filter_cache.get(str(ctx.guild.id), [])
        await ctx.send(embed=embed(
            (", ".join(f"`{w['word']}`" for w in words[:60]) or "No blocked words yet.") +
            f"\n\nManage at {DASHBOARD_URL}/dashboard/{ctx.guild.id}/settings", title="Word filter"))


# ---------------------------------------------------------------------------
# Information / utility
# ---------------------------------------------------------------------------


class Info(commands.Cog):
    """Server, user and bot information."""

    @commands.command(help="Show the RM help menu")
    async def help(self, ctx, *, query: str | None = None):
        if query:
            cmd = bot.get_command(query)
            if not cmd:
                return await ctx.send(embed=err(f"No command named `{query}`."))
            return await ctx.send(embed=embed(
                f"{cmd.help or 'No description.'}\n\n"
                f"**Usage:** `{ctx.prefix}{cmd.qualified_name} {cmd.signature}`\n"
                f"**Aliases:** {', '.join(cmd.aliases) or 'none'}",
                title=f"{ctx.prefix}{cmd.qualified_name}"))
        e = embed(f"`{len(set(bot.walk_commands()))}` commands across "
                  f"{len(bot.cogs)} categories.\nDashboard: {DASHBOARD_URL}",
                  title="RM help")
        for name, cog in bot.cogs.items():
            names = sorted({c.name for c in cog.get_commands()})
            if names:
                e.add_field(name=f"{name} ({len(names)})",
                            value=", ".join(f"`{n}`" for n in names)[:1020], inline=False)
        await ctx.send(embed=e)

    @commands.command(aliases=["commands"], help="Count every command")
    async def commandlist(self, ctx):
        await ctx.send(embed=embed(f"RM has **{len(set(bot.walk_commands()))}** commands. "
                                   f"Browse them all at {DASHBOARD_URL}/#commands"))

    @commands.command(help="Bot status and uptime")
    async def botinfo(self, ctx):
        up = human_delta((datetime.now(timezone.utc) - STARTED_AT).total_seconds())
        await ctx.send(embed=embed(
            f"**Version** {VERSION}\n**Uptime** {up}\n**Latency** {round(bot.latency*1000)}ms\n"
            f"**Servers** {len(bot.guilds)}\n**Users** {sum(g.member_count or 0 for g in bot.guilds):,}\n"
            f"**Commands run** {bot.commands_processed}\n**Python** {platform.python_version()}\n"
            f"**discord.py** {discord.__version__}", title="RM"))

    @commands.command(help="Show latency")
    async def ping(self, ctx):
        await ctx.send(embed=embed(f"🏓 {round(bot.latency * 1000)}ms"))

    @commands.command(help="Show uptime")
    async def uptime(self, ctx):
        await ctx.send(embed=embed(human_delta((datetime.now(timezone.utc) - STARTED_AT).total_seconds())))

    @commands.command(help="Dashboard link")
    async def dashboard(self, ctx):
        target = f"/dashboard/{ctx.guild.id}" if ctx.guild else ""
        await ctx.send(embed=embed(f"{DASHBOARD_URL}{target}", title="RM dashboard"))

    @commands.command(help="Invite RM to another server")
    async def invite(self, ctx):
        await ctx.send(embed=embed(
            f"https://discord.com/oauth2/authorize?client_id={bot.user.id}"
            f"&scope=bot+applications.commands&permissions=1099780064374",
            title="Invite RM"))

    @commands.command(help="Show the prefixes here")
    async def prefix(self, ctx):
        prefixes = bot.prefix_cache.get(str(ctx.guild.id), DEFAULT_PREFIXES) if ctx.guild else DEFAULT_PREFIXES
        await ctx.send(embed=embed(", ".join(f"`{p}`" for p in prefixes) + " and mentions"))

    @commands.command(help="Server information")
    async def serverinfo(self, ctx):
        g = ctx.guild
        e = embed(
            f"**Owner** <@{g.owner_id}>\n**Created** {discord.utils.format_dt(g.created_at, 'R')}\n"
            f"**Members** {g.member_count}\n**Channels** {len(g.channels)}\n**Roles** {len(g.roles)}\n"
            f"**Emojis** {len(g.emojis)}\n**Boosts** {g.premium_subscription_count}\n"
            f"**Verification** {g.verification_level}", title=g.name)
        if g.icon:
            e.set_thumbnail(url=g.icon.url)
        await ctx.send(embed=e)

    @commands.command(help="User information")
    async def userinfo(self, ctx, member: discord.Member | None = None):
        m = member or ctx.author
        roles = [r.mention for r in reversed(m.roles) if not r.is_default()][:20]
        e = embed(
            f"**ID** {m.id}\n**Created** {discord.utils.format_dt(m.created_at, 'R')}\n"
            f"**Joined** {discord.utils.format_dt(m.joined_at, 'R') if m.joined_at else '?'}\n"
            f"**Top role** {m.top_role.mention}\n**Roles** {' '.join(roles) or 'none'}",
            title=str(m))
        e.set_thumbnail(url=m.display_avatar.url)
        await ctx.send(embed=e)

    @commands.command(help="Role information")
    async def roleinfo(self, ctx, *, role: discord.Role):
        await ctx.send(embed=embed(
            f"**ID** {role.id}\n**Members** {len(role.members)}\n**Colour** {role.colour}\n"
            f"**Position** {role.position}\n**Mentionable** {role.mentionable}\n"
            f"**Created** {discord.utils.format_dt(role.created_at, 'R')}", title=role.name))

    @commands.command(help="Channel information")
    async def channelinfo(self, ctx, channel: discord.TextChannel | None = None):
        c = channel or ctx.channel
        await ctx.send(embed=embed(
            f"**ID** {c.id}\n**Topic** {c.topic or 'none'}\n**NSFW** {c.is_nsfw()}\n"
            f"**Slowmode** {c.slowmode_delay}s\n"
            f"**Created** {discord.utils.format_dt(c.created_at, 'R')}", title=f"#{c.name}"))

    @commands.command(help="Show a user's avatar")
    async def avatar(self, ctx, member: discord.Member | None = None):
        m = member or ctx.author
        e = embed(f"[Open]({m.display_avatar.url})", title=f"{m}'s avatar")
        e.set_image(url=m.display_avatar.url)
        await ctx.send(embed=e)

    @commands.command(help="Show a user's banner")
    async def banner(self, ctx, member: discord.Member | None = None):
        user = await bot.fetch_user((member or ctx.author).id)
        if not user.banner:
            return await ctx.send(embed=err("No banner set."))
        e = embed("", title=f"{user}'s banner")
        e.set_image(url=user.banner.url)
        await ctx.send(embed=e)

    @commands.command(help="Server icon")
    async def servericon(self, ctx):
        if not ctx.guild.icon:
            return await ctx.send(embed=err("No server icon."))
        e = embed("", title=ctx.guild.name)
        e.set_image(url=ctx.guild.icon.url)
        await ctx.send(embed=e)

    @commands.command(help="Member count")
    async def membercount(self, ctx):
        humans = sum(1 for m in ctx.guild.members if not m.bot)
        await ctx.send(embed=embed(f"**{ctx.guild.member_count}** total · {humans} humans · "
                                   f"{(ctx.guild.member_count or 0) - humans} bots"))

    @commands.command(help="List server roles")
    async def roles(self, ctx):
        names = [r.name for r in reversed(ctx.guild.roles) if not r.is_default()]
        await ctx.send(embed=embed(", ".join(names)[:3800] or "none",
                                   title=f"{len(names)} roles"))

    @commands.command(help="List server emojis")
    async def emojis(self, ctx):
        await ctx.send(embed=embed(" ".join(str(e) for e in ctx.guild.emojis)[:3800] or "none",
                                   title=f"{len(ctx.guild.emojis)} emojis"))

    @commands.command(help="List boosters")
    async def boosters(self, ctx):
        names = [m.mention for m in ctx.guild.premium_subscribers]
        await ctx.send(embed=embed(" ".join(names) or "No boosters yet.",
                                   title=f"{len(names)} boosters"))

    @commands.command(help="List bots in the server")
    async def bots(self, ctx):
        names = [m.mention for m in ctx.guild.members if m.bot]
        await ctx.send(embed=embed(" ".join(names)[:3800] or "none", title=f"{len(names)} bots"))

    @commands.command(help="Show a member's key permissions")
    async def permissions(self, ctx, member: discord.Member | None = None):
        m = member or ctx.author
        perms = [n.replace("_", " ") for n, v in m.guild_permissions if v]
        await ctx.send(embed=embed(", ".join(perms)[:3800], title=f"{m} permissions"))

    @commands.command(help="Look up any user by ID")
    async def whois(self, ctx, user_id: int):
        user = await bot.fetch_user(user_id)
        e = embed(f"**ID** {user.id}\n**Created** {discord.utils.format_dt(user.created_at, 'R')}",
                  title=str(user))
        e.set_thumbnail(url=user.display_avatar.url)
        await ctx.send(embed=e)

    @commands.command(help="Show a snowflake's timestamp")
    async def snowflake(self, ctx, snowflake: int):
        created = discord.utils.snowflake_time(snowflake)
        await ctx.send(embed=embed(f"{discord.utils.format_dt(created)} "
                                   f"({discord.utils.format_dt(created, 'R')})"))

    @commands.command(help="Show the server's ban count")
    @commands.has_permissions(ban_members=True)
    async def bans(self, ctx):
        count = 0
        async for _ in ctx.guild.bans(limit=None):
            count += 1
        await ctx.send(embed=embed(f"**{count}** bans"))

    @commands.command(help="Show current invites")
    @commands.has_permissions(manage_guild=True)
    async def invites(self, ctx):
        invs = await ctx.guild.invites()
        await ctx.send(embed=embed(
            "\n".join(f"`{i.code}` · {i.uses} uses · {i.inviter}" for i in invs[:20]) or "none",
            title=f"{len(invs)} invites"))


# ---------------------------------------------------------------------------
# Utility tools
# ---------------------------------------------------------------------------


class Tools(commands.Cog):
    """Reminders, polls, converters and text tools."""

    @commands.command(help="Set a reminder, e.g. remind 10m water")
    async def remind(self, ctx, duration: str, *, text: str):
        seconds = parse_duration(duration)
        if not seconds:
            return await ctx.send(embed=err("Use a duration like `10m` or `2h`."))
        await ctx.send(embed=ok(f"I'll remind you in {human_delta(seconds)}."))
        await asyncio.sleep(seconds)
        try:
            await ctx.author.send(embed=embed(text, title="⏰ Reminder"))
        except discord.HTTPException:
            await ctx.send(f"{ctx.author.mention} ⏰ {text}")

    @commands.command(help="Set your AFK status")
    async def afk(self, ctx, *, reason: str = "AFK"):
        bot.afk[ctx.author.id] = reason
        await ctx.send(embed=ok(f"You're AFK: {reason}"))

    @commands.command(help="Create a reaction poll: poll question | a | b")
    async def poll(self, ctx, *, text: str):
        parts = [p.strip() for p in text.split("|") if p.strip()]
        question, options = parts[0], parts[1:10]
        digits = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"]
        if not options:
            msg = await ctx.send(embed=embed(question, title="Poll"))
            for r in ("👍", "👎"):
                await msg.add_reaction(r)
            return
        body = "\n".join(f"{digits[i]} {o}" for i, o in enumerate(options))
        msg = await ctx.send(embed=embed(body, title=question))
        for i in range(len(options)):
            await msg.add_reaction(digits[i])

    @commands.command(help="Submit a suggestion")
    async def suggest(self, ctx, *, text: str):
        config = await bot.guild_config(ctx.guild.id)
        channel_id = bot.module(config, "suggestions").get("channel_id")
        channel = ctx.guild.get_channel(int(channel_id)) if channel_id else ctx.channel
        e = embed(text, title="New suggestion")
        e.set_footer(text=f"from {ctx.author}")
        msg = await channel.send(embed=e)
        for r in ("⬆️", "⬇️"):
            await msg.add_reaction(r)
        await ctx.send(embed=ok("Suggestion submitted."))

    @commands.command(help="Start a giveaway: giveaway 1h 1 Nitro")
    @commands.has_permissions(manage_guild=True)
    async def giveaway(self, ctx, duration: str, winners: int, *, prize: str):
        seconds = parse_duration(duration) or 3600
        e = embed(f"React 🎉 to enter!\n**Winners:** {winners}\n"
                  f"**Ends:** {human_delta(seconds)}", title=f"🎉 {prize}")
        msg = await ctx.send(embed=e)
        await msg.add_reaction("🎉")
        await asyncio.sleep(seconds)
        msg = await ctx.channel.fetch_message(msg.id)
        users = set()
        for reaction in msg.reactions:
            if str(reaction.emoji) == "🎉":
                async for u in reaction.users():
                    if not u.bot:
                        users.add(u)
        if not users:
            return await ctx.send(embed=err("No valid entries."))
        picked = random.sample(list(users), min(winners, len(users)))
        await ctx.send(embed=ok(f"Winners of **{prize}**: " + ", ".join(u.mention for u in picked)))

    @commands.command(help="Send an embed: embed title | description")
    @commands.has_permissions(manage_messages=True)
    async def embed(self, ctx, *, text: str):
        title, _, desc = text.partition("|")
        await ctx.send(embed=discord.Embed(title=title.strip(), description=desc.strip(), color=BRAND))

    @commands.command(help="Repeat a message")
    @commands.has_permissions(manage_messages=True)
    async def say(self, ctx, *, text: str):
        await ctx.message.delete(delay=0)
        await ctx.send(text[:1900])

    @commands.command(help="Do maths: calc 2*(3+4)")
    async def calc(self, ctx, *, expression: str):
        if not re.fullmatch(r"[0-9\.\s\+\-\*/%\(\)]+", expression):
            return await ctx.send(embed=err("Numbers and + - * / % ( ) only."))
        try:
            await ctx.send(embed=embed(f"`{expression}` = **{eval(expression, {'__builtins__': {}})}**"))
        except Exception:
            await ctx.send(embed=err("I couldn't work that out."))

    @commands.command(help="Base64 encode")
    async def b64encode(self, ctx, *, text: str):
        await ctx.send(embed=embed(f"`{base64.b64encode(text.encode()).decode()[:1800]}`"))

    @commands.command(help="Base64 decode")
    async def b64decode(self, ctx, *, text: str):
        try:
            await ctx.send(embed=embed(f"`{base64.b64decode(text).decode()[:1800]}`"))
        except Exception:
            await ctx.send(embed=err("That isn't valid base64."))

    @commands.command(help="Hash text with sha256")
    async def hash(self, ctx, *, text: str):
        await ctx.send(embed=embed(f"`{hashlib.sha256(text.encode()).hexdigest()}`"))

    @commands.command(help="Generate a strong password")
    async def password(self, ctx, length: int = 20):
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
        pwd = "".join(random.choice(alphabet) for _ in range(max(8, min(length, 64))))
        try:
            await ctx.author.send(embed=embed(f"`{pwd}`", title="Your password"))
            await ctx.send(embed=ok("Sent to your DMs."))
        except discord.HTTPException:
            await ctx.send(embed=err("I can't DM you."))

    @commands.command(help="Shorten long text into a code block")
    async def code(self, ctx, *, text: str):
        await ctx.send(f"```\n{text[:1900]}\n```")

    @commands.command(help="Reverse text")
    async def reverse(self, ctx, *, text: str):
        await ctx.send(embed=embed(text[::-1][:1900]))

    @commands.command(help="UPPERCASE text")
    async def upper(self, ctx, *, text: str):
        await ctx.send(embed=embed(text.upper()[:1900]))

    @commands.command(help="lowercase text")
    async def lower(self, ctx, *, text: str):
        await ctx.send(embed=embed(text.lower()[:1900]))

    @commands.command(help="mOcK tExT")
    async def mock(self, ctx, *, text: str):
        await ctx.send(embed=embed("".join(
            c.upper() if i % 2 else c.lower() for i, c in enumerate(text))[:1900]))

    @commands.command(help="s p a c e d text")
    async def spaced(self, ctx, *, text: str):
        await ctx.send(embed=embed(" ".join(text)[:1900]))

    @commands.command(help="Convert text to emoji letters")
    async def emojify(self, ctx, *, text: str):
        out = "".join(
            f":regional_indicator_{c}: " if c.isalpha() else ("   " if c == " " else c)
            for c in text.lower())
        await ctx.send(embed=embed(out[:1900]))

    @commands.command(help="Count characters and words")
    async def count(self, ctx, *, text: str):
        await ctx.send(embed=embed(f"**{len(text)}** characters · **{len(text.split())}** words"))

    @commands.command(help="Convert units of time to seconds")
    async def toseconds(self, ctx, duration: str):
        secs = parse_duration(duration)
        await ctx.send(embed=embed(f"{duration} = **{secs or 0}** seconds"))

    @commands.command(help="Show the current time in UTC")
    async def time(self, ctx):
        await ctx.send(embed=embed(discord.utils.format_dt(discord.utils.utcnow(), "F")))

    @commands.command(help="Countdown to a duration from now")
    async def timestamp(self, ctx, duration: str = "1h"):
        secs = parse_duration(duration) or 3600
        when = discord.utils.utcnow() + timedelta(seconds=secs)
        await ctx.send(embed=embed(f"{discord.utils.format_dt(when, 'F')} "
                                   f"({discord.utils.format_dt(when, 'R')})\n"
                                   f"`<t:{int(when.timestamp())}:R>`"))

    @commands.command(help="Convert a colour hex to a preview")
    async def color(self, ctx, hex_code: str):
        value = hex_code.lstrip("#")
        if not re.fullmatch(r"[0-9a-fA-F]{6}", value):
            return await ctx.send(embed=err("Use a hex colour like `#EF4444`."))
        e = discord.Embed(title=f"#{value.upper()}", color=int(value, 16))
        e.set_image(url=f"https://singlecolorimage.com/get/{value}/200x80")
        await ctx.send(embed=e)

    @commands.command(help="Make a QR code for text")
    async def qr(self, ctx, *, text: str):
        url = "https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=" + \
              discord.utils.escape_markdown(text).replace(" ", "%20")
        e = embed("", title="QR code")
        e.set_image(url=url)
        await ctx.send(embed=e)

    @commands.command(help="Pick a random member")
    async def randommember(self, ctx):
        await ctx.send(embed=embed(random.choice(ctx.guild.members).mention))

    @commands.command(help="Show a big first-letter banner")
    async def bigtext(self, ctx, *, text: str):
        await ctx.send(embed=embed(f"# {text[:60]}"))


# ---------------------------------------------------------------------------
# Fun
# ---------------------------------------------------------------------------

EIGHTBALL = [
    "It is certain.", "Without a doubt.", "Most likely.", "Ask again later.",
    "Don't count on it.", "My sources say no.", "Very doubtful.", "Yes — definitely.",
    "Signs point to yes.", "Better not tell you now.",
]


class Fun(commands.Cog):
    """Games, randomisers and silly things."""

    @commands.command(name="8ball", help="Ask the magic 8 ball")
    async def eightball(self, ctx, *, question: str):
        await ctx.send(embed=embed(f"🎱 {random.choice(EIGHTBALL)}", title=question[:250]))

    @commands.command(help="Flip a coin")
    async def coinflip(self, ctx):
        await ctx.send(embed=embed(f"🪙 **{random.choice(['Heads', 'Tails'])}**"))

    @commands.command(help="Roll dice, e.g. roll 2d20")
    async def roll(self, ctx, dice: str = "1d6"):
        match = re.fullmatch(r"(\d{1,2})?d(\d{1,3})", dice.lower())
        if not match:
            return await ctx.send(embed=err("Use a format like `2d20`."))
        count = int(match.group(1) or 1)
        sides = int(match.group(2))
        rolls = [random.randint(1, sides) for _ in range(min(count, 25))]
        await ctx.send(embed=embed(f"{' + '.join(map(str, rolls))} = **{sum(rolls)}**"))

    @commands.command(help="Choose between options separated by |")
    async def choose(self, ctx, *, options: str):
        picks = [o.strip() for o in options.split("|") if o.strip()]
        if len(picks) < 2:
            return await ctx.send(embed=err("Give me at least two options split by `|`."))
        await ctx.send(embed=embed(f"I pick **{random.choice(picks)}**"))

    @commands.command(help="Rate something out of ten")
    async def rate(self, ctx, *, thing: str):
        score = int(hashlib.md5(thing.lower().encode()).hexdigest(), 16) % 11
        await ctx.send(embed=embed(f"I rate **{thing}** a **{score}/10**"))

    @commands.command(help="Ship two members")
    async def ship(self, ctx, a: discord.Member, b: discord.Member | None = None):
        b = b or ctx.author
        pct = int(hashlib.md5(f"{min(a.id,b.id)}{max(a.id,b.id)}".encode()).hexdigest(), 16) % 101
        bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
        await ctx.send(embed=embed(f"{a.display_name} 💞 {b.display_name}\n`{bar}` **{pct}%**"))

    @commands.command(help="How gay is it (joke meter)")
    async def howgay(self, ctx, member: discord.Member | None = None):
        m = member or ctx.author
        await ctx.send(embed=embed(f"{m.display_name} is **{m.id % 101}%** 🌈 (not scientific)"))

    @commands.command(help="Simulate a fight")
    async def fight(self, ctx, member: discord.Member):
        winner = random.choice([ctx.author, member])
        await ctx.send(embed=embed(f"⚔️ **{winner.display_name}** wins!"))

    @commands.command(help="Random number in a range")
    async def random(self, ctx, low: int = 1, high: int = 100):
        low, high = min(low, high), max(low, high)
        await ctx.send(embed=embed(f"🎲 **{random.randint(low, high)}**"))

    @commands.command(help="Rock paper scissors")
    async def rps(self, ctx, choice: str):
        options = ["rock", "paper", "scissors"]
        if choice.lower() not in options:
            return await ctx.send(embed=err("Pick rock, paper or scissors."))
        mine = random.choice(options)
        beats = {"rock": "scissors", "paper": "rock", "scissors": "paper"}
        if mine == choice.lower():
            result = "It's a draw!"
        elif beats[choice.lower()] == mine:
            result = "You win!"
        else:
            result = "I win!"
        await ctx.send(embed=embed(f"I chose **{mine}** — {result}"))

    @commands.command(help="Flip a table")
    async def tableflip(self, ctx):
        await ctx.send("(╯°□°）╯︵ ┻━┻")

    @commands.command(help="Put the table back")
    async def unflip(self, ctx):
        await ctx.send("┬─┬ ノ( ゜-゜ノ)")

    @commands.command(help="Shrug")
    async def shrug(self, ctx):
        await ctx.send(r"¯\_(ツ)_/¯")

    @commands.command(help="Random cat picture")
    async def cat(self, ctx):
        e = embed("", title="🐱")
        e.set_image(url="https://cataas.com/cat?" + str(random.randint(1, 99999)))
        await ctx.send(embed=e)

    @commands.command(help="Random dog picture")
    async def dog(self, ctx):
        if bot.session:
            try:
                async with bot.session.get("https://dog.ceo/api/breeds/image/random") as r:
                    data = await r.json()
                e = embed("", title="🐶")
                e.set_image(url=data["message"])
                return await ctx.send(embed=e)
            except Exception:
                pass
        await ctx.send(embed=err("Couldn't fetch a dog right now."))

    @commands.command(help="Would you rather")
    async def wyr(self, ctx):
        pairs = [
            ("always be 10 minutes late", "always be 20 minutes early"),
            ("have no internet", "have no music"),
            ("fight one horse-sized duck", "100 duck-sized horses"),
            ("read minds", "be invisible"),
            ("never sleep again", "never eat again"),
        ]
        a, b = random.choice(pairs)
        await ctx.send(embed=embed(f"Would you rather **{a}** or **{b}**?"))

    @commands.command(help="Random truth question")
    async def truth(self, ctx):
        qs = ["What's your biggest fear?", "Worst lie you've told?",
              "Last thing you searched?", "Biggest regret?", "Who do you text most?"]
        await ctx.send(embed=embed(random.choice(qs), title="Truth"))

    @commands.command(help="Random dare")
    async def dare(self, ctx):
        ds = ["Send the 5th photo in your gallery.", "Change your nickname for an hour.",
              "Type only in emoji for 10 minutes.", "Compliment three people here."]
        await ctx.send(embed=embed(random.choice(ds), title="Dare"))

    @commands.command(help="Random joke")
    async def joke(self, ctx):
        jokes = [
            "I told my computer I needed a break — it said 'no problem, I'll go to sleep'.",
            "Why do programmers prefer dark mode? Because light attracts bugs.",
            "There are 10 kinds of people: those who understand binary and those who don't.",
        ]
        await ctx.send(embed=embed(random.choice(jokes)))

    @commands.command(help="Random compliment")
    async def compliment(self, ctx, member: discord.Member | None = None):
        m = member or ctx.author
        cs = ["you make servers better", "your timing is impeccable", "you're the good kind of chaos"]
        await ctx.send(embed=embed(f"{m.mention}, {random.choice(cs)}."))

    @commands.command(help="Random fact")
    async def fact(self, ctx):
        facts = ["Honey never spoils.", "Octopuses have three hearts.",
                 "Bananas are berries; strawberries aren't."]
        await ctx.send(embed=embed(random.choice(facts)))

    @commands.command(help="Roast someone lightly")
    async def roast(self, ctx, member: discord.Member):
        rs = ["you bring everyone so much joy… when you leave",
              "you're proof that even a broken clock is right twice a day",
              "your secrets are safe with me — I never listen"]
        await ctx.send(embed=embed(f"{member.mention}, {random.choice(rs)}."))

    @commands.command(help="Slot machine")
    async def slots(self, ctx):
        icons = ["🍒", "🍋", "🔔", "💎", "7️⃣"]
        spin = [random.choice(icons) for _ in range(3)]
        win = len(set(spin)) == 1
        await ctx.send(embed=embed(f"{' | '.join(spin)}\n" + ("**Jackpot!**" if win else "No luck.")))

    @commands.command(help="Guess a number 1-10")
    async def guess(self, ctx, number: int):
        secret = random.randint(1, 10)
        await ctx.send(embed=embed(f"I was thinking of **{secret}** — "
                                   f"{'you got it!' if secret == number else 'try again.'}"))

    @commands.command(help="Countdown from a number")
    async def countdown(self, ctx, start: int = 5):
        start = max(1, min(start, 10))
        msg = await ctx.send(embed=embed(str(start)))
        for i in range(start - 1, -1, -1):
            await asyncio.sleep(1)
            await msg.edit(embed=embed(str(i) if i else "🎉 Go!"))


# ---------------------------------------------------------------------------
# Economy & levels (dashboard-backed counters)
# ---------------------------------------------------------------------------


class Economy(commands.Cog):
    """Currency, rewards and leaderboards."""

    @commands.command(aliases=["bal"], help="Check your balance")
    async def balance(self, ctx, member: discord.Member | None = None):
        m = member or ctx.author
        await ctx.send(embed=embed(
            f"Balances live on the dashboard leaderboard.\n"
            f"{DASHBOARD_URL}/dashboard/{ctx.guild.id}", title=f"{m.display_name}'s wallet"))

    @commands.command(help="Claim your daily reward")
    @commands.cooldown(1, 86400, commands.BucketType.user)
    async def daily(self, ctx):
        config = await bot.guild_config(ctx.guild.id)
        amount = int(bot.module(config, "economy").get("daily_amount", 250))
        await ctx.send(embed=ok(f"You claimed **{amount}** "
                                f"{bot.module(config, 'economy').get('currency_name', 'credits')}."))

    @commands.command(help="Claim your weekly reward")
    @commands.cooldown(1, 604800, commands.BucketType.user)
    async def weekly(self, ctx):
        config = await bot.guild_config(ctx.guild.id)
        amount = int(bot.module(config, "economy").get("weekly_amount", 1200))
        await ctx.send(embed=ok(f"You claimed **{amount}** this week."))

    @commands.command(help="Work for currency")
    @commands.cooldown(1, 3600, commands.BucketType.user)
    async def work(self, ctx):
        config = await bot.guild_config(ctx.guild.id)
        eco = bot.module(config, "economy")
        amount = random.randint(int(eco.get("work_min", 50)), int(eco.get("work_max", 300)))
        await ctx.send(embed=ok(f"You earned **{amount}**."))

    @commands.command(help="Gamble a coinflip")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def gamble(self, ctx, amount: int = 100):
        won = random.random() < 0.45
        await ctx.send(embed=embed(f"You {'won' if won else 'lost'} **{amount}**.",
                                   color=OK if won else BAD))

    @commands.command(help="Browse the shop")
    async def shop(self, ctx):
        await ctx.send(embed=embed(f"Shop items are managed on the dashboard:\n"
                                   f"{DASHBOARD_URL}/dashboard/{ctx.guild.id}/settings",
                                   title="Shop"))

    @commands.command(help="Show your rank")
    async def rank(self, ctx, member: discord.Member | None = None):
        m = member or ctx.author
        await ctx.send(embed=embed(f"XP and levels for {m.mention} are on the dashboard.",
                                   title="Rank"))

    @commands.command(aliases=["lb"], help="Show the leaderboard")
    async def leaderboard(self, ctx):
        await ctx.send(embed=embed(f"{DASHBOARD_URL}/dashboard/{ctx.guild.id}",
                                   title="Leaderboard"))

    @commands.command(help="Show configured level roles")
    async def levels(self, ctx):
        config = await bot.guild_config(ctx.guild.id)
        roles = bot.module(config, "leveling").get("level_roles") or []
        body = "\n".join(f"Level {r.get('level')} → <@&{r.get('role_id')}>" for r in roles)
        await ctx.send(embed=embed(body or "No level roles configured yet.", title="Level roles"))


# ---------------------------------------------------------------------------
# Tickets & roles
# ---------------------------------------------------------------------------


class Tickets(commands.Cog):
    """Support tickets and self-assign roles."""

    @commands.command(help="Open a support ticket")
    async def newticket(self, ctx, *, subject: str = "Support"):
        config = await bot.guild_config(ctx.guild.id)
        staff_roles = [ctx.guild.get_role(int(r)) for r in
                       (bot.module(config, "tickets").get("staff_roles") or [])]
        overwrites = {
            ctx.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            ctx.author: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            ctx.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        for role in staff_roles:
            if role:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        channel = await ctx.guild.create_text_channel(
            f"ticket-{ctx.author.name}"[:90], overwrites=overwrites, topic=subject)
        await channel.send(embed=embed(f"{ctx.author.mention} opened a ticket: **{subject}**\n"
                                       f"Staff will be with you shortly. Use `{ctx.prefix}closeticket` "
                                       f"when you're done.", title="Ticket"))
        await ctx.send(embed=ok(f"Ticket created: {channel.mention}"))
        await bot.log_event(ctx.guild.id, "server", "ticket_open", f"{ctx.author} opened {channel.name}")

    @commands.command(help="Close the current ticket")
    async def closeticket(self, ctx):
        if not ctx.channel.name.startswith("ticket-"):
            return await ctx.send(embed=err("This isn't a ticket channel."))
        await ctx.send(embed=embed("Closing in 5 seconds…"))
        await asyncio.sleep(5)
        await bot.log_event(ctx.guild.id, "server", "ticket_close", f"{ctx.author} closed {ctx.channel.name}")
        await ctx.channel.delete(reason=f"Ticket closed by {ctx.author}")

    @commands.command(help="Add someone to this ticket")
    async def ticketadd(self, ctx, member: discord.Member):
        await ctx.channel.set_permissions(member, view_channel=True, send_messages=True)
        await ctx.send(embed=ok(f"Added {member.mention}"))

    @commands.command(help="Remove someone from this ticket")
    async def ticketremove(self, ctx, member: discord.Member):
        await ctx.channel.set_permissions(member, overwrite=None)
        await ctx.send(embed=ok(f"Removed {member.mention}"))

    @commands.command(help="Claim this ticket as staff")
    @commands.has_permissions(manage_messages=True)
    async def claim(self, ctx):
        await ctx.send(embed=ok(f"{ctx.author.mention} claimed this ticket."))

    @commands.command(help="Publish a reaction-role message")
    @commands.has_permissions(manage_roles=True)
    async def rolepanel(self, ctx, role: discord.Role, emoji: str, *, label: str = ""):
        msg = await ctx.send(embed=embed(f"React with {emoji} for {role.mention}. {label}",
                                         title="Self roles"))
        await msg.add_reaction(emoji)
        await bot.log_event(ctx.guild.id, "server", "rolepanel",
                            f"{ctx.author} published a role panel for {role.name}")

    @commands.command(help="Give yourself a self-assignable role")
    async def iam(self, ctx, *, role: discord.Role):
        config = await bot.guild_config(ctx.guild.id)
        allowed = {str(r) for r in (bot.module(config, "welcome").get("autoroles") or [])}
        if str(role.id) not in allowed:
            return await ctx.send(embed=err("That role isn't self-assignable."))
        await ctx.author.add_roles(role)
        await ctx.send(embed=ok(f"You now have {role.name}"))

    @commands.command(help="Remove a self-assignable role")
    async def iamnot(self, ctx, *, role: discord.Role):
        await ctx.author.remove_roles(role)
        await ctx.send(embed=ok(f"Removed {role.name}"))


# ---------------------------------------------------------------------------
# Owner
# ---------------------------------------------------------------------------


def owner_only():
    async def predicate(ctx):
        return ctx.author.id in OWNER_IDS or await ctx.bot.is_owner(ctx.author)
    return commands.check(predicate)


class Owner(commands.Cog):
    """Bot-owner only tools."""

    @commands.command(help="List every server RM is in")
    @owner_only()
    async def guilds(self, ctx):
        rows = sorted(bot.guilds, key=lambda g: g.member_count or 0, reverse=True)[:25]
        await ctx.send(embed=embed("\n".join(f"`{g.id}` {g.name} — {g.member_count}" for g in rows),
                                   title=f"{len(bot.guilds)} servers"))

    @commands.command(help="Leave a server by ID")
    @owner_only()
    async def leaveguild(self, ctx, guild_id: int):
        guild = bot.get_guild(guild_id)
        if not guild:
            return await ctx.send(embed=err("Not in that server."))
        await guild.leave()
        await ctx.send(embed=ok(f"Left {guild.name}"))

    @commands.command(help="Sync slash commands")
    @owner_only()
    async def syncslash(self, ctx):
        synced = await bot.tree.sync()
        await ctx.send(embed=ok(f"Synced {len(synced)} slash commands"))

    @commands.command(help="Push the command catalog to the dashboard")
    @owner_only()
    async def pushcatalog(self, ctx):
        await bot.sync("catalog", {"commands": [
            {"name": c.qualified_name, "description": c.help or "",
             "category": c.cog_name or "General"} for c in bot.walk_commands()]})
        await ctx.send(embed=ok("Catalog pushed."))

    @commands.command(help="Set the bot's presence")
    @owner_only()
    async def presence(self, ctx, *, text: str):
        await bot.change_presence(activity=discord.Game(name=text))
        await ctx.send(embed=ok("Presence updated."))

    @commands.command(help="Check dashboard connectivity")
    @owner_only()
    async def synctest(self, ctx):
        result = await bot.sync("heartbeat", {"shard_id": 0, "status": "online",
                                              "guild_count": len(bot.guilds), "version": VERSION})
        await ctx.send(embed=(ok("Dashboard reachable and key accepted.") if result
                              else err("Dashboard unreachable or BOT_SYNC_KEY mismatch.")))

    @commands.command(help="Shut the bot down")
    @owner_only()
    async def shutdown(self, ctx):
        await ctx.send(embed=ok("Shutting down."))
        await bot.close()


# ---------------------------------------------------------------------------
# Generated simple commands (keeps the catalog broad without duplication)
# ---------------------------------------------------------------------------

TEXT_REPLIES: dict[str, str] = {
    "hug": "🤗 {author} hugs {target}",
    "pat": "🫶 {author} pats {target}",
    "poke": "👉 {author} pokes {target}",
    "highfive": "🙌 {author} high-fives {target}",
    "wave": "👋 {author} waves at {target}",
    "slap": "👋 {author} slaps {target}",
    "punch": "🥊 {author} punches {target}",
    "kiss": "😘 {author} kisses {target}",
    "cuddle": "🫂 {author} cuddles {target}",
    "bite": "🦷 {author} bites {target}",
    "boop": "👆 {author} boops {target}",
    "tickle": "🪶 {author} tickles {target}",
    "dance": "💃 {author} dances with {target}",
    "cheer": "📣 {author} cheers for {target}",
    "salute": "🫡 {author} salutes {target}",
    "stare": "👀 {author} stares at {target}",
    "blush": "😊 {author} blushes at {target}",
    "cry": "😢 {author} cries at {target}",
    "laugh": "😂 {author} laughs with {target}",
    "yeet": "🚀 {author} yeets {target}",
    "protect": "🛡️ {author} protects {target}",
    "feed": "🍽️ {author} feeds {target}",
    "handshake": "🤝 {author} shakes hands with {target}",
    "facepalm": "🤦 {author} facepalms at {target}",
    "applaud": "👏 {author} applauds {target}",
}


def make_interaction(name: str, template: str):
    @commands.command(name=name, help=f"Interaction: {name}")
    async def _cmd(self, ctx, member: discord.Member | None = None):  # noqa: ANN001
        target = (member or ctx.me).display_name
        await ctx.send(embed=embed(template.format(author=ctx.author.display_name, target=target)))
    return _cmd


Interactions = type(
    "Interactions",
    (commands.Cog,),
    {"__doc__": "Emote-style interactions.",
     **{name: make_interaction(name, tpl) for name, tpl in TEXT_REPLIES.items()}},
)

GENERATORS: dict[str, tuple[str, list[str]]] = {
    "advice": ("Advice", ["Drink water.", "Sleep earlier.", "Back up your database.",
                          "Say no more often.", "Ship it, then polish it."]),
    "motivate": ("Motivation", ["You've done harder things.", "Progress beats perfection.",
                                "One more push.", "Start small, start now."]),
    "quote": ("Quote", ["\"Simplicity is the soul of efficiency.\"",
                        "\"Make it work, make it right, make it fast.\"",
                        "\"Talk is cheap. Show me the code.\""]),
    "pickup": ("Pickup line", ["Are you a keyboard? You're just my type.",
                               "Is your name Wi-Fi? I'm feeling a connection."]),
    "insult": ("Playful insult", ["You have the charisma of a wet napkin.",
                                  "You're not lost, you're just exploring failure."]),
    "riddle": ("Riddle", ["I speak without a mouth. What am I? (an echo)",
                          "The more you take, the more you leave behind. (footsteps)"]),
    "topic": ("Conversation starter", ["What's the best thing you've built?",
                                       "What's an unpopular opinion you hold?"]),
    "wisdom": ("Wisdom", ["Slow is smooth, smooth is fast.", "Measure twice, deploy once."]),
    "excuse": ("Excuse", ["My cat unplugged the router.", "There was a DNS issue. There always is."]),
    "conspiracy": ("Conspiracy", ["Loading spinners are just for suspense.",
                                  "Bots dream of faster rate limits."]),
    "affirmation": ("Affirmation", ["You are capable.", "Your work matters."]),
    "prediction": ("Prediction", ["Something good happens within 48 hours.",
                                  "You'll fix that bug on the first try."]),
}


def make_generator(name: str, title: str, pool: list[str]):
    @commands.command(name=name, help=f"Random {title.lower()}")
    async def _cmd(self, ctx):  # noqa: ANN001
        await ctx.send(embed=embed(random.choice(pool), title=title))
    return _cmd


Generators = type(
    "Generators",
    (commands.Cog,),
    {"__doc__": "Random text generators.",
     **{name: make_generator(name, title, pool) for name, (title, pool) in GENERATORS.items()}},
)

# Quick single-line info commands
QUICK: dict[str, str] = {
    "support": f"Need help? {DASHBOARD_URL}",
    "website": f"{DASHBOARD_URL}",
    "docs": f"{DASHBOARD_URL}/#commands",
    "vote": f"{DASHBOARD_URL}",
    "privacy": "RM stores only server settings, cases and logs you generate.",
    "terms": "Use RM lawfully and follow Discord's Terms of Service.",
    "credits": "RM — built with discord.py and a TanStack dashboard.",
    "status": f"Live status: {DASHBOARD_URL}",
    "changelog": f"Release notes live at {DASHBOARD_URL}",
    "setup": f"Run the setup wizard at {DASHBOARD_URL}",
}


def make_quick(name: str, text: str):
    @commands.command(name=name, help=text[:90])
    async def _cmd(self, ctx):  # noqa: ANN001
        await ctx.send(embed=embed(text))
    return _cmd


Links = type(
    "Links",
    (commands.Cog,),
    {"__doc__": "Links and legal.",
     **{name: make_quick(name, text) for name, text in QUICK.items()}},
)


# ---------------------------------------------------------------------------
# Slash command mirrors for the most used commands
# ---------------------------------------------------------------------------


@bot.tree.command(name="help", description="Show the RM help menu")
async def slash_help(interaction: discord.Interaction):
    await interaction.response.send_message(
        embed=embed(f"`{len(set(bot.walk_commands()))}` commands. Prefixes: "
                    f"{', '.join(DEFAULT_PREFIXES)}\nDashboard: {DASHBOARD_URL}", title="RM"),
        ephemeral=True)


@bot.tree.command(name="ping", description="Show bot latency")
async def slash_ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"🏓 {round(bot.latency*1000)}ms", ephemeral=True)


@bot.tree.command(name="userinfo", description="Show information about a member")
async def slash_userinfo(interaction: discord.Interaction, member: discord.Member | None = None):
    m = member or interaction.user
    await interaction.response.send_message(
        embed=embed(f"**ID** {m.id}\n**Created** {discord.utils.format_dt(m.created_at, 'R')}",
                    title=str(m)), ephemeral=True)


@bot.tree.command(name="warn", description="Warn a member")
async def slash_warn(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    if not interaction.user.guild_permissions.moderate_members:
        return await interaction.response.send_message("You lack permission.", ephemeral=True)
    number = await bot.log_case(interaction.guild, "warn", member, interaction.user, reason)
    await interaction.response.send_message(
        embed=ok(f"Warned {member.mention}" + (f" — case #{number}" if number else "")))


@bot.tree.command(name="lockdown", description="Lock every channel")
async def slash_lockdown(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("Administrator only.", ephemeral=True)
    await interaction.response.defer()
    count = await set_lockdown(interaction.guild, True, f"{interaction.user}: slash lockdown")
    await interaction.followup.send(embed=ok(f"Locked {count} channels"))


# ---------------------------------------------------------------------------
# Dashboard account verification (rm!verify / /verify)
# ---------------------------------------------------------------------------


def _can_manage(member: discord.Member, guild: discord.Guild) -> bool:
    perms = member.guild_permissions
    return bool(member.id == guild.owner_id or perms.administrator or perms.manage_guild)


async def create_verify_link(member: discord.Member, guild: discord.Guild) -> str:
    """Ask the dashboard for a one-time signup link for this member."""
    if not SYNC_KEY:
        raise RuntimeError("BOT_SYNC_KEY is not configured")
    if bot.session is None or bot.session.closed:
        raise RuntimeError("HTTP session unavailable")
    async with bot.session.post(
        f"{DASHBOARD_URL}/api/public/verify/start",
        json={
            "discord_id": str(member.id),
            "discord_username": str(member),
            "guild_id": str(guild.id),
        },
        headers={"x-bot-key": SYNC_KEY, "content-type": "application/json"},
        timeout=aiohttp.ClientTimeout(total=15),
    ) as response:
        body = await response.json(content_type=None)
        status = response.status
    if status != 200 or not isinstance(body, dict) or not body.get("url"):
        raise RuntimeError(f"dashboard returned {status}: {body}")
    return str(body["url"])


class VerifyEmailModal(discord.ui.Modal, title="RM account verification"):
    email = discord.ui.TextInput(
        label="Email address",
        placeholder="you@example.com",
        required=True,
        min_length=5,
        max_length=254,
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            return await interaction.response.send_message(
                "Run this inside the server you want to manage.", ephemeral=True)
        if not _can_manage(member, guild):
            return await interaction.response.send_message(
                "You need Administrator or Manage Server to verify this server.", ephemeral=True)
        address = str(self.email.value).strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", address):
            return await interaction.response.send_message(
                "That doesn't look like a valid email address.", ephemeral=True)
        try:
            url = await create_verify_link(member, guild)
        except Exception as exc:  # noqa: BLE001
            print(f"[verify] failed: {exc}")
            return await interaction.response.send_message(
                "I couldn't create your verification link. Check DASHBOARD_URL and BOT_SYNC_KEY.",
                ephemeral=True)
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}email={urllib.parse.quote(address)}"
        view = discord.ui.View(timeout=300)
        view.add_item(discord.ui.Button(label="Finish RM signup", url=url,
                                        style=discord.ButtonStyle.link))
        await interaction.response.send_message(
            embed=embed(
                f"**{address}** is attached to your RM verification.\n\n"
                "Open the button to choose your username, password and profile picture. "
                "RM sends the confirmation email when you submit it.",
                "Email saved"),
            view=view, ephemeral=True)


class VerifyStartView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Start RM Verification", style=discord.ButtonStyle.danger)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            return await interaction.response.send_message(
                "Run this inside the server you want to manage.", ephemeral=True)
        if not _can_manage(member, guild):
            return await interaction.response.send_message(
                "You need Administrator or Manage Server to verify this server.", ephemeral=True)
        await interaction.response.send_modal(VerifyEmailModal())


class Verification(commands.Cog):
    """Dashboard account onboarding."""

    @commands.command(name="verify", aliases=["verifyaccount", "panel"])
    @commands.guild_only()
    async def verify(self, ctx: commands.Context):
        """Create your RM dashboard account."""
        if not _can_manage(ctx.author, ctx.guild):
            return await ctx.reply(embed=err(
                "You need Administrator or Manage Server to verify this server."),
                mention_author=False)
        await ctx.reply(
            embed=embed(
                "Your Discord permissions check out. Press the button and enter your email — "
                "RM will hand you a secure dashboard signup link.",
                "RM Verification"),
            view=VerifyStartView(), mention_author=False)


@bot.tree.command(name="verify", description="Create your secure RM Dashboard account")
async def slash_verify(interaction: discord.Interaction):
    guild = interaction.guild
    member = interaction.user
    if guild is None or not isinstance(member, discord.Member):
        return await interaction.response.send_message(
            "Run /verify inside the server you want to manage.", ephemeral=True)
    if not _can_manage(member, guild):
        return await interaction.response.send_message(
            "You need Administrator or Manage Server to verify this server.", ephemeral=True)
    await interaction.response.send_modal(VerifyEmailModal())


# ---------------------------------------------------------------------------
# Boot
# ---------------------------------------------------------------------------


async def main():
    if not TOKEN or TOKEN == "PUT_YOUR_BOT_TOKEN_HERE":
        raise SystemExit("Set DISCORD_BOT_TOKEN in bot/.env before starting RM.")
    if not SYNC_KEY or SYNC_KEY == "PUT_YOUR_SYNC_KEY_HERE":
        print("WARNING: BOT_SYNC_KEY is not set — the dashboard bridge is disabled.")
    async with bot:
        for cog in (Moderation(), Security(), Info(), Tools(), Fun(), Economy(),
                    Tickets(), Owner(), Interactions(), Generators(), Links(),
                    Verification()):
            await bot.add_cog(cog, override=True)
        await bot.start(TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("shutting down")
