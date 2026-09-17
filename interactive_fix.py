"""Runtime hardening for RM's interactive command UI.

This module is intentionally loaded by main.py after bot.py so the existing
command implementations stay intact while the shared interactive view layer
gets a safer Discord.py 2.6 implementation.
"""

from __future__ import annotations

import traceback
from typing import Optional

import discord
from discord.ext import commands

import bot as runtime


class SafeRMView(discord.ui.View):
    """Shared owner-locked base for RM interactive panels."""

    def __init__(self, owner_id: int, timeout: Optional[float] = 180):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(
                        "This RM panel belongs to another user.", ephemeral=True
                    )
                else:
                    await interaction.response.send_message(
                        "This RM panel belongs to another user.", ephemeral=True
                    )
            except discord.HTTPException:
                pass
            return False
        return True

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item,
    ) -> None:
        print(
            f"[RM INTERACTIVE ERROR] item={getattr(item, 'custom_id', None)!r} "
            f"type={type(error).__name__}: {error!r}"
        )
        traceback.print_exception(type(error), error, error.__traceback__)
        message = "❌ That interactive action failed. Check the bot log for the exact error."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass


class SafeControlView(SafeRMView):
    """Discord.py 2.6-safe replacement for the shared ControlView."""

    def __init__(self, owner_id: int):
        super().__init__(owner_id, 180)

        self.server_button = discord.ui.Button(
            label="Server", style=discord.ButtonStyle.primary, emoji="🛡️", row=0
        )
        self.mod_button = discord.ui.Button(
            label="Moderation", style=discord.ButtonStyle.danger, emoji="🔨", row=0
        )
        self.security_button = discord.ui.Button(
            label="Security", style=discord.ButtonStyle.danger, emoji="🔐", row=0
        )
        self.community_button = discord.ui.Button(
            label="Community", style=discord.ButtonStyle.success, emoji="✨", row=0
        )
        self.ai_button = discord.ui.Button(
            label="AI", style=discord.ButtonStyle.secondary, emoji="✦", row=0
        )

        self.server_button.callback = self._server
        self.mod_button.callback = self._moderation
        self.security_button.callback = self._security
        self.community_button.callback = self._community
        self.ai_button.callback = self._ai

        for button in (
            self.server_button,
            self.mod_button,
            self.security_button,
            self.community_button,
            self.ai_button,
        ):
            self.add_item(button)

    async def _server(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("Server only.", ephemeral=True)
        embed = runtime.make_embed(
            "RM • Server Center",
            f"**{guild.name}**\n"
            f"Members: `{guild.member_count}`\n"
            f"Channels: `{len(guild.channels)}`\n"
            f"Roles: `{len(guild.roles)}`",
        )
        await interaction.response.edit_message(
            embed=embed, view=SafeServerView(interaction.user.id)
        )

    async def _moderation(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            embed=runtime.make_embed(
                "RM • Moderation Center", "Choose a moderation workflow."
            ),
            view=SafeModerationView(interaction.user.id),
        )

    async def _security(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            embed=runtime.make_embed(
                "RM • Security Center",
                "Automod, verification, raid protection and lockdown.",
            ),
            view=SafeSecurityView(interaction.user.id),
        )

    async def _community(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            embed=runtime.make_embed(
                "RM • Community Center", "Polls, reminders, AFK, XP and utilities."
            ),
            view=SafeCommunityView(interaction.user.id),
        )

    async def _ai(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            embed=runtime.make_embed(
                "RM Core",
                "Private owner-only engineering AI. Use `/core <prompt>` or `-core <prompt>`."
                ,
            ),
            view=self,
        )


class SafeServerView(SafeRMView):
    def __init__(self, owner_id: int):
        super().__init__(owner_id, 180)
        info = discord.ui.Button(label="Info", style=discord.ButtonStyle.primary, emoji="📊")
        lockdown = discord.ui.Button(label="Lockdown", style=discord.ButtonStyle.danger, emoji="🚨")
        stats = discord.ui.Button(label="Stats", style=discord.ButtonStyle.secondary, emoji="📈")
        back = discord.ui.Button(label="Back", style=discord.ButtonStyle.secondary, emoji="↩")
        info.callback = self._info
        lockdown.callback = self._lockdown
        stats.callback = self._stats
        back.callback = self._back
        for button in (info, lockdown, stats, back):
            self.add_item(button)

    async def _info(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("Server only.", ephemeral=True)
        embed = runtime.make_embed(
            f"📊 {guild.name}",
            f"**Owner:** <@{guild.owner_id}>\n"
            f"**Members:** `{guild.member_count}`\n"
            f"**Channels:** `{len(guild.channels)}`\n"
            f"**Roles:** `{len(guild.roles)}`\n"
            f"**Boosts:** `{guild.premium_subscription_count}`\n"
            f"**Created:** {discord.utils.format_dt(guild.created_at, 'F')}",
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _lockdown(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "Use `/lockdown` for the confirmation workflow.", ephemeral=True
        )

    async def _stats(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("Server only.", ephemeral=True)
        conn = runtime.db()
        cases = conn.execute(
            "SELECT COUNT(*) n FROM cases WHERE guild_id=?", (guild.id,)
        ).fetchone()["n"]
        warns = conn.execute(
            "SELECT COUNT(*) n FROM warnings WHERE guild_id=?", (guild.id,)
        ).fetchone()["n"]
        conn.close()
        await interaction.response.edit_message(
            embed=runtime.make_embed(
                "RM • Server Stats",
                f"Cases: `{cases}`\nWarnings: `{warns}`\nLatency: `{round(runtime.bot.latency * 1000)}ms`",
            ),
            view=self,
        )

    async def _back(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            embed=runtime.make_embed("RM • Control Center", "Choose a system."),
            view=SafeControlView(interaction.user.id),
        )


class SafeModerationView(SafeRMView):
    def __init__(self, owner_id: int):
        super().__init__(owner_id, 180)
        cases = discord.ui.Button(label="Cases", style=discord.ButtonStyle.secondary, emoji="📁")
        purge = discord.ui.Button(label="Purge", style=discord.ButtonStyle.secondary, emoji="🧹")
        back = discord.ui.Button(label="Back", style=discord.ButtonStyle.secondary, emoji="↩")
        cases.callback = self._cases
        purge.callback = self._purge
        back.callback = self._back
        for button in (cases, purge, back):
            self.add_item(button)

    async def _cases(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "Use `/casebrowser` for the interactive case browser.", ephemeral=True
        )

    async def _purge(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "Use `/purgeui <amount>` for confirmation + animated completion.",
            ephemeral=True,
        )

    async def _back(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            embed=runtime.make_embed("RM • Control Center", "Choose a system."),
            view=SafeControlView(interaction.user.id),
        )


class SafeSecurityView(SafeRMView):
    def __init__(self, owner_id: int):
        super().__init__(owner_id, 180)
        status = discord.ui.Button(label="Status", style=discord.ButtonStyle.primary, emoji="🛡️")
        back = discord.ui.Button(label="Back", style=discord.ButtonStyle.secondary, emoji="↩")
        status.callback = self._status
        back.callback = self._back
        self.add_item(status)
        self.add_item(back)

    async def _status(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return await interaction.response.send_message("Server only.", ephemeral=True)
        config = runtime.get_config(interaction.guild.id)
        text = (
            f"Links `{config['anti_links']}`\n"
            f"Invites `{config['anti_invites']}`\n"
            f"Spam `{config['anti_spam']}`\n"
            f"Mentions `{config['anti_mentions']}`\n"
            f"Caps `{config['anti_caps']}`\n"
            f"Verification `{config['verification_enabled']}`"
        )
        await interaction.response.edit_message(
            embed=runtime.make_embed("RM • Security Status", text), view=self
        )

    async def _back(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            embed=runtime.make_embed("RM • Control Center", "Choose a system."),
            view=SafeControlView(interaction.user.id),
        )


class SafeCommunityView(SafeRMView):
    def __init__(self, owner_id: int):
        super().__init__(owner_id, 180)
        poll = discord.ui.Button(label="Poll", style=discord.ButtonStyle.primary, emoji="📊")
        reminder = discord.ui.Button(label="Reminder", style=discord.ButtonStyle.secondary, emoji="⏰")
        back = discord.ui.Button(label="Back", style=discord.ButtonStyle.secondary, emoji="↩")
        poll.callback = self._poll
        reminder.callback = self._reminder
        back.callback = self._back
        for button in (poll, reminder, back):
            self.add_item(button)

    async def _poll(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "Use `/poll question options` to create an interactive poll.", ephemeral=True
        )

    async def _reminder(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "Use `/remind 10m message`.", ephemeral=True
        )

    async def _back(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            embed=runtime.make_embed("RM • Control Center", "Choose a system."),
            view=SafeControlView(interaction.user.id),
        )


# Replace only the shared view classes. Existing commands keep their callbacks,
# but their global ControlView name now resolves to the hardened implementation.
runtime.RMView = SafeRMView
runtime.ControlView = SafeControlView
runtime.ServerView = SafeServerView
runtime.ModerationView = SafeModerationView
runtime.SecurityView = SafeSecurityView
runtime.CommunityView = SafeCommunityView


async def _safe_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
    """Never hide the useful exception in the VPS console."""
    original = getattr(error, "original", error)
    print(
        f"[RM COMMAND ERROR] command={getattr(ctx.command, 'qualified_name', 'unknown')!r} "
        f"type={type(original).__name__}: {original!r}"
    )
    traceback.print_exception(type(original), original, original.__traceback__)

    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CheckFailure):
        message = "🚫 You don't have permission to use that."
    elif isinstance(error, commands.MissingRequiredArgument):
        message = f"Usage: `{runtime.PREFIX}{ctx.command.qualified_name} {ctx.command.signature}`"
    elif isinstance(error, commands.BadArgument):
        message = "❌ Invalid member, role, channel, or number."
    elif isinstance(original, discord.Forbidden):
        message = "❌ Discord denied that action. Check my permissions and role position."
    else:
        message = "❌ Something went wrong while running that command. The full error is now logged on the VPS."

    try:
        if getattr(ctx, "interaction", None) and ctx.interaction.response.is_done():
            await ctx.interaction.followup.send(message, ephemeral=True)
        else:
            await ctx.reply(message, ephemeral=True)
    except (discord.HTTPException, discord.NotFound):
        try:
            await ctx.send(message)
        except discord.HTTPException:
            pass


async def _safe_app_command_error(
    interaction: discord.Interaction, error: discord.app_commands.AppCommandError
) -> None:
    original = getattr(error, "original", error)
    print(
        f"[RM SLASH ERROR] command={getattr(interaction.command, 'qualified_name', 'unknown')!r} "
        f"type={type(original).__name__}: {original!r}"
    )
    traceback.print_exception(type(original), original, original.__traceback__)
    message = "❌ Something went wrong while running that command. The full error is now logged on the VPS."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except (discord.HTTPException, discord.NotFound):
        pass


runtime.bot.on_command_error = _safe_command_error
runtime.bot.tree.on_error = _safe_app_command_error
