"""RM VPS launcher.

The full bot source lives in the last known-good GitHub revision. This tiny
launcher keeps Wispbyte from running a damaged local copy and applies the
production dashboard fixes before starting RM.
"""

from __future__ import annotations

import urllib.request

SOURCE_URL = "https://raw.githubusercontent.com/itzmnyzzzXD/rmxyzBOT/95f7ac7ac990a1c44e2c58eef1635da13197fb58/bot.py"

with urllib.request.urlopen(SOURCE_URL, timeout=20) as response:
    code = response.read().decode("utf-8")

# Keep existing Wispbyte configurations working whether the variable was
# entered as BOT_SYNC_KEY or bot_sync_key.
code = code.replace(
    'SYNC_KEY = os.getenv("BOT_SYNC_KEY", "")',
    'SYNC_KEY = os.getenv("BOT_SYNC_KEY") or os.getenv("bot_sync_key") or ""',
    1,
)

# Production RM verification does not collect or require email.
start = code.find("class VerifyEmailModal")
end = code.find("# ---------------------------------------------------------------------------\n# Boot", start)
if start == -1 or end == -1:
    raise RuntimeError("Could not locate the verification section in the known-good bot source")

verification = r'''class VerifyStartView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Create RM Dashboard", style=discord.ButtonStyle.danger)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            return await interaction.response.send_message(
                "Run this inside the server you want to manage.", ephemeral=True)
        if not _can_manage(member, guild):
            return await interaction.response.send_message(
                "You need Administrator or Manage Server to verify this server.", ephemeral=True)
        try:
            url = await create_verify_link(member, guild)
        except Exception as exc:
            print(f"[verify] failed: {exc}")
            return await interaction.response.send_message(
                "I couldn't create the dashboard link. Check BOT_SYNC_KEY and DASHBOARD_URL.",
                ephemeral=True,
            )
        view = discord.ui.View(timeout=600)
        view.add_item(discord.ui.Button(label="Open RM Dashboard", url=url,
                                        style=discord.ButtonStyle.link))
        await interaction.response.send_message(
            embed=embed(
                "Your server ownership/management permission was verified. "
                "Open the dashboard and create your username and password. No email is required.",
                "RM Dashboard Verification"),
            view=view, ephemeral=True)


class Verification(commands.Cog):
    """Dashboard account onboarding."""

    @commands.command(name="verify", aliases=["verifyaccount", "panel"])
    @commands.guild_only()
    async def verify(self, ctx: commands.Context):
        if not _can_manage(ctx.author, ctx.guild):
            return await ctx.reply(embed=err(
                "You need Administrator or Manage Server to verify this server."),
                mention_author=False)
        await ctx.reply(
            embed=embed(
                "Your Discord permissions check out. Press the button to create your RM dashboard account. "
                "You only need a dashboard username and password — no email.",
                "RM Verification"),
            view=VerifyStartView(), mention_author=False)


@bot.tree.command(name="verify", description="Create your RM Dashboard account")
async def slash_verify(interaction: discord.Interaction):
    guild = interaction.guild
    member = interaction.user
    if guild is None or not isinstance(member, discord.Member):
        return await interaction.response.send_message(
            "Run /verify inside the server you want to manage.", ephemeral=True)
    if not _can_manage(member, guild):
        return await interaction.response.send_message(
            "You need Administrator or Manage Server to verify this server.", ephemeral=True)
    try:
        url = await create_verify_link(member, guild)
    except Exception as exc:
        print(f"[verify] failed: {exc}")
        return await interaction.response.send_message(
            "I couldn't create the dashboard link. Check BOT_SYNC_KEY and DASHBOARD_URL.",
            ephemeral=True,
        )
    view = discord.ui.View(timeout=600)
    view.add_item(discord.ui.Button(label="Open RM Dashboard", url=url,
                                    style=discord.ButtonStyle.link))
    await interaction.response.send_message(
        embed=embed(
            "Create your dashboard username and password on the secure RM page. No email is required.",
            "RM Dashboard Verification"),
        view=view, ephemeral=True)


'''

code = code[:start] + verification + code[end:]
exec(compile(code, "bot.py", "exec"))
