"""
Version helper for Smart Whale Tracker.

Reads VERSION file at startup. Used by bot.py, monitor_nightly.py,
telegram_utils.py, and dashboard.py for consistent version reporting.

Versioning policy (Semantic Versioning):
  MAJOR . MINOR . PATCH
    1       2       3
       ↑       ↑       ↑
       |       |       └─ bug fixes, tiny tweaks (no behavior change)
       |       └───────── new features, behavior improvements (backwards compatible)
       └───────────────── breaking changes (data format, config schema)

Each change should bump exactly ONE of these numbers and reset the lower ones.
"""
import os

_VERSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")


def get_version() -> str:
    """Return the current version string (e.g. '2.1.0')."""
    try:
        with open(_VERSION_FILE, "r", encoding="utf-8") as f:
            return f.read().strip() or "0.0.0"
    except Exception:
        return "0.0.0"


def get_version_short() -> str:
    """Return just MAJOR.MINOR (e.g. '2.1')."""
    v = get_version().split(".")
    if len(v) >= 2:
        return f"{v[0]}.{v[1]}"
    return v[0] if v else "0"


def get_version_banner() -> str:
    """Return a banner string for log output."""
    return f"Smart Whale Tracker v{get_version()}"


if __name__ == "__main__":
    print(get_version_banner())
