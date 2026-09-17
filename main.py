"""RM Bot single-entry runtime.

bot.py remains the complete core runtime. rm_expansion.py is loaded here before
startup so the existing RM expansion features and interactive workflows are
registered into the same Discord client without deleting or replacing bot.py.
"""

from __future__ import annotations

import sys

from bot import TOKEN, bot
import rm_expansion  # noqa: F401  # registers RM expansion commands/views/listeners


def main() -> int:
    if not TOKEN:
        print("[RM] DISCORD_TOKEN / DISCORD_BOT_TOKEN is missing.", file=sys.stderr)
        return 1

    print("[RM] Integrated runtime loading...")
    print("[RM] Core bot + RM expansion + interactive UI registered")
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
