"""RM Bot single-entry runtime.

bot.py is the complete runtime: the core bot and the full RM expansion UI,
commands, animations, views, listeners, and interactive systems are all merged
into that single module.
"""

from __future__ import annotations

import sys

from bot import TOKEN, bot
import interactive_fix  # noqa: F401 - hardens the shared interactive UI/error layer


def main() -> int:
    if not TOKEN:
        print("[RM] DISCORD_TOKEN / DISCORD_BOT_TOKEN is missing.", file=sys.stderr)
        return 1

    print("[RM] Loading complete bot.py runtime...")
    print("[RM] Core bot + full RM expansion + interactive UI loaded")
    print("[RM] Interactive UI hardening loaded")
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
