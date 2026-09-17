"""RM Bot single-entry runtime.

All Discord functionality is already integrated into bot.py. This launcher
keeps the runtime simple: import bot.py once, let every registered cog/view/
listener/command initialize, then start the Discord client.
"""

from __future__ import annotations

import sys

from bot import TOKEN, bot


def main() -> int:
    if not TOKEN:
        print("[RM] DISCORD_TOKEN / DISCORD_BOT_TOKEN is missing.", file=sys.stderr)
        return 1

    print("[RM] Integrated runtime loading...")
    print(f"[RM] Commands: slash + configured prefix")
    print(f"[RM] Servers will be reported after login.")

    try:
        bot.run(TOKEN)
    except KeyboardInterrupt:
        print("\n[RM] Stopped.")
    except Exception as exc:
        print(f"[RM] Fatal runtime error: {exc}", file=sys.stderr)
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
