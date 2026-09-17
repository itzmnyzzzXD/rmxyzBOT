"""RM Bot single-entry runtime.

bot.py is the complete runtime. The RM expansion and interactive panels are
merged directly into bot.py, so this launcher must not import them a second time.
"""

from __future__ import annotations

import sys

from bot import TOKEN, bot


def main() -> int:
    if not TOKEN:
        print("[RM] DISCORD_TOKEN / DISCORD_BOT_TOKEN is missing.", file=sys.stderr)
        return 1

    print("[RM] Integrated runtime loading...")
    print("[RM] bot.py contains core + RM expansion + expanded interactive UI")
    print("[RM] Commands: slash + configured prefix")
    print("[RM] Servers will be reported after login.")

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
