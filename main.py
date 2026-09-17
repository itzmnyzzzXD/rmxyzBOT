"""RM Bot single-entry runtime.

The original bot.py remains the core runtime. rm_expansion.py registers the
additive UI, utility, moderation workflows and private RM Core AI before the
Discord client starts.
"""

from __future__ import annotations

import sys

from bot import TOKEN, bot
import rm_expansion  # noqa: F401  # registers additive commands/listeners/views


def main() -> int:
    if not TOKEN:
        print("[RM] DISCORD_TOKEN / DISCORD_BOT_TOKEN is missing.", file=sys.stderr)
        return 1

    print("[RM] Integrated runtime loading...")
    print("[RM] Additive expansion loaded: UI + utilities + moderation + RM Core AI")
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
