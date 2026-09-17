import discord
from discord.ext import commands

from bot import bot, admin_only, get_config, make_embed, PALETTE, PREFIX
from rm_plus import full_setup, ConfigView


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
