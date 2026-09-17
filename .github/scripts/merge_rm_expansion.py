from pathlib import Path

BOT_PATH = Path("bot.py")
EXPANSION_PATH = Path("rm_expansion.py")
UI_PATH = Path(".github/scripts/interactive_ui_hardening.py")
MARKER = "# === RM EXPANSION MERGED INTO BOT.PY ==="
UI_MARKER = "# === RM HARDENED INTERACTIVE UI ==="


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


def clean_ui(source: str) -> str:
    lines = []
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("from __future__ import "):
            continue
        if stripped == "HARDENED_UI_MARKER = \"# === RM HARDENED INTERACTIVE UI ===\"":
            continue
        if stripped == "import bot as runtime":
            continue
        line = line.replace("runtime.", "")
        lines.append(line)
    return "\n".join(lines).rstrip()


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

    if UI_PATH.exists() and UI_MARKER not in bot:
        ui = clean_ui(UI_PATH.read_text(encoding="utf-8"))
        bot = inject_before_runtime(bot, ui, UI_MARKER)
        print(f"Injected hardened interactive UI into bot.py ({len(ui.splitlines())} UI lines added).")
    elif UI_MARKER in bot:
        print("Hardened interactive UI is already in bot.py.")

    BOT_PATH.write_text(bot, encoding="utf-8")


if __name__ == "__main__":
    main()
