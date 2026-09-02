from __future__ import annotations

from datetime import UTC, datetime

RULESET_REV: str = "r1"
RULESET_REV_V2: str = "r2"
PINNED_RULESET_DATE: str = "2026-05-13"


def git_short_hash() -> str:
    return "nogit"


def current_version() -> str:
    date = PINNED_RULESET_DATE or datetime.now(UTC).strftime("%Y-%m-%d")
    return f"{date}.{RULESET_REV}"


def current_version_v2() -> str:
    """r2 ruleset stamp for the content-v2 enrich pipeline (runner_v2).

    Mirrors current_version()'s constant-concatenation mechanism with a
    distinct revision suffix so v2 item_evaluations rows (ruleset_version
    ending in .r2) are naturally isolated from v1 rows (.r1) — no eval/
    content_contract dependency, per the content-v2 integration decision.
    """
    date = PINNED_RULESET_DATE or datetime.now(UTC).strftime("%Y-%m-%d")
    return f"{date}.{RULESET_REV_V2}"
