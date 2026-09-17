"""RM Bot single-file runtime.

Run this file with:
    python3 main.py

It loads the Python modules from this repository directly into memory, so you do
not need to manually start bot.py, rm_plus.py, rm_ui.py, or rm_verify.py.

Environment variables are still read from .env when present. The actual command
implementation stays in the repo, which means updating the GitHub Python files
updates the runtime automatically the next time this process starts.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
import urllib.error
import urllib.request
from pathlib import Path

REPO = "itzmnyzzzXD/rmxyzBOT"
BRANCH = os.getenv("RM_BOT_BRANCH", "main")
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"

# Keep the dependency order from the repo:
# bot -> rm_plus -> rm_ui -> rm_verify
MODULES = ("bot", "rm_plus", "rm_ui", "rm_verify")


def _read_source(name: str) -> str:
    local = Path(__file__).with_name(f"{name}.py")
    if local.exists() and local.resolve() != Path(__file__).resolve():
        return local.read_text(encoding="utf-8")

    url = f"{RAW_BASE}/{name}.py"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "RM-Bot-Single-File-Runtime/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, UnicodeDecodeError) as exc:
        raise RuntimeError(
            f"Could not load {name}.py from {url}. "
            "Place the repo Python files beside main.py or restore internet access."
        ) from exc


def _load_module(name: str, source: str) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__file__ = f"{RAW_BASE}/{name}.py"
    module.__package__ = ""
    module.__loader__ = None
    sys.modules[name] = module
    try:
        exec(compile(source, module.__file__, "exec"), module.__dict__)
    except Exception:
        # Remove half-loaded modules so a failed start does not leave a broken
        # module object in sys.modules.
        sys.modules.pop(name, None)
        raise
    return module


def main() -> int:
    print("[RM] Starting single-file runtime...")
    print(f"[RM] Source: {RAW_BASE}")

    loaded: dict[str, types.ModuleType] = {}
    try:
        for name in MODULES:
            source = _read_source(name)
            print(f"[RM] Loading {name}.py ...")
            loaded[name] = _load_module(name, source)
    except Exception as exc:
        print(f"[RM] Startup failed: {exc}", file=sys.stderr)
        return 1

    bot_module = loaded["bot"]
    bot = bot_module.bot
    token = bot_module.TOKEN

    # rm_verify.py patches rm_plus.VerifyView at import time, while rm_ui.py
    # replaces the basic setup/help/config commands. All of that has already
    # happened because the modules were loaded above.
    if not token:
        print("[RM] DISCORD_TOKEN / DISCORD_BOT_TOKEN is missing.", file=sys.stderr)
        return 1

    print("[RM] All RM modules loaded into one process.")
    print("[RM] Starting Discord bot...")

    try:
        bot.run(token)
    except KeyboardInterrupt:
        print("\n[RM] Stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
