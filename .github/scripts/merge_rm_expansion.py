from pathlib import Path

BOT_PATH = Path("bot.py")
EXPANSION_PATH = Path("rm_expansion.py")
MARKER = "# === RM EXPANSION MERGED INTO BOT.PY ==="


def clean_expansion(source: str) -> str:
    lines = []
    for line in source.splitlines():
        stripped = line.strip()
        # Keep the original lines as provenance, but they cannot execute here.
        # A future import is only legal at the beginning of a Python module.
        if stripped.startswith("from __future__ import "):
            lines.append("# [RM EXPANSION ORIGINAL IMPORT] " + line)
            continue
        # The expansion imported symbols from bot.py. After flattening, those
        # imports would become a self-import/circular import, so retain the exact
        # original line as a comment while executing the same code in-module.
        if stripped.startswith("from bot import "):
            lines.append("# [RM EXPANSION ORIGINAL SELF-IMPORT] " + line)
            continue
        lines.append(line)
    return "\n".join(lines).rstrip()


def main() -> None:
    bot = BOT_PATH.read_text(encoding="utf-8")
    expansion = EXPANSION_PATH.read_text(encoding="utf-8")

    if MARKER in bot:
        print("RM expansion is already physically merged into bot.py; no changes made.")
        return

    merged = clean_expansion(expansion)

    # bot.py starts the actual Discord client with bot.run(...). Expansion code
    # must be registered before that blocking call, otherwise its commands/views
    # would never be added to the running client.
    run_pos = bot.rfind("\nif __name__ == \"__main__\":\n    bot.run(TOKEN)")
    if run_pos == -1:
        run_pos = bot.rfind("\nbot.run(TOKEN)")
    if run_pos == -1:
        raise RuntimeError("Could not find bot.run(TOKEN) in bot.py; refusing to rewrite the file.")

    pre = bot[:run_pos].rstrip()
    post = bot[run_pos:]

    # rm_expansion.py uses json but bot.py may not already import it.
    if not any(line.strip() == "import json" for line in pre.splitlines()):
        pre = pre.replace("import random\n", "import random\nimport json\n", 1)

    merged_text = (
        pre
        + "\n\n"
        + MARKER
        + "\n"
        + merged
        + "\n"
        + post
        + "\n"
    )
    BOT_PATH.write_text(merged_text, encoding="utf-8")
    print(f"Merged full rm_expansion.py into bot.py ({len(merged.splitlines())} expansion lines preserved).")


if __name__ == "__main__":
    main()
