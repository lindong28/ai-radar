from __future__ import annotations

from datetime import UTC, datetime

RULESET_REV: str = "r1"
PINNED_RULESET_DATE: str = "2026-05-13"


def git_short_hash() -> str:
    return "nogit"


def current_version() -> str:
    date = PINNED_RULESET_DATE or datetime.now(UTC).strftime("%Y-%m-%d")
    return f"{date}.{RULESET_REV}"
