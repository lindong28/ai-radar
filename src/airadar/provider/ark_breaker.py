"""Time-based circuit breaker for the Volcengine ARK agent-plan endpoint.

When ARK returns a quota / rate-limit error (i.e. the monthly agent-plan
allowance is exhausted), retrying ARK on every item just wastes one failed
request per item until the next reset. This breaker records the failure to a
small state file; while it stays open (default 2h) callers skip ARK and go
straight to the DeepSeek fallback. After the cooldown it retries ARK once,
self-healing when the allowance refills.

State is file-backed (not in-memory) so it survives across the short-lived
pipeline processes that cron spawns per stage.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ..db import PROJECT_ROOT

DEFAULT_COOLDOWN_SECONDS = 7200  # 2 hours

# Substrings that mark an ARK error as "retrying soon is futile" (quota / rate
# limit / billing). Kept deliberately broad: a false positive only costs a 2h
# detour to the paid DeepSeek path (self-healing), while a miss means we keep
# wasting one failed ARK call per item. Refine once a real exhaustion error is
# observed in production logs.
_QUOTA_ERROR_KEYWORDS = (
    "quota",
    "limit",
    "insufficient",
    "overdue",
    "balance",
    "exhaust",
    "exceeded",
    "too many requests",
)


def _state_path() -> Path:
    configured = os.environ.get("AI_RADAR_ARK_BREAKER_STATE")
    if configured:
        return Path(configured)
    return PROJECT_ROOT / "data" / "ark-breaker.json"


def _cooldown_seconds() -> float:
    try:
        return float(os.environ.get("AI_RADAR_ARK_BREAKER_COOLDOWN_SECONDS", DEFAULT_COOLDOWN_SECONDS))
    except ValueError:
        return float(DEFAULT_COOLDOWN_SECONDS)


def _now() -> float:
    return time.time()


def is_open() -> bool:
    """True while the breaker is tripped and ARK should be skipped."""
    try:
        raw = _state_path().read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return False
    try:
        opened_at = float(json.loads(raw).get("opened_at", 0.0))
    except (ValueError, TypeError, AttributeError):
        return False
    return (_now() - opened_at) < _cooldown_seconds()


def is_quota_error(exc: BaseException) -> bool:
    """Whether an ARK exception looks like quota / rate-limit exhaustion."""
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if status == 429:
        return True
    text = str(exc).lower()
    return any(keyword in text for keyword in _QUOTA_ERROR_KEYWORDS)


def trip(reason: str) -> None:
    """Open the breaker now, persisting the reason for observability."""
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"opened_at": _now(), "reason": reason[:300]}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def record_failure(exc: BaseException) -> bool:
    """Trip the breaker iff `exc` is a quota / rate-limit error. Returns whether tripped."""
    if is_quota_error(exc):
        trip(f"{type(exc).__name__}: {exc}")
        return True
    return False
