from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

X_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
X_RUNTIME_SCHEMA_VERSION = 1
X_RUNTIME_META_KEYS = frozenset(
    {
        "x_state_schema_version",
        "x_cursor_state",
        "x_reference_status",
        "x_reference_validated_at",
        "x_reference_attempted_at",
        "x_reference_reason",
        "x_reference_recovery",
        "x_user_id",
        "x_initial_start_time",
        "x_since_id",
        "x_since_time",
        "x_pagination_token",
        "x_pending_since_id",
        "x_pending_start_time",
    }
)


def x_runtime_meta(meta: Mapping[str, Any]) -> dict[str, Any]:
    runtime = {key: meta[key] for key in X_RUNTIME_META_KEYS if key in meta}
    if runtime and "x_state_schema_version" not in runtime:
        runtime = {"x_state_schema_version": X_RUNTIME_SCHEMA_VERSION, **runtime}
    return runtime


def without_x_runtime_meta(meta: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in meta.items() if key not in X_RUNTIME_META_KEYS}


def _valid_timestamp(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def validate_x_runtime_meta(meta: Mapping[str, Any], *, context: str) -> dict[str, Any]:
    unknown = {str(key) for key in meta if str(key).startswith("x_")} - X_RUNTIME_META_KEYS
    if unknown:
        raise ValueError(f"invalid X runtime state for {context}: unknown keys {sorted(unknown)}")

    runtime = x_runtime_meta(meta)
    schema_version = runtime.get("x_state_schema_version")
    if schema_version is None:
        runtime = {"x_state_schema_version": X_RUNTIME_SCHEMA_VERSION, **runtime}
    elif schema_version != X_RUNTIME_SCHEMA_VERSION:
        raise ValueError(f"invalid X runtime state for {context}: unsupported schema version")
    reference_status = runtime.get("x_reference_status")
    if not isinstance(reference_status, str) or reference_status not in {"blocked", "pending", "verified"}:
        raise ValueError(f"invalid X runtime state for {context}: reference status is missing or invalid")
    validated_at = runtime.get("x_reference_validated_at")
    if reference_status == "pending" and validated_at is not None:
        raise ValueError(f"invalid X runtime state for {context}: pending reference has validation time")
    if reference_status == "verified" and (
        not isinstance(validated_at, str) or not _valid_timestamp(validated_at)
    ):
        raise ValueError(f"invalid X runtime state for {context}: verified reference needs validation time")
    blocked_fields = {"x_reference_attempted_at", "x_reference_reason", "x_reference_recovery"}
    if reference_status == "blocked":
        if validated_at is not None or not blocked_fields <= runtime.keys():
            raise ValueError(f"invalid X runtime state for {context}: blocked reference needs attempt details")
    elif blocked_fields & runtime.keys():
        raise ValueError(f"invalid X runtime state for {context}: attempt details require blocked status")
    state = runtime.get("x_cursor_state")
    if state not in {"identity_pending", "uninitialized", "checkpointed", "draining"}:
        raise ValueError(f"invalid X runtime state for {context}: cursor state is missing or invalid")
    for key, value in runtime.items():
        if key == "x_state_schema_version":
            continue
        if not isinstance(value, str) or not value:
            raise ValueError(f"invalid X runtime state for {context}: {key} must be a non-empty string")
    for key in ("x_user_id", "x_since_id", "x_pending_since_id"):
        value = runtime.get(key)
        if value is not None and not value.isdigit():
            raise ValueError(f"invalid X runtime state for {context}: {key} must be a decimal ID")
    user_id_value = runtime.get("x_user_id")
    if user_id_value is not None and len(user_id_value) > 19:
        raise ValueError(f"invalid X runtime state for {context}: x_user_id must be at most 19 digits")
    for key in (
        "x_initial_start_time",
        "x_since_time",
        "x_pending_start_time",
        "x_reference_validated_at",
        "x_reference_attempted_at",
    ):
        value = runtime.get(key)
        if value is not None and not _valid_timestamp(value):
            raise ValueError(f"invalid X runtime state for {context}: {key} must be an aware timestamp")

    if "x_since_id" in runtime and "x_since_time" in runtime:
        raise ValueError(f"invalid X runtime state for {context}: committed checkpoints are mutually exclusive")
    anchors = {"x_since_id", "x_since_time"} & runtime.keys()
    pagination_keys = {
        "x_pagination_token",
        "x_pending_since_id",
        "x_pending_start_time",
    } & runtime.keys()
    user_id = runtime.get("x_user_id")
    initial_start_time = runtime.get("x_initial_start_time")
    if state == "identity_pending" and (user_id or initial_start_time or anchors or pagination_keys):
        raise ValueError(f"invalid X runtime state for {context}: pending identity has resolved state")
    if state == "uninitialized" and (
        user_id is None or initial_start_time is None or anchors or pagination_keys
    ):
        raise ValueError(f"invalid X runtime state for {context}: uninitialized state needs identity and start time")
    if state == "checkpointed" and (
        user_id is None or initial_start_time is not None or len(anchors) != 1 or pagination_keys
    ):
        raise ValueError(f"invalid X runtime state for {context}: checkpointed state needs one committed anchor")
    if state == "draining":
        if user_id is None or initial_start_time is not None:
            raise ValueError(f"invalid X runtime state for {context}: draining state needs resolved identity")
        if not {"x_pagination_token", "x_pending_since_id"} <= runtime.keys():
            raise ValueError(f"invalid X runtime state for {context}: draining state needs cursor and high-water")
        if "x_since_id" in runtime:
            if "x_pending_start_time" in runtime or "x_since_time" in runtime:
                raise ValueError(f"invalid X runtime state for {context}: ID pagination has conflicting anchors")
            if int(runtime["x_pending_since_id"]) <= int(runtime["x_since_id"]):
                raise ValueError(f"invalid X runtime state for {context}: pending high-water must advance")
        else:
            time_anchors = {"x_since_time", "x_pending_start_time"} & runtime.keys()
            if len(time_anchors) != 1:
                raise ValueError(f"invalid X runtime state for {context}: time pagination needs one start boundary")
    if reference_status == "pending" and state not in {"identity_pending", "uninitialized"}:
        raise ValueError(f"invalid X runtime state for {context}: pending reference has completed timeline state")
    if reference_status == "verified" and state not in {"checkpointed", "draining"}:
        raise ValueError(f"invalid X runtime state for {context}: verified reference has incomplete timeline state")
    return runtime
