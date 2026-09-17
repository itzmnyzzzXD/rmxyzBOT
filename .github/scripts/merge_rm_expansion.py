from pathlib import Path

BOT_PATH = Path("bot.py")
EXPANSION_PATH = Path("rm_expansion.py")
MARKER = "# === RM EXPANSION MERGED INTO BOT.PY ==="
UI_ERROR_MARKER = "# === RM FINAL INTERACTIVE ERROR HANDLER ==="


def clean_expansion(source: str) -> str:
    lines = []
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("from __future__ import "):
            lines.append("# [RM EXPANSION ORIGINAL IMPORT] " + line)
            continue
        if stripped.startswith("from bot import "):
            lines.append("# [RM EXPANSION ORIGINAL SELF-IMPORT] " + line)
            continue
        lines.append(line)
    return "\n".join(lines).rstrip()


def normalize_interactive_components(source: str) -> str:
    replacements = {
        'emoji="@"': 'emoji="📣"',
        'emoji=\'@\'': 'emoji="📣"',
        'emoji="✓"': 'emoji="✅"',
        'emoji=\'✓\'': 'emoji="✅"',
        'emoji="×"': 'emoji="❌"',
        'emoji=\'×\'': 'emoji="❌"',
        'emoji="↩"': 'emoji="🔙"',
        'emoji=\'↩\'': 'emoji="🔙"',
        'emoji="↻"': 'emoji="🔄"',
        'emoji=\'↻\'': 'emoji="🔄"',
        'emoji="⌂"': 'emoji="🏠"',
        'emoji=\'⌂\'': 'emoji="🏠"',
        'emoji="◀"': 'emoji="◀️"',
        'emoji=\'◀\'': 'emoji="◀️"',
        'emoji="▶"': 'emoji="▶️"',
        'emoji=\'▶\'': 'emoji="▶️"',
        'emoji="✦"': 'emoji="✨"',
        'emoji=\'✦\'': 'emoji="✨"',
        'emoji="⚠"': 'emoji="⚠️"',
        'emoji=\'⚠\'': 'emoji="⚠️"',
    }
    for old, new in replacements.items():
        source = source.replace(old, new)
    return source


FINAL_ERROR_HANDLER = r'''
import traceback as _rm_traceback


async def _rm_final_command_error(ctx, error):
    original = getattr(error, "original", error)
    command_name = getattr(getattr(ctx, "command", None), "qualified_name", "unknown")
    print(f"[RM COMMAND ERROR] command={command_name!r} type={type(original).__name__}: {original!r}")
    _rm_traceback.print_exception(type(original), original, original.__traceback__)

    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CheckFailure):
        message = "🚫 You don't have permission to use that."
    elif isinstance(error, commands.MissingRequiredArgument):
        command = getattr(ctx, "command", None)
        signature = getattr(command, "signature", "")
        message = f"Usage: `{PREFIX}{command.qualified_name} {signature}`" if command else "Missing required argument."
    elif isinstance(error, commands.BadArgument):
        message = "❌ Invalid member, role, channel, or number."
    elif isinstance(original, discord.Forbidden):
        message = "❌ Discord denied that action. Check my permissions and role position."
    else:
        message = f"❌ Command failed: `{type(original).__name__}`. The full traceback is in the VPS console."

    try:
        interaction = getattr(ctx, "interaction", None)
        if interaction is not None:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        else:
            await ctx.send(message)
    except (discord.HTTPException, discord.NotFound):
        pass
    except Exception:
        _rm_traceback.print_exc()


async def _rm_final_app_command_error(interaction, error):
    original = getattr(error, "original", error)
    command_name = getattr(getattr(interaction, "command", None), "qualified_name", "unknown")
    print(f"[RM SLASH ERROR] command={command_name!r} type={type(original).__name__}: {original!r}")
    _rm_traceback.print_exception(type(original), original, original.__traceback__)
    message = f"❌ Command failed: `{type(original).__name__}`. The full traceback is in the VPS console."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except (discord.HTTPException, discord.NotFound):
        pass
    except Exception:
        _rm_traceback.print_exc()


bot.on_command_error = _rm_final_command_error
bot.tree.on_error = _rm_final_app_command_error
'''.strip()


def inject_before_runtime(bot: str, block: str, marker: str) -> str:
    if marker in bot:
        return bot
    run_pos = bot.rfind("\nif __name__ == \"__main__\":\n    bot.run(TOKEN)")
    if run_pos == -1:
        run_pos = bot.rfind("\nbot.run(TOKEN)")
    if run_pos == -1:
        raise RuntimeError("Could not find bot.run(TOKEN) in bot.py; refusing to rewrite the file.")
    pre = bot[:run_pos].rstrip()
    post = bot[run_pos:]
    return pre + "\n\n" + marker + "\n" + block + "\n" + post + "\n"


def main() -> None:
    bot = BOT_PATH.read_text(encoding="utf-8")
    expansion = EXPANSION_PATH.read_text(encoding="utf-8")

    if MARKER not in bot:
        merged = clean_expansion(expansion)
        run_pos = bot.rfind("\nif __name__ == \"__main__\":\n    bot.run(TOKEN)")
        if run_pos == -1:
            run_pos = bot.rfind("\nbot.run(TOKEN)")
        if run_pos == -1:
            raise RuntimeError("Could not find bot.run(TOKEN) in bot.py; refusing to rewrite the file.")

        pre = bot[:run_pos].rstrip()
        post = bot[run_pos:]
        if not any(line.strip() == "import json" for line in pre.splitlines()):
            pre = pre.replace("import random\n", "import random\nimport json\n", 1)
        bot = pre + "\n\n" + MARKER + "\n" + merged + "\n" + post + "\n"
        print(f"Merged full rm_expansion.py into bot.py ({len(merged.splitlines())} expansion lines preserved).")
    else:
        print("RM expansion is already physically merged into bot.py; keeping existing runtime code.")

    bot = normalize_interactive_components(bot)
    bot = inject_before_runtime(bot, FINAL_ERROR_HANDLER, UI_ERROR_MARKER)
    BOT_PATH.write_text(bot, encoding="utf-8")
    print("Normalized interactive component payloads and installed the final traceback-preserving error handler in bot.py.")


if __name__ == "__main__":
    main()
