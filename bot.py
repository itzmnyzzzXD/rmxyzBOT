"""RM VPS launcher.

Fetches the last known-good bot core and applies the dashboard verification
patch. Environment values are normalized so Wispbyte variable casing and
accidental surrounding whitespace do not break the dashboard bridge.
"""

from __future__ import annotations

import os
import urllib.request

SOURCE_URL = "https://raw.githubusercontent.com/itzmnyzzzXD/rmxyzBOT/95f7ac7ac990a1c44e2c58eef1635da13197fb58/bot.py"

# Normalize the sync key before the fetched bot reads it.
key = (os.getenv("BOT_SYNC_KEY") or os.getenv("bot_sync_key") or "").strip()
if key:
    os.environ["BOT_SYNC_KEY"] = key

# Keep the dashboard URL configurable, but provide the production default.
dash = (os.getenv("DASHBOARD_URL") or os.getenv("dashboard_url") or "https://rmxyz.vercel.app").strip().rstrip("/")
os.environ["DASHBOARD_URL"] = dash

with urllib.request.urlopen(SOURCE_URL, timeout=20) as response:
    code = response.read().decode("utf-8")

# Patch the known-good source so both common Wispbyte key spellings work.
code = code.replace(
    'SYNC_KEY = os.getenv("BOT_SYNC_KEY", "")',
    'SYNC_KEY = (os.getenv("BOT_SYNC_KEY") or os.getenv("bot_sync_key") or "").strip()',
    1,
)

# Production RM verification does not collect or require email.
start = code.find("class VerifyEmailModal")
end = code.find("# ---------------------------------------------------------------------------\n# Boot", start)
if start == -1 or end == -1:
    raise RuntimeError("Could not locate the verification section in the known-good bot source")

verification = r'''async def create_verify_link(member: discord.Member, guild: discord.Guild) -> str:
    """Create a dashboard verification link with useful diagnostics.

    The dashboard may return a JSON error even when the HTTP request itself
    succeeds. Log the response body so VPS logs show the actual backend error
    instead of a generic JSON parsing exception.
    """
    if bot.session is None:
        raise RuntimeError("HTTP session is not ready")
    if not SYNC_KEY:
        raise RuntimeError("BOT_SYNC_KEY is missing")

    url = f"{DASHBOARD_URL}/api/public/verify/start"
    payload = {
        "discord_id": str(member.id),
        "discord_username": str(member),
        "guild_id": str(guild.id),
    }

    try:
        async with bot.session.post(
            url,
            json=payload,
            headers={"x-bot-key": SYNC_KEY},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            raw = await resp.text()
            content_type = resp.headers.get("content-type", "")
            print(f"[verify] POST {url} -> HTTP {resp.status} ({content_type})")
            if raw:
                print(f"[verify] dashboard response: {raw[:1500]}")

            try:
                data = await resp.json(content_type=None)
            except Exception as exc:
                raise RuntimeError(
                    f"dashboard returned non-JSON HTTP {resp.status}: {raw[:500]}"
                ) from exc

            if resp.status != 200:
                raise RuntimeError(f"dashboard returned HTTP {resp.status}: {data}")

            if not isinstance(data, dict) or not data.get("ok") or not data.get("url"):
                message = data.get("error") if isinstance(data, dict) else None
                raise RuntimeError(
                    f"dashboard verification failed: {message or data}"
                )

            return str(data["url"])
    except aiohttp.ClientError as exc:
        raise RuntimeError(f"dashboard request failed: {exc}") from exc


class VerifyStartView(discord.ui.View):
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
                "I couldn't create the dashboard link. Check the VPS/dashboard configuration.",
                ephemeral=True,
            )
        view = discord.ui.View(timeout=600)
        view.add_item(discord.ui.Button(label="Open RM Dashboard", url=url,
                                        style=discord.ButtonStyle.link))
        await interaction.response.send_message(
            embed=embed(
                "Your Discord permissions were verified. Open the dashboard and create your username and password. No email is required.",
                "RM Dashboard Verification"),
            view=view, ephemeral=True)


class Verification(commands.Cog):
    """Dashboard account onboarding without email."""

    @commands.command(name="verify", aliases=["verifyaccount", "panel"])
    @commands.guild_only()
    async def verify(self, ctx: commands.Context):
        if not _can_manage(ctx.author, ctx.guild):
            return await ctx.reply(embed=err(
                "You need Administrator or Manage Server to verify this server."),
                mention_author=False)
        await ctx.reply(
            embed=embed(
                "Press the button to create your RM dashboard account. You only need a dashboard username and password — no email.",
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
            "I couldn't create the dashboard link. Check the VPS/dashboard configuration.",
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
