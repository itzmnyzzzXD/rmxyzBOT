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

from bot import (
    bot,
    db,
    get_config,
    set_config,
    make_embed,
    animated_reply,
    progress_bar,
    PALETTE,
    PREFIX,
    admin_only,
    mod_only,
    VerifyView,
)

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
