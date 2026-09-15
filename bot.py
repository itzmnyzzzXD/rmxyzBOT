"""RM VPS launcher.

Fetches the last known-good bot core and applies the dashboard onboarding
patch. Environment values are normalized so Wispbyte variable casing and
accidental surrounding whitespace do not break the dashboard bridge.
"""

from __future__ import annotations

import os
import urllib.request

SOURCE_URL = "https://raw.githubusercontent.com/itzmnyzzzXD/rmxyzBOT/95f7ac7ac990a1c44e2c58eef1635da13197fb58/bot.py"

key = (os.getenv("BOT_SYNC_KEY") or os.getenv("bot_sync_key") or "").strip()
if key:
    os.environ["BOT_SYNC_KEY"] = key

dash = (os.getenv("DASHBOARD_URL") or os.getenv("dashboard_url") or "https://rmxyzbot.vercel.app").strip().rstrip("/")
os.environ["DASHBOARD_URL"] = dash

with urllib.request.urlopen(SOURCE_URL, timeout=20) as response:
    code = response.read().decode("utf-8")

code = code.replace(
    'SYNC_KEY = os.getenv("BOT_SYNC_KEY", "")',
    'SYNC_KEY = (os.getenv("BOT_SYNC_KEY") or os.getenv("bot_sync_key") or "").strip()',
    1,
)

start = code.find("class VerifyEmailModal")
end = code.find("# ---------------------------------------------------------------------------\n# Boot", start)
if start == -1 or end == -1:
    raise RuntimeError("Could not locate the verification section in the known-good bot source")

verification = r'''async def provision_dashboard(member: discord.Member, guild: discord.Guild) -> tuple[str, str]:
    if bot.session is None:
        raise RuntimeError("HTTP session is not ready")
    if not SYNC_KEY:
        raise RuntimeError("BOT_SYNC_KEY is missing")

    endpoint = f"{DASHBOARD_URL}/api/public/verify/provision"
    payload = {
        "discord_id": str(member.id),
        "discord_username": str(member),
        "guild_id": str(guild.id),
        "guild_name": guild.name,
    }

    async with bot.session.post(
        endpoint,
        json=payload,
        headers={"x-bot-key": SYNC_KEY},
        timeout=aiohttp.ClientTimeout(total=15),
    ) as resp:
        raw = await resp.text()
        print(f"[verify] HTTP {resp.status}: {raw[:1200]}")
        try:
            data = await resp.json(content_type=None)
        except Exception as exc:
            raise RuntimeError(f"Dashboard returned non-JSON HTTP {resp.status}: {raw[:400]}") from exc
        if resp.status != 200 or not isinstance(data, dict) or not data.get("ok"):
            raise RuntimeError(str(data.get("error", data)) if isinstance(data, dict) else str(data))
        user = str(data.get("username") or "")
        secret = str(data.get("password") or "")
        if not user or not secret:
            raise RuntimeError("Dashboard did not return account details")
        return user, secret


def verify_admin(member: discord.Member, guild: discord.Guild) -> bool:
    return member.id == guild.owner_id or member.guild_permissions.administrator or member.id in OWNER_IDS


class Verification(commands.Cog):
    """Administrator-only RM dashboard account setup."""

    @commands.command(name="verify", aliases=["verifyaccount", "panel"])
    @commands.guild_only()
    async def verify(self, ctx: commands.Context):
        if not isinstance(ctx.author, discord.Member) or not verify_admin(ctx.author, ctx.guild):
            return await ctx.reply(
                embed=err("Only the server owner or an Administrator can use verify."),
                mention_author=False,
                delete_after=8,
            )
        try:
            user, secret = await provision_dashboard(ctx.author, ctx.guild)
            message = embed(
                f"**Server:** `{ctx.guild.name}`\n\n**Username:** `{user}`\n**Password:** `{secret}`\n\nLog in once to RM Control Center. Your session is remembered on this device.",
                "RM Dashboard Access",
            )
            try:
                await ctx.author.send(embed=message)
                return await ctx.reply(
                    embed=ok("Your RM dashboard access was sent to your DMs."),
                    mention_author=False,
                    delete_after=10,
                )
            except discord.Forbidden:
                return await ctx.reply(
                    embed=err("I created the account, but your DMs are closed. Enable DMs from server members and run verify again."),
                    mention_author=False,
                )
        except Exception as exc:
            print(f"[verify] failed: {exc}")
            return await ctx.reply(
                embed=err("I couldn't create the RM dashboard account. Check the dashboard backend and Redis connection."),
                mention_author=False,
            )


@bot.tree.command(name="verify", description="Create or rotate RM Dashboard access")
async def slash_verify(interaction: discord.Interaction):
    guild = interaction.guild
    member = interaction.user
    if guild is None or not isinstance(member, discord.Member):
        return await interaction.response.send_message("Run /verify inside a server.", ephemeral=True)
    if not verify_admin(member, guild):
        return await interaction.response.send_message(
            "Only the server owner or an Administrator can use /verify.",
            ephemeral=True,
        )

    await interaction.response.defer(ephemeral=True)
    try:
        user, secret = await provision_dashboard(member, guild)
        await interaction.followup.send(
            embed=embed(
                f"**Server:** `{guild.name}`\n\n**Username:** `{user}`\n**Password:** `{secret}`\n\nLog in once to RM Control Center. Your session is remembered on this device.",
                "RM Dashboard Access",
            ),
            ephemeral=True,
        )
    except Exception as exc:
        print(f"[verify] failed: {exc}")
        await interaction.followup.send(
            embed=err("I couldn't create the RM dashboard account. Check the dashboard backend and Redis connection."),
            ephemeral=True,
        )


'''

code = code[:start] + verification + code[end:]
exec(compile(code, "bot.py", "exec"))
