from __future__ import annotations

from datetime import UTC, datetime

RULESET_REV: str = "r1"
RULESET_REV_V2: str = "r2"
PINNED_RULESET_DATE: str = "2026-05-13"
# Scoring carries its own date because its behaviour changed on its own schedule: the prompt
# gained a `significance` dimension on 2026-09-06 (ADR-20260906-7c31) while prefilter and enrich
# v1 were untouched. Sharing one constant made rows from either side of that change claim the
# same version -- indistinguishable afterwards -- and, worse, unreachable: the scoring runner
# skips items that already have a row at the current version, so a change that leaves the
# version alone can never reach the archive at all.
PINNED_SCORE_RULESET_DATE: str = "2026-09-06"


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


def current_score_version() -> str:
    """Scoring's own stamp. Bump PINNED_SCORE_RULESET_DATE whenever scoring behaviour changes.

    A bump makes every already-scored item a candidate again, so the next run re-scores whatever
    falls inside its --since window and a wider window backfills the rest.
    """
    date = PINNED_SCORE_RULESET_DATE or datetime.now(UTC).strftime("%Y-%m-%d")
    return f"{date}.{RULESET_REV}"
