from __future__ import annotations

import traceback

import discord
from discord.ext import commands


HARDENED_UI_MARKER = "# === RM HARDENED INTERACTIVE UI ==="


async def _safe_interaction_error(interaction: discord.Interaction, error: BaseException):
    print(f"[RM UI ERROR] {type(error).__name__}: {error}")
    traceback.print_exc()
    message = "❌ The panel hit an error, but the bot is still running. Try the action again."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        pass


class SafeInteractiveView(discord.ui.View):
    def __init__(self, owner_id: int, timeout: float = 300):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            try:
                await interaction.response.send_message(
                    "This interactive panel belongs to the person who opened it.",
                    ephemeral=True,
                )
            except discord.HTTPException:
                pass
            return False
        return True

    async def on_error(self, interaction, error, item):
        await _safe_interaction_error(interaction, error)

    async def close_panel(self, interaction: discord.Interaction):
        try:
            await interaction.response.edit_message(
                embed=make_embed("RM • Panel Closed", "This interactive panel has been closed.", PALETTE["warning"]),
                view=None,
            )
        except discord.HTTPException:
            if not interaction.response.is_done():
                await interaction.response.send_message("Panel closed.", ephemeral=True)
        self.stop()

    async def home(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            embed=make_embed(
                "RM • Control Center",
                "Choose a system below.\n\n🟢 Live controls • 🔐 Security • 🔨 Moderation • ✨ Community • 📊 Analytics",
            ),
            view=ControlView(interaction.user.id),
        )


class ControlView(SafeInteractiveView):
    def __init__(self, owner_id: int):
        super().__init__(owner_id)
        self.selector = discord.ui.Select(
            placeholder="Choose an RM system…",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="Server", value="server", emoji="🛡️", description="Server information and controls"),
                discord.SelectOption(label="Security", value="security", emoji="🔐", description="Protection and verification"),
                discord.SelectOption(label="Moderation", value="moderation", emoji="🔨", description="Moderation utilities"),
                discord.SelectOption(label="Community", value="community", emoji="✨", description="Polls, AFK, reminders and more"),
                discord.SelectOption(label="Analytics", value="analytics", emoji="📊", description="Cases, warnings, activity and bot stats"),
            ],
            row=0,
        )
        self.selector.callback = self.select_system
        self.add_item(self.selector)

    async def select_system(self, interaction: discord.Interaction):
        choice = self.selector.values[0]
        if choice == "server":
            view = ServerInteractiveView(interaction.user.id)
            embed = await server_overview_embed(interaction.guild)
        elif choice == "security":
            view = SecurityInteractiveView(interaction.user.id)
            embed = security_overview_embed(interaction.guild)
        elif choice == "moderation":
            view = ModerationInteractiveView(interaction.user.id)
            embed = moderation_overview_embed(interaction.guild)
        elif choice == "community":
            view = CommunityInteractiveView(interaction.user.id)
            embed = community_overview_embed(interaction.guild)
        else:
            view = AnalyticsInteractiveView(interaction.user.id)
            embed = analytics_overview_embed(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Live Status", style=discord.ButtonStyle.success, emoji="🟢", row=1)
    async def status(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        embed = make_embed(
            "RM • Live Status",
            f"**Latency:** `{round(bot.latency * 1000)}ms`\n"
            f"**Servers:** `{len(bot.guilds)}`\n"
            f"**Members:** `{sum(g.member_count or 0 for g in bot.guilds):,}`\n"
            f"**Current server:** `{guild.name if guild else 'DM'}`",
            PALETTE["success"],
        )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Configuration", style=discord.ButtonStyle.primary, emoji="⚙️", row=1)
    async def configuration(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)
        await interaction.response.edit_message(
            embed=config_overview_embed(interaction.guild),
            view=InteractiveConfigView(interaction.user.id, interaction.guild.id),
        )

    @discord.ui.button(label="Quick Actions", style=discord.ButtonStyle.secondary, emoji="⚡", row=1)
    async def quick_actions(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            embed=make_embed("RM • Quick Actions", "Fast access to the most-used RM utilities."),
            view=QuickActionsView(interaction.user.id),
        )

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, emoji="↻", row=2)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            embed=make_embed("RM • Control Center", "Panel refreshed. Choose a system below."),
            view=ControlView(interaction.user.id),
        )

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, emoji="×", row=2)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.close_panel(interaction)


class ServerInteractiveView(SafeInteractiveView):
    @discord.ui.button(label="Overview", style=discord.ButtonStyle.primary, emoji="📊")
    async def overview(self, interaction, button):
        await interaction.response.edit_message(embed=await server_overview_embed(interaction.guild), view=self)

    @discord.ui.button(label="Channels", style=discord.ButtonStyle.secondary, emoji="📺")
    async def channels(self, interaction, button):
        g = interaction.guild
        counts = f"Text `{len(g.text_channels)}` • Voice `{len(g.voice_channels)}` • Categories `{len(g.categories)}`"
        await interaction.response.edit_message(embed=make_embed("RM • Channel Overview", counts), view=self)

    @discord.ui.button(label="Roles", style=discord.ButtonStyle.secondary, emoji="🎭")
    async def roles(self, interaction, button):
        g = interaction.guild
        top = g.roles[-8:][::-1]
        lines = [f"{r.mention} • `{len(r.members)}` members" for r in top if not r.is_default()]
        await interaction.response.edit_message(embed=make_embed("RM • Role Overview", "\n".join(lines) or "No roles found."), view=self)

    @discord.ui.button(label="Permissions", style=discord.ButtonStyle.primary, emoji="🔑")
    async def permissions(self, interaction, button):
        me = interaction.guild.me
        perms = [name.replace("_", " ").title() for name, enabled in me.guild_permissions if enabled]
        await interaction.response.edit_message(embed=make_embed("RM • Bot Permissions", ", ".join(perms)[:3900] or "None"), view=self)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="↩", row=1)
    async def back(self, interaction, button):
        await self.home(interaction)


class SecurityInteractiveView(SafeInteractiveView):
    @discord.ui.button(label="Status", style=discord.ButtonStyle.primary, emoji="🛡️")
    async def status(self, interaction, button):
        await interaction.response.edit_message(embed=security_overview_embed(interaction.guild), view=self)

    @discord.ui.button(label="Toggle Module", style=discord.ButtonStyle.success, emoji="⚙️")
    async def toggle(self, interaction, button):
        await interaction.response.edit_message(
            embed=make_embed("RM • Security Modules", "Choose a module to toggle instantly."),
            view=SecurityToggleView(interaction.user.id, interaction.guild.id),
        )

    @discord.ui.button(label="Verification", style=discord.ButtonStyle.primary, emoji="✅")
    async def verification(self, interaction, button):
        cfg = get_config(interaction.guild.id)
        role = interaction.guild.get_role(cfg["verify_role"]) if cfg["verify_role"] else None
        await interaction.response.edit_message(
            embed=make_embed("RM • Verification", f"Enabled: `{bool(cfg['verification_enabled'])}`\nRole: {role.mention if role else '`Not configured`'}"),
            view=self,
        )

    @discord.ui.button(label="Raid Protection", style=discord.ButtonStyle.danger, emoji="🚨")
    async def raid(self, interaction, button):
        conn = db()
        row = conn.execute("SELECT enabled FROM rm_raid_mode WHERE guild_id=?", (interaction.guild.id,)).fetchone()
        conn.close()
        enabled = int(row["enabled"]) if row else 0
        await interaction.response.edit_message(
            embed=make_embed("RM • Raid Protection", f"Current mode: **{'ACTIVE' if enabled else 'OFF'}**\nUse the existing raid-mode command for full activation/deactivation."),
            view=self,
        )

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="↩", row=1)
    async def back(self, interaction, button):
        await self.home(interaction)


class SecurityToggleView(SafeInteractiveView):
    def __init__(self, owner_id, guild_id):
        super().__init__(owner_id)
        self.guild_id = guild_id
        self.select = discord.ui.Select(
            placeholder="Toggle a security module…",
            options=[
                discord.SelectOption(label="Links", value="anti_links", emoji="🔗"),
                discord.SelectOption(label="Invites", value="anti_invites", emoji="📨"),
                discord.SelectOption(label="Spam", value="anti_spam", emoji="⚡"),
                discord.SelectOption(label="Mentions", value="anti_mentions", emoji="@"),
                discord.SelectOption(label="Caps", value="anti_caps", emoji="🔠"),
                discord.SelectOption(label="Slurs", value="anti_slurs", emoji="🛡️"),
            ],
        )
        self.select.callback = self.toggle_selected
        self.add_item(self.select)

    async def toggle_selected(self, interaction):
        key = self.select.values[0]
        cfg = get_config(self.guild_id)
        new_value = 0 if int(cfg[key]) else 1
        set_config(self.guild_id, key, new_value)
        await interaction.response.edit_message(embed=security_overview_embed(interaction.guild), view=SecurityInteractiveView(interaction.user.id))

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="↩", row=1)
    async def back(self, interaction, button):
        await interaction.response.edit_message(embed=security_overview_embed(interaction.guild), view=SecurityInteractiveView(interaction.user.id))


class ModerationInteractiveView(SafeInteractiveView):
    @discord.ui.button(label="Cases", style=discord.ButtonStyle.primary, emoji="📁")
    async def cases(self, interaction, button):
        conn = db()
        rows = conn.execute("SELECT id, action, user_id, reason FROM cases WHERE guild_id=? ORDER BY id DESC LIMIT 10", (interaction.guild.id,)).fetchall()
        conn.close()
        body = "\n".join(f"`#{r['id']}` • **{r['action']}** • <@{r['user_id']}> • {r['reason'] or 'No reason'}" for r in rows) or "No cases found."
        await interaction.response.edit_message(embed=make_embed("RM • Recent Cases", body[:3900]), view=self)

    @discord.ui.button(label="Warnings", style=discord.ButtonStyle.secondary, emoji="⚠️")
    async def warnings(self, interaction, button):
        conn = db()
        total = conn.execute("SELECT COUNT(*) n FROM warnings WHERE guild_id=?", (interaction.guild.id,)).fetchone()["n"]
        recent = conn.execute("SELECT user_id, reason FROM warnings WHERE guild_id=? ORDER BY id DESC LIMIT 8", (interaction.guild.id,)).fetchall()
        conn.close()
        body = "\n".join(f"<@{r['user_id']}> • {r['reason']}" for r in recent) or "No recent warnings."
        await interaction.response.edit_message(embed=make_embed("RM • Warning Center", f"Total warnings: `{total}`\n\n{body}"), view=self)

    @discord.ui.button(label="Purge", style=discord.ButtonStyle.danger, emoji="🧹")
    async def purge(self, interaction, button):
        await interaction.response.edit_message(embed=make_embed("RM • Purge", "Use the existing `purgeui` workflow to confirm the amount before deleting messages."), view=self)

    @discord.ui.button(label="Slowmode", style=discord.ButtonStyle.secondary, emoji="🐢")
    async def slowmode(self, interaction, button):
        await interaction.response.edit_message(embed=make_embed("RM • Slowmode", "Use `slowmodeall <seconds>` for the animated confirmation workflow."), view=self)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="↩", row=1)
    async def back(self, interaction, button):
        await self.home(interaction)


class CommunityInteractiveView(SafeInteractiveView):
    @discord.ui.button(label="Poll", style=discord.ButtonStyle.success, emoji="📊")
    async def poll(self, interaction, button):
        await interaction.response.edit_message(embed=make_embed("RM • Polls", "Create a poll with `poll <question> <options>`; options are separated with `|`."), view=self)

    @discord.ui.button(label="Reminders", style=discord.ButtonStyle.primary, emoji="⏰")
    async def reminders(self, interaction, button):
        await interaction.response.edit_message(embed=make_embed("RM • Reminders", "Use `remind 10m message`, `remind 2h message`, or `remind 1d message`."), view=self)

    @discord.ui.button(label="AFK", style=discord.ButtonStyle.secondary, emoji="💤")
    async def afk(self, interaction, button):
        await interaction.response.edit_message(embed=make_embed("RM • AFK", "Use `afk <reason>` to enable and `unafk` to clear it."), view=self)

    @discord.ui.button(label="Sticky", style=discord.ButtonStyle.secondary, emoji="📌")
    async def sticky(self, interaction, button):
        await interaction.response.edit_message(embed=make_embed("RM • Sticky", "Use `sticky <content>` and `unsticky` for channel sticky messages."), view=self)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="↩", row=1)
    async def back(self, interaction, button):
        await self.home(interaction)


class AnalyticsInteractiveView(SafeInteractiveView):
    @discord.ui.button(label="Server Stats", style=discord.ButtonStyle.primary, emoji="📈")
    async def server_stats(self, interaction, button):
        await interaction.response.edit_message(embed=analytics_overview_embed(interaction.guild), view=self)

    @discord.ui.button(label="Activity", style=discord.ButtonStyle.secondary, emoji="👥")
    async def activity(self, interaction, button):
        conn = db()
        rows = conn.execute("SELECT user_id, messages, commands, mod_actions FROM rm_staff_activity WHERE guild_id=? ORDER BY (messages + commands + mod_actions) DESC LIMIT 10", (interaction.guild.id,)).fetchall()
        conn.close()
        body = "\n".join(f"<@{r['user_id']}> • M `{r['messages']}` C `{r['commands']}` Mod `{r['mod_actions']}`" for r in rows) or "No tracked staff activity yet."
        await interaction.response.edit_message(embed=make_embed("RM • Staff Activity", body), view=self)

    @discord.ui.button(label="Members", style=discord.ButtonStyle.secondary, emoji="👤")
    async def members(self, interaction, button):
        g = interaction.guild
        bots = sum(1 for m in g.members if m.bot)
        humans = max(0, len(g.members) - bots)
        await interaction.response.edit_message(embed=make_embed("RM • Member Analytics", f"Humans: `{humans}`\nBots: `{bots}`\nTotal cached: `{len(g.members)}`"), view=self)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="↩", row=1)
    async def back(self, interaction, button):
        await self.home(interaction)


class InteractiveConfigView(SafeInteractiveView):
    def __init__(self, owner_id, guild_id):
        super().__init__(owner_id)
        self.guild_id = guild_id
        self.select = discord.ui.Select(
            placeholder="Toggle a module…",
            options=[
                discord.SelectOption(label="Anti Links", value="anti_links", emoji="🔗"),
                discord.SelectOption(label="Anti Invites", value="anti_invites", emoji="📨"),
                discord.SelectOption(label="Anti Spam", value="anti_spam", emoji="⚡"),
                discord.SelectOption(label="Anti Mentions", value="anti_mentions", emoji="@"),
                discord.SelectOption(label="Anti Caps", value="anti_caps", emoji="🔠"),
                discord.SelectOption(label="Leveling", value="leveling", emoji="📈"),
                discord.SelectOption(label="Welcome", value="welcome_enabled", emoji="👋"),
                discord.SelectOption(label="Autorole", value="autorole_enabled", emoji="🎭"),
                discord.SelectOption(label="Verification", value="verification_enabled", emoji="✅"),
                discord.SelectOption(label="Tickets", value="tickets_enabled", emoji="🎫"),
            ],
        )
        self.select.callback = self.toggle_module
        self.add_item(self.select)

    async def toggle_module(self, interaction):
        key = self.select.values[0]
        cfg = get_config(self.guild_id)
        new_value = 0 if int(cfg[key]) else 1
        set_config(self.guild_id, key, new_value)
        await interaction.response.edit_message(embed=config_overview_embed(interaction.guild), view=InteractiveConfigView(interaction.user.id, self.guild_id))

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, emoji="↻", row=1)
    async def refresh(self, interaction, button):
        await interaction.response.edit_message(embed=config_overview_embed(interaction.guild), view=self)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.primary, emoji="↩", row=1)
    async def back(self, interaction, button):
        await self.home(interaction)


class QuickActionsView(SafeInteractiveView):
    @discord.ui.button(label="Server Stats", style=discord.ButtonStyle.primary, emoji="📊")
    async def stats(self, interaction, button):
        await interaction.response.edit_message(embed=analytics_overview_embed(interaction.guild), view=self)

    @discord.ui.button(label="Config", style=discord.ButtonStyle.secondary, emoji="⚙️")
    async def config(self, interaction, button):
        await interaction.response.edit_message(embed=config_overview_embed(interaction.guild), view=InteractiveConfigView(interaction.user.id, interaction.guild.id))

    @discord.ui.button(label="Security", style=discord.ButtonStyle.danger, emoji="🔐")
    async def security(self, interaction, button):
        await interaction.response.edit_message(embed=security_overview_embed(interaction.guild), view=SecurityInteractiveView(interaction.user.id))

    @discord.ui.button(label="Moderation", style=discord.ButtonStyle.danger, emoji="🔨")
    async def moderation(self, interaction, button):
        await interaction.response.edit_message(embed=moderation_overview_embed(interaction.guild), view=ModerationInteractiveView(interaction.user.id))

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="↩", row=1)
    async def back(self, interaction, button):
        await self.home(interaction)


async def server_overview_embed(guild):
    me = guild.me
    e = make_embed(
        f"RM • {guild.name}",
        f"**Owner:** <@{guild.owner_id}>\n**Members:** `{guild.member_count or 0}`\n**Channels:** `{len(guild.channels)}`\n**Roles:** `{len(guild.roles)}`\n**Boosts:** `{guild.premium_subscription_count}`\n**Bot latency:** `{round(bot.latency * 1000)}ms`",
    )
    if guild.icon:
        e.set_thumbnail(url=guild.icon.url)
    if me:
        e.add_field(name="Bot Role", value=me.top_role.mention)
    return e


def security_overview_embed(guild):
    c = get_config(guild.id)
    text = "\n".join(
        f"{'🟢' if int(c[k]) else '⚫'} **{label}**"
        for k, label in [
            ("anti_links", "Link Filter"),
            ("anti_invites", "Invite Filter"),
            ("anti_slurs", "Word Filter"),
            ("anti_spam", "Spam Shield"),
            ("anti_mentions", "Mention Shield"),
            ("anti_caps", "Caps Filter"),
            ("verification_enabled", "Verification"),
        ]
    )
    return make_embed("RM • Security Center", text)


def moderation_overview_embed(guild):
    conn = db()
    cases = conn.execute("SELECT COUNT(*) n FROM cases WHERE guild_id=?", (guild.id,)).fetchone()["n"]
    warns = conn.execute("SELECT COUNT(*) n FROM warnings WHERE guild_id=?", (guild.id,)).fetchone()["n"]
    conn.close()
    return make_embed("RM • Moderation Center", f"**Cases:** `{cases}`\n**Warnings:** `{warns}`\n\nUse the buttons below for recent cases, warnings and moderation utilities.")


def community_overview_embed(guild):
    c = get_config(guild.id)
    return make_embed("RM • Community Center", f"Welcome `{c['welcome_enabled']}`\nLeveling `{c['leveling']}`\nAutorole `{c['autorole_enabled']}`\nTickets `{c['tickets_enabled']}`\n\nUse the buttons below for community tools.")


def analytics_overview_embed(guild):
    conn = db()
    cases = conn.execute("SELECT COUNT(*) n FROM cases WHERE guild_id=?", (guild.id,)).fetchone()["n"]
    warns = conn.execute("SELECT COUNT(*) n FROM warnings WHERE guild_id=?", (guild.id,)).fetchone()["n"]
    tickets = conn.execute("SELECT COUNT(*) n FROM tickets WHERE guild_id=?", (guild.id,)).fetchone()["n"]
    conn.close()
    return make_embed("RM • Analytics", f"**Members:** `{guild.member_count or 0}`\n**Cases:** `{cases}`\n**Warnings:** `{warns}`\n**Tickets:** `{tickets}`\n**Latency:** `{round(bot.latency * 1000)}ms`")


def config_overview_embed(guild):
    c = get_config(guild.id)
    enabled = sum(int(c[k]) for k in (
        "anti_links", "anti_invites", "anti_slurs", "anti_spam", "anti_mentions", "anti_caps",
        "leveling", "welcome_enabled", "autorole_enabled", "verification_enabled", "tickets_enabled",
    ))
    return make_embed("RM • Live Configuration", f"**Enabled modules:** `{enabled}/11`\n\nSelect a module below to toggle it. Changes save immediately.")


@bot.hybrid_command(name="rmuipanel", description="Open the expanded RM interactive UI.")
async def rmuipanel(ctx):
    if not ctx.guild:
        return await ctx.reply("Use this inside a server.", ephemeral=True)
    await ctx.reply(
        embed=make_embed("RM • Control Center", "Choose a system below.\n\nThe panel is locked to you and safely handles callback errors."),
        view=ControlView(ctx.author.id),
    )


@bot.hybrid_command(name="securityui", description="Open the RM interactive security center.")
@admin_only()
async def securityui(ctx):
    await ctx.reply(embed=security_overview_embed(ctx.guild), view=SecurityInteractiveView(ctx.author.id))


@bot.hybrid_command(name="moderationui", description="Open the RM interactive moderation center.")
@mod_only()
async def moderationui(ctx):
    await ctx.reply(embed=moderation_overview_embed(ctx.guild), view=ModerationInteractiveView(ctx.author.id))


@bot.hybrid_command(name="analyticsui", description="Open the RM interactive analytics center.")
async def analyticsui(ctx):
    await ctx.reply(embed=analytics_overview_embed(ctx.guild), view=AnalyticsInteractiveView(ctx.author.id))


@bot.hybrid_command(name="configui", description="Open the RM interactive module configuration panel.")
@admin_only()
async def configui(ctx):
    await ctx.reply(embed=config_overview_embed(ctx.guild), view=InteractiveConfigView(ctx.author.id, ctx.guild.id))


# Override legacy expansion view globals so all existing interactive commands use the hardened layer.
ServerView = ServerInteractiveView
SecurityView = SecurityInteractiveView
ModerationView = ModerationInteractiveView
CommunityView = CommunityInteractiveView

