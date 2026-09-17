import discord

from bot import bot, get_config, make_embed, PALETTE
from rm_plus import get_question, plus_log


class VerifyQuestionModal(discord.ui.Modal, title="RM • Verification"):
    answer = discord.ui.TextInput(label="Answer", style=discord.TextStyle.short, max_length=300)

    def __init__(self, member):
        super().__init__()
        self.member = member

    async def on_submit(self, interaction):
        question = get_question(interaction.guild.id) if interaction.guild else None
        if not question:
            return await interaction.response.send_message("Verification is not configured.", ephemeral=True)
        expected = (question["expected"] or "").strip().casefold()
        answer = self.answer.value.strip().casefold()
        if expected and answer != expected:
            await interaction.response.send_message("❌ That answer wasn't accepted. Try again.", ephemeral=True)
            return
        guild = interaction.guild
        cfg = get_config(guild.id)
        role = guild.get_role(cfg["verify_role"]) if cfg["verify_role"] else None
        if not role or role >= guild.me.top_role:
            return await interaction.response.send_message("Verification role is not configured correctly.", ephemeral=True)
        try:
            await self.member.add_roles(role, reason="RM question verification")
            unverified = guild.get_role(cfg["unverified_role"]) if cfg["unverified_role"] else None
            if unverified and unverified in self.member.roles and unverified < guild.me.top_role:
                await self.member.remove_roles(unverified, reason="RM question verification")
            await interaction.response.send_message("✅ Verified. Welcome in.", ephemeral=True)
        except discord.HTTPException as exc:
            plus_log(f"Question verification role update failed: {exc}")
            await interaction.response.send_message("I couldn't update your roles.", ephemeral=True)


class QuestionVerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Start Verification", style=discord.ButtonStyle.success, emoji="✓", custom_id="rm:question_verify")
    async def start(self, interaction, button):
        question = get_question(interaction.guild.id) if interaction.guild else None
        if not question:
            return await interaction.response.send_message("No verification question is configured. Ask an administrator.", ephemeral=True)
        await interaction.response.send_modal(VerifyQuestionModal(interaction.user))


@bot.hybrid_command(name="verifychallenge", description="Deploy the RM member verification question panel.")
async def verifychallenge(ctx, channel: discord.TextChannel = None):
    if not ctx.guild or not (ctx.author.guild_permissions.manage_guild or ctx.author.guild_permissions.administrator):
        return await ctx.reply("You need Manage Server to deploy this.")
    question = get_question(ctx.guild.id)
    if not question:
        return await ctx.reply("Set a question first: `verifyquestions set Question || expected-answer`.")
    channel = channel or ctx.channel
    embed = make_embed("🔐 RM • Verification Challenge", f"**Question:** {question['question']}\n\nPress **Start Verification** to answer privately.")
    await channel.send(embed=embed, view=QuestionVerifyView())
    await ctx.reply(f"✅ Verification challenge deployed in {channel.mention}.")


# full_setup resolves VerifyView from rm_plus at call time, so replace it with the
# question-aware view without altering the original core bot file.
import rm_plus as _rm_plus
_rm_plus.VerifyView = QuestionVerifyView
