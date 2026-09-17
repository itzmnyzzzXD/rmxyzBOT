import importlib
import sys
import threading
import time
import traceback


def _autoload_rm():
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            main = sys.modules.get("__main__")
            if main is not None and getattr(main, "__file__", "").endswith("bot.py"):
                ready = all(hasattr(main, name) for name in ("bot", "setup_hook", "on_app_command_error", "setup", "config"))
                if ready:
                    sys.modules.setdefault("bot", main)
                    importlib.import_module("rm_plus")
                    importlib.import_module("rm_ui")
                    importlib.import_module("rm_verify")
                    return
            time.sleep(0.05)
    except Exception:
        try:
            with open("rm_ai_runtime.log", "a", encoding="utf-8") as fh:
                fh.write("\n[RM AUTOLOAD ERROR]\n" + traceback.format_exc())
        except OSError:
            pass


threading.Thread(target=_autoload_rm, name="rm-autoload", daemon=True).start()
