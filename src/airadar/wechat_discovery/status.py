from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .models import (
    FAILURE_STATES,
    BackendRequest,
    DiscoveryAttempt,
    DiscoveryConfig,
    DiscoveryGateState,
    DiscoveryState,
    EffectiveStatus,
)

WECHAT_PLATFORM_TZ = ZoneInfo("Asia/Shanghai")


def _has_recorded_rate_limit(
    state: str,
    platform_error_ret: int | None,
    platform_error_ret_origin: str,
) -> bool:
    return (
        state == DiscoveryState.RATE_LIMITED.value
        and platform_error_ret_origin == "recorded"
        and platform_error_ret is not None
    )


def manual_probe_blocked_until(
    config: DiscoveryConfig,
    latest_attempt: DiscoveryAttempt | None,
    *,
    now: datetime | None = None,
) -> datetime | None:
    if latest_attempt is None:
        return None
    current = now or datetime.now(UTC)
    if latest_attempt.state not in FAILURE_STATES:
        if latest_attempt.finished_at is None:
            return latest_attempt.started_at + config.refresh_interval
        candidate = latest_attempt.finished_at + config.refresh_interval
        return candidate if candidate > current else None
    if not _has_recorded_rate_limit(
        latest_attempt.state.value,
        latest_attempt.platform_error_ret,
        latest_attempt.platform_error_ret_origin,
    ):
        return None
    if latest_attempt.finished_at is None:
        return latest_attempt.started_at + config.refresh_interval
    finished_local = latest_attempt.finished_at.astimezone(WECHAT_PLATFORM_TZ)
    current_local = current.astimezone(WECHAT_PLATFORM_TZ)
    if finished_local.date() != current_local.date():
        return None
    next_day = finished_local.date() + timedelta(days=1)
    candidate = datetime.combine(next_day, time.min, tzinfo=WECHAT_PLATFORM_TZ).astimezone(UTC)
    return candidate if candidate > current else None


def backend_request_blocked_until(
    config: DiscoveryConfig,
    latest_request: BackendRequest | None,
    *,
    now: datetime | None = None,
) -> datetime | None:
    if latest_request is None:
        return None
    current = now or datetime.now(UTC)
    if latest_request.state == "reserved":
        candidate = latest_request.started_at + config.refresh_interval
        return candidate if candidate > current else None
    if latest_request.state in {
        "resolved",
        "provisional_match",
        "legacy_name_and_biz_match",
        DiscoveryState.SUCCESS.value,
        DiscoveryState.SUCCESS_NO_NEW_SHADOW_CANDIDATES.value,
        DiscoveryState.SUCCESS_WITH_NEW_SHADOW_CANDIDATES.value,
        DiscoveryState.IDENTITY_UNVERIFIED.value,
        DiscoveryState.IDENTITY_MISMATCH.value,
    }:
        if latest_request.finished_at is None:
            raise ValueError("terminal backend request has no completion time")
        candidate = latest_request.finished_at + config.refresh_interval
        return candidate if candidate > current else None
    if not _has_recorded_rate_limit(
        latest_request.state,
        latest_request.platform_error_ret,
        latest_request.platform_error_ret_origin,
    ):
        return None
    if latest_request.finished_at is None:
        raise ValueError("terminal backend request has no completion time")
    finished_local = latest_request.finished_at.astimezone(WECHAT_PLATFORM_TZ)
    current_local = current.astimezone(WECHAT_PLATFORM_TZ)
    if finished_local.date() != current_local.date():
        return None
    candidate = datetime.combine(
        finished_local.date() + timedelta(days=1),
        time.min,
        tzinfo=WECHAT_PLATFORM_TZ,
    ).astimezone(UTC)
    return candidate if candidate > current else None


def effective_status(
    config: DiscoveryConfig,
    *,
    credential_path: str | Path,
    latest_attempt: DiscoveryAttempt | None = None,
    latest_request: BackendRequest | None = None,
    resolved_ready_count: int = 0,
    assigned_count: int = 0,
    invalidated_count: int = 0,
    unresolved_count: int | None = None,
    ready_accounts: tuple[tuple[str, int], ...] = (),
    now: datetime | None = None,
) -> EffectiveStatus:
    account_count = len(config.accounts)
    unresolved = account_count if unresolved_count is None else unresolved_count
    if not config.manual_backend_requests_enabled:
        return EffectiveStatus(DiscoveryGateState.DISABLED, account_count)
    if not Path(credential_path).is_file():
        return EffectiveStatus(DiscoveryGateState.UNCONFIGURED, account_count)
    next_request_at = backend_request_blocked_until(config, latest_request, now=now)
    if (
        latest_request is not None
        and latest_request.state == "reserved"
        and next_request_at is not None
    ):
        state = DiscoveryGateState.REQUEST_OUTCOME_UNKNOWN
    elif next_request_at is not None:
        state = DiscoveryGateState.COOLDOWN
    elif resolved_ready_count:
        state = DiscoveryGateState.READY_TO_PROBE
    else:
        state = DiscoveryGateState.READY_TO_RESOLVE
    return EffectiveStatus(
        state,
        account_count,
        next_request_at,
        latest_attempt,
        latest_request,
        resolved_ready_count,
        assigned_count,
        invalidated_count,
        unresolved,
        ready_accounts,
    )
