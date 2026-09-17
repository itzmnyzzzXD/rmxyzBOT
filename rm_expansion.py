from __future__ import annotations

import asyncio
import json
import os
import random
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import aiohttp
import discord
from discord.ext import commands, tasks

from bot import bot, db, get_config, make_embed, progress_bar, PALETTE, PREFIX, admin_only, mod_only

AI_OWNER_ID = 1394708156818391110
HF_TOKEN = os.getenv("HF_TOKEN", "").strip()
HF_URL = os.getenv("HF_URL", "https://router.huggingface.co/v1/chat/completions")
HF_MODEL = os.getenv("HF_MODEL", "openai/gpt-oss-120b:fastest")
AI_HISTORY = defaultdict(list)
AFK = {}
REMINDERS = {}
STICKIES = {}


def utcnow():
    return datetime.now(timezone.utc)


def target_ok(actor: discord.Member, target: discord.Member):
    me = actor.guild.me
    return target.id not in {actor.id, actor.guild.owner_id, me.id} and target.top_role < actor.top_role and target.top_role < me.top_role


class RMView(discord.ui.View):
    def __init__(self, owner_id: int, timeout=180):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This RM panel belongs to another user.", ephemeral=True)
            return False
        return True


class ConfirmView(RMView):
    def __init__(self, owner_id, callback):
        super().__init__(owner_id, 60)
        self.callback_fn = callback

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger, emoji="✓")
    async def confirm(self, interaction, button):
        await self.callback_fn(interaction)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="×")
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(embed=make_embed("RM • Cancelled", "No changes were made.", PALETTE["warning"]), view=None)
        self.stop()


class ControlView(RMView):
    @discord.ui.button(label="Server", style=discord.ButtonStyle.primary, emoji="🛡️")
    async def server(self, interaction, button):
        g = interaction.guild
        e = make_embed("RM • Server Center", f"**{g.name}**\nMembers: `{g.member_count}`\nChannels: `{len(g.channels)}`\nRoles: `{len(g.roles)}`")
        await interaction.response.edit_message(embed=e, view=ServerView(interaction.user.id))

    @discord.ui.button(label="Moderation", style=discord.ButtonStyle.danger, emoji="🔨")
    async def moderation(self, interaction, button):
        await interaction.response.edit_message(embed=make_embed("RM • Moderation Center", "Choose a moderation workflow."), view=ModerationView(interaction.user.id))

    @discord.ui.button(label="Security", style=discord.ButtonStyle.danger, emoji="🔐")
    async def security(self, interaction, button):
        await interaction.response.edit_message(embed=make_embed("RM • Security Center", "Automod, verification, raid protection and lockdown."), view=SecurityView(interaction.user.id))

    @discord.ui.button(label="Community", style=discord.ButtonStyle.success, emoji="✨")
    async def community(self, interaction, button):
        await interaction.response.edit_message(embed=make_embed("RM • Community Center", "Polls, reminders, AFK, XP and utilities."), view=CommunityView(interaction.user.id))

    @discord.ui.button(label="AI", style=discord.ButtonStyle.secondary, emoji="✦")
    async def ai(self, interaction, button):
        await interaction.response.edit_message(embed=make_embed("RM Core", "Private owner-only engineering AI. Use `/core <prompt>` or `-core <prompt>`."), view=self)


class ServerView(RMView):
    @discord.ui.button(label="Info", style=discord.ButtonStyle.primary, emoji="📊")
    async def info(self, interaction, button):
        g = interaction.guild
        e = make_embed(f"📊 {g.name}", f"**Owner:** <@{g.owner_id}>\n**Members:** `{g.member_count}`\n**Channels:** `{len(g.channels)}`\n**Roles:** `{len(g.roles)}`\n**Boosts:** `{g.premium_subscription_count}`\n**Created:** {discord.utils.format_dt(g.created_at, 'F')}")
        if g.icon: e.set_thumbnail(url=g.icon.url)
        await interaction.response.edit_message(embed=e, view=self)

    @discord.ui.button(label="Lockdown", style=discord.ButtonStyle.danger, emoji="🚨")
    async def lockdown(self, interaction, button):
        await interaction.response.send_message("Use `/lockdown` for the confirmation workflow.", ephemeral=True)

    @discord.ui.button(label="Stats", style=discord.ButtonStyle.secondary, emoji="📈")
    async def stats(self, interaction, button):
        conn = db(); cases = conn.execute("SELECT COUNT(*) n FROM cases WHERE guild_id=?", (interaction.guild.id,)).fetchone()["n"]; warns = conn.execute("SELECT COUNT(*) n FROM warnings WHERE guild_id=?", (interaction.guild.id,)).fetchone()["n"]; conn.close()
        await interaction.response.edit_message(embed=make_embed("RM • Server Stats", f"Cases: `{cases}`\nWarnings: `{warns}`\nLatency: `{round(bot.latency*1000)}ms`"), view=self)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="↩")
    async def back(self, interaction, button):
        await interaction.response.edit_message(embed=make_embed("RM • Control Center", "Choose a system."), view=ControlView(interaction.user.id))


class ModerationView(RMView):
    @discord.ui.button(label="Cases", style=discord.ButtonStyle.secondary, emoji="📁")
    async def cases(self, interaction, button):
        await interaction.response.send_message("Use `/casebrowser` for the interactive case browser.", ephemeral=True)

    @discord.ui.button(label="Purge", style=discord.ButtonStyle.secondary, emoji="🧹")
    async def purge(self, interaction, button):
        await interaction.response.send_message("Use `/purgeui <amount>` for confirmation + animated completion.", ephemeral=True)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="↩")
    async def back(self, interaction, button):
        await interaction.response.edit_message(embed=make_embed("RM • Control Center", "Choose a system."), view=ControlView(interaction.user.id))


class SecurityView(RMView):
    @discord.ui.button(label="Status", style=discord.ButtonStyle.primary, emoji="🛡️")
    async def status(self, interaction, button):
        c = get_config(interaction.guild.id)
        text = f"Links `{c['anti_links']}`\nInvites `{c['anti_invites']}`\nSpam `{c['anti_spam']}`\nMentions `{c['anti_mentions']}`\nCaps `{c['anti_caps']}`\nVerification `{c['verification_enabled']}`"
        await interaction.response.edit_message(embed=make_embed("RM • Security Status", text), view=self)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="↩")
    async def back(self, interaction, button):
        await interaction.response.edit_message(embed=make_embed("RM • Control Center", "Choose a system."), view=ControlView(interaction.user.id))


class CommunityView(RMView):
    @discord.ui.button(label="Poll", style=discord.ButtonStyle.primary, emoji="📊")
    async def poll(self, interaction, button):
        await interaction.response.send_message("Use `/poll question options` to create an interactive poll.", ephemeral=True)

    @discord.ui.button(label="Reminder", style=discord.ButtonStyle.secondary, emoji="⏰")
    async def reminder(self, interaction, button):
        await interaction.response.send_message("Use `/remind 10m message`.", ephemeral=True)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="↩")
    async def back(self, interaction, button):
        await interaction.response.edit_message(embed=make_embed("RM • Control Center", "Choose a system."), view=ControlView(interaction.user.id))


@bot.hybrid_command(name="control", description="Open the animated RM Control Center.")
async def control(ctx):
    msg = await ctx.reply(embed=make_embed("RM • Loading Control Center", "▰▱▱▱▱\nBooting UI…"))
    for i, step in enumerate(["Checking modules", "Loading security", "Loading moderation", "Loading community", "Ready"], 1):
        await asyncio.sleep(.12)
        await msg.edit(embed=make_embed("RM • Control Center", f"{progress_bar(i, 5)}\n{step}"))
    await msg.edit(embed=make_embed("RM • Control Center", "Choose a system below."), view=ControlView(ctx.author.id))


@bot.hybrid_command(name="dashboard", description="Open the RM server dashboard.")
async def dashboard(ctx):
    c = get_config(ctx.guild.id)
    e = make_embed("RM • Live Dashboard", f"**{ctx.guild.name}**\nMembers `{ctx.guild.member_count}` • Channels `{len(ctx.guild.channels)}` • Roles `{len(ctx.guild.roles)}`\nLatency `{round(bot.latency*1000)}ms`")
    e.add_field(name="Security", value=f"Links `{c['anti_links']}` • Invites `{c['anti_invites']}` • Spam `{c['anti_spam']}` • Mentions `{c['anti_mentions']}`")
    e.add_field(name="Community", value=f"Welcome `{c['welcome_enabled']}` • XP `{c['leveling']}` • Tickets `{c['tickets_enabled']}`")
    await ctx.reply(embed=e, view=ControlView(ctx.author.id))


@bot.hybrid_command(name="userpanel", description="Open an interactive member panel.")
async def userpanel(ctx, member: discord.Member = None):
    member = member or ctx.author
    e = make_embed(f"👤 {member}", f"ID `{member.id}`\nCreated {discord.utils.format_dt(member.created_at, 'R')}\nJoined {discord.utils.format_dt(member.joined_at, 'R') if member.joined_at else 'Unknown'}\nTop role {member.top_role.mention}")
    e.set_thumbnail(url=member.display_avatar.url)
    await ctx.reply(embed=e, view=ControlView(ctx.author.id))


@bot.hybrid_command(name="roleinfo", description="Inspect a role interactively.")
async def roleinfo(ctx, role: discord.Role):
    await ctx.reply(embed=make_embed(f"🎭 {role.name}", f"ID `{role.id}`\nMembers `{len(role.members)}`\nPosition `{role.position}`\nMentionable `{role.mentionable}`\nManaged `{role.managed}`"), view=ControlView(ctx.author.id))


@bot.hybrid_command(name="channelinfo", description="Inspect a channel interactively.")
async def channelinfo(ctx, channel: discord.TextChannel = None):
    channel = channel or ctx.channel
    await ctx.reply(embed=make_embed(f"📺 #{channel.name}", f"ID `{channel.id}`\nType `{channel.__class__.__name__}`\nPosition `{channel.position}`\nCategory `{channel.category.name if channel.category else 'None'}`\nNSFW `{getattr(channel,'nsfw',False)}`"), view=ControlView(ctx.author.id))


@bot.hybrid_command(name="permissions", description="View a member's permissions interactively.")
async def permissions(ctx, member: discord.Member = None):
    member = member or ctx.author
    perms = [n.replace('_',' ').title() for n,v in member.guild_permissions if v]
    await ctx.reply(embed=make_embed(f"🔑 Permissions • {member.display_name}", ', '.join(perms)[:3900] or 'None'), view=ControlView(ctx.author.id))


@bot.hybrid_command(name="avatar", description="View a member avatar with UI.")
async def avatar(ctx, member: discord.Member = None):
    member = member or ctx.author
    e = make_embed(f"🖼️ Avatar • {member.display_name}", f"[Open PNG]({member.display_avatar.with_format('png').url})")
    e.set_image(url=member.display_avatar.url)
    await ctx.reply(embed=e, view=ControlView(ctx.author.id))


@bot.hybrid_command(name="banner", description="View a member banner with UI.")
async def banner(ctx, member: discord.Member = None):
    user = await bot.fetch_user((member or ctx.author).id)
    if not user.banner: return await ctx.reply("That user has no banner.")
    e = make_embed(f"🎨 Banner • {user.display_name}"); e.set_image(url=user.banner.url)
    await ctx.reply(embed=e, view=ControlView(ctx.author.id))


@bot.hybrid_command(name="nick", description="Edit a member nickname with confirmation.")
@mod_only()
async def nick(ctx, member: discord.Member, *, nickname: str = None):
    if not target_ok(ctx.author, member): return await ctx.reply("Member hierarchy prevents that action.")
    async def apply(interaction):
        try: await member.edit(nick=nickname, reason=f"RM nickname by {ctx.author}")
        except discord.HTTPException: return await interaction.response.edit_message(embed=make_embed("❌ Failed", "Discord rejected the nickname change.", PALETTE['danger']), view=None)
        await interaction.response.edit_message(embed=make_embed("✅ Nickname Updated", f"{member.mention} → `{nickname or member.name}`", PALETTE['success']), view=ControlView(ctx.author.id))
    await ctx.reply(embed=make_embed("✏️ Confirm Nickname", f"Change {member.mention}'s nickname to `{nickname or member.name}`?"), view=ConfirmView(ctx.author.id, apply))


@bot.hybrid_command(name="roleadd", description="Add a role with confirmation.")
@mod_only()
async def roleadd(ctx, member: discord.Member, role: discord.Role):
    if role >= ctx.guild.me.top_role or not target_ok(ctx.author, member): return await ctx.reply("Role/member hierarchy prevents this.")
    async def apply(interaction):
        try: await member.add_roles(role, reason=f"RM role add by {ctx.author}")
        except discord.HTTPException: return await interaction.response.edit_message(embed=make_embed("❌ Failed", "Discord rejected the role update.", PALETTE['danger']), view=None)
        await interaction.response.edit_message(embed=make_embed("✅ Role Added", f"{role.mention} → {member.mention}", PALETTE['success']), view=ControlView(ctx.author.id))
    await ctx.reply(embed=make_embed("🎭 Confirm Role Add", f"Give {role.mention} to {member.mention}?"), view=ConfirmView(ctx.author.id, apply))


@bot.hybrid_command(name="roleremove", description="Remove a role with confirmation.")
@mod_only()
async def roleremove(ctx, member: discord.Member, role: discord.Role):
    if role >= ctx.guild.me.top_role or not target_ok(ctx.author, member): return await ctx.reply("Role/member hierarchy prevents this.")
    async def apply(interaction):
        try: await member.remove_roles(role, reason=f"RM role remove by {ctx.author}")
        except discord.HTTPException: return await interaction.response.edit_message(embed=make_embed("❌ Failed", "Discord rejected the role update.", PALETTE['danger']), view=None)
        await interaction.response.edit_message(embed=make_embed("✅ Role Removed", f"{role.mention} ← {member.mention}", PALETTE['success']), view=ControlView(ctx.author.id))
    await ctx.reply(embed=make_embed("🎭 Confirm Role Remove", f"Remove {role.mention} from {member.mention}?"), view=ConfirmView(ctx.author.id, apply))


@bot.hybrid_command(name="lockdown", description="Lock server text channels with confirmation.")
@mod_only()
async def lockdown(ctx):
    async def apply(interaction):
        changed = 0
        for ch in ctx.guild.text_channels:
            try: await ch.set_permissions(ctx.guild.default_role, send_messages=False, reason=f"RM lockdown by {ctx.author}"); changed += 1
            except discord.HTTPException: pass
        await interaction.response.edit_message(embed=make_embed("🚨 RM • Lockdown Active", f"Locked `{changed}` text channels.", PALETTE['danger']), view=ControlView(ctx.author.id))
    await ctx.reply(embed=make_embed("🚨 Confirm Lockdown", "Deny @everyone permission to send in text channels?"), view=ConfirmView(ctx.author.id, apply))


@bot.hybrid_command(name="unlockdown", description="Remove server lockdown.")
@mod_only()
async def unlockdown(ctx):
    changed = 0
    for ch in ctx.guild.text_channels:
        try:
            ow = ch.overwrites_for(ctx.guild.default_role); ow.send_messages = None
            await ch.set_permissions(ctx.guild.default_role, overwrite=ow, reason=f"RM unlock by {ctx.author}"); changed += 1
        except discord.HTTPException: pass
    await ctx.reply(embed=make_embed("✅ Lockdown Removed", f"Updated `{changed}` channels.", PALETTE['success']), view=ControlView(ctx.author.id))


@bot.hybrid_command(name="slowmodeall", description="Set slowmode for all text channels.")
@admin_only()
async def slowmodeall(ctx, seconds: int):
    seconds = max(0, min(21600, seconds))
    async def apply(interaction):
        changed = 0
        for ch in ctx.guild.text_channels:
            try: await ch.edit(slowmode_delay=seconds, reason=f"RM slowmode by {ctx.author}"); changed += 1
            except discord.HTTPException: pass
        await interaction.response.edit_message(embed=make_embed("⚡ Slowmode Applied", f"`{seconds}s` applied to `{changed}` channels.", PALETTE['success']), view=ControlView(ctx.author.id))
    await ctx.reply(embed=make_embed("⚡ Confirm Slowmode", f"Apply `{seconds}s` to all text channels?"), view=ConfirmView(ctx.author.id, apply))


@bot.hybrid_command(name="purgeui", description="Interactive purge workflow.")
@mod_only()
async def purgeui(ctx, amount: int = 10):
    amount = max(1, min(100, amount))
    async def apply(interaction):
        deleted = await ctx.channel.purge(limit=amount)
        await interaction.response.edit_message(embed=make_embed("🧹 Purge Complete", f"Deleted `{len(deleted)}` messages.", PALETTE['success']), view=None)
    await ctx.reply(embed=make_embed("🧹 Confirm Purge", f"Delete up to `{amount}` messages here?"), view=ConfirmView(ctx.author.id, apply))


@bot.hybrid_command(name="casebrowser", description="Browse recent moderation cases.")
@mod_only()
async def casebrowser(ctx, member: discord.Member = None):
    conn = db()
    rows = conn.execute("SELECT * FROM cases WHERE guild_id=?" + (" AND user_id=?" if member else "") + " ORDER BY id DESC LIMIT 12", (ctx.guild.id, member.id) if member else (ctx.guild.id,)).fetchall(); conn.close()
    if not rows: return await ctx.reply(embed=make_embed("📁 Cases", "No cases found."), view=ControlView(ctx.author.id))
    lines = [f"`#{r['id']}` • **{r['action']}** • <@{r['user_id']}> • {r['reason'] or 'No reason'}" for r in rows]
    await ctx.reply(embed=make_embed("📁 RM • Case Browser", '\n'.join(lines)), view=ControlView(ctx.author.id))


@bot.hybrid_command(name="poll", description="Create a reaction poll.")
async def poll(ctx, question: str, *, options: str = "Yes | No"):
    opts = [x.strip() for x in options.split('|') if x.strip()][:5] or ['Yes','No']
    nums = ['1️⃣','2️⃣','3️⃣','4️⃣','5️⃣']
    msg = await ctx.reply(embed=make_embed('📊 RM • Poll', f"**{question}**\n\n" + '\n'.join(f'{nums[i]} {o}' for i,o in enumerate(opts))), view=ControlView(ctx.author.id))
    for x in nums[:len(opts)]:
        try: await msg.add_reaction(x)
        except discord.HTTPException: pass


@bot.hybrid_command(name="remind", description="Create a reminder.")
async def remind(ctx, delay: str, *, text: str):
    units = {'s':1,'m':60,'h':3600,'d':86400}
    try: seconds = int(delay[:-1]) * units[delay[-1].lower()]
    except (ValueError, KeyError): return await ctx.reply('Use `30s`, `10m`, `2h`, or `1d`.')
    if not 5 <= seconds <= 2592000: return await ctx.reply('Reminder must be 5 seconds to 30 days.')
    due = utcnow() + timedelta(seconds=seconds); key = f'{ctx.author.id}:{time.time_ns()}'; REMINDERS[key]=(ctx.author.id,ctx.channel.id,text[:1000],due.timestamp())
    await ctx.reply(embed=make_embed('⏰ Reminder Scheduled', f"{discord.utils.format_dt(due,'R')}\n{text[:1000]}", PALETTE['success']), view=ControlView(ctx.author.id))


@tasks.loop(seconds=5)
async def reminder_worker():
    for key,(uid,cid,text,due) in list(REMINDERS.items()):
        if time.time() < due: continue
        target = bot.get_channel(cid) or bot.get_user(uid)
        try:
            if target: await target.send(f'<@{uid}> ⏰ **Reminder:** {text}')
        except discord.HTTPException: pass
        REMINDERS.pop(key,None)


@bot.hybrid_command(name="afk", description="Set AFK status.")
async def afk(ctx, *, reason='AFK'):
    AFK[ctx.author.id]={'reason':reason[:250],'since':utcnow()}; await ctx.reply(embed=make_embed('💤 AFK Enabled', reason[:250], PALETTE['success']), view=ControlView(ctx.author.id))


@bot.hybrid_command(name="unafk", description="Clear AFK status.")
async def unafk(ctx):
    AFK.pop(ctx.author.id,None); await ctx.reply(embed=make_embed('👋 AFK Cleared','Welcome back.',PALETTE['success']), view=ControlView(ctx.author.id))


@bot.hybrid_command(name="sticky", description="Create a sticky channel message.")
@mod_only()
async def sticky(ctx, *, content: str):
    old=STICKIES.get(ctx.channel.id)
    if old:
        try: await old.delete()
        except discord.HTTPException: pass
    msg=await ctx.channel.send(embed=make_embed('📌 Sticky',content[:1800])); STICKIES[ctx.channel.id]=msg
    await ctx.reply(embed=make_embed('📌 Sticky Enabled','The channel sticky is now active.',PALETTE['success']),delete_after=5)


@bot.hybrid_command(name="unsticky", description="Remove the channel sticky.")
@mod_only()
async def unsticky(ctx):
    old=STICKIES.pop(ctx.channel.id,None)
    if old:
        try: await old.delete()
        except discord.HTTPException: pass
    await ctx.reply(embed=make_embed('📌 Sticky Removed','Sticky disabled.',PALETTE['success']), view=ControlView(ctx.author.id))


@bot.hybrid_command(name="serverstats", description="Open server statistics UI.")
async def serverstats(ctx):
    c=get_config(ctx.guild.id); conn=db(); cases=conn.execute('SELECT COUNT(*) n FROM cases WHERE guild_id=?',(ctx.guild.id,)).fetchone()['n']; warns=conn.execute('SELECT COUNT(*) n FROM warnings WHERE guild_id=?',(ctx.guild.id,)).fetchone()['n']; conn.close()
    await ctx.reply(embed=make_embed('📈 RM • Server Stats',f"Members `{ctx.guild.member_count}`\nCases `{cases}`\nWarnings `{warns}`\nLatency `{round(bot.latency*1000)}ms`\n\nLinks `{c['anti_links']}` • Spam `{c['anti_spam']}` • XP `{c['leveling']}`"),view=ControlView(ctx.author.id))


async def hf_chat(messages):
    if not HF_TOKEN: return 'HF_TOKEN is not configured on the VPS.'
    headers={'Authorization':f'Bearer {HF_TOKEN}','Content-Type':'application/json'}
    payload={'model':HF_MODEL,'messages':messages,'temperature':0.3,'max_tokens':2400}
    async with aiohttp.ClientSession() as s:
        async with s.post(HF_URL,headers=headers,json=payload,timeout=aiohttp.ClientTimeout(total=45)) as r:
            text=await r.text()
            if r.status>=400: raise RuntimeError(f'Hugging Face {r.status}: {text[:500]}')
            data=json.loads(text); return data['choices'][0]['message']['content'].strip()


AI_SYSTEM='''You are RM Core, the private engineering and server-operations AI for the RM Discord bot. Only the owner may use you. You are an expert in discord.py 2.x, Discord permissions, slash/hybrid commands, views/modals/select menus, SQLite, aiohttp, GitHub/Vercel integrations, dashboards, moderation, verification, tickets, raid protection and debugging. Be practical and concise. Never expose secrets or pretend an action happened unless the bot performed it. When asked to change code, first produce: summary, files, exact change, risk, validation. Prefer reversible changes and preserve existing code. When debugging, identify likely root cause, give a safe fix, and a test plan. Do not reveal hidden chain-of-thought.'''


@bot.hybrid_command(name='core',description='Private owner-only RM Core AI.')
async def core(ctx, *, prompt: str):
    if ctx.author.id != AI_OWNER_ID: return
    h=AI_HISTORY[ctx.author.id]; h.append({'role':'user','content':prompt[:6000]}); h[:] = h[-16:]
    try: result=await hf_chat([{'role':'system','content':AI_SYSTEM},*h])
    except Exception as e: result=f'AI request failed: `{e}`'
    h.append({'role':'assistant','content':result[:6000]})
    await ctx.reply(embed=make_embed('RM Core • Private AI',result[:3900]),view=ControlView(ctx.author.id),mention_author=False)


@bot.hybrid_command(name='coreclear',description='Clear private RM Core memory.')
async def coreclear(ctx):
    if ctx.author.id != AI_OWNER_ID:return
    AI_HISTORY[ctx.author.id].clear(); await ctx.reply(embed=make_embed('RM Core • Memory Cleared','Conversation memory reset.',PALETTE['success']))


@bot.hybrid_command(name='corestatus',description='Show RM Core AI status.')
async def corestatus(ctx):
    if ctx.author.id != AI_OWNER_ID:return
    await ctx.reply(embed=make_embed('RM Core • Status',f'Provider `Hugging Face`\nModel `{HF_MODEL}`\nToken `{"configured" if HF_TOKEN else "missing"}`'),view=ControlView(ctx.author.id))


@bot.listen('on_message')
async def expansion_message(message):
    if message.author.bot or not message.guild:return
    if message.author.id in AFK:
        AFK.pop(message.author.id,None)
        try: await message.channel.send(f'Welcome back, {message.author.mention}. AFK cleared.',delete_after=5)
        except discord.HTTPException: pass
    for uid in message.raw_mentions:
        info=AFK.get(uid)
        if info:
            try: await message.channel.send(f'💤 <@{uid}> is AFK: **{info["reason"]}**',delete_after=7)
            except discord.HTTPException: pass
    old=STICKIES.get(message.channel.id)
    if old and message.content and not message.content.startswith(PREFIX):
        try: await old.delete()
        except discord.HTTPException: pass
        try: STICKIES[message.channel.id]=await message.channel.send(embed=make_embed('📌 Sticky',old.embeds[0].description if old.embeds else ''))
        except discord.HTTPException: pass


@bot.listen('on_ready')
async def expansion_ready():
    if not reminder_worker.is_running(): reminder_worker.start()
