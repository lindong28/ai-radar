from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request

from ...presentation.summary import json_loads
from ...sources.contract import load_source_contract
from ...sources.x_state import X_RUNTIME_META_KEYS, validate_x_runtime_meta
from ...wechat_archive import ARCHIVE_SOURCE_ID
from ..envelope import ok
from .request_db import conn_from_request

router = APIRouter()
v2_router = APIRouter()
V1_SOURCE_KINDS = frozenset({"feed", "x", "wechat"})


def _valid_timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _retrieval_validation(value: str | None) -> dict[str, object]:
    meta = json_loads(value, {})
    labels = {
        "pending": "待首次验证",
        "verified": "已验证",
        "blocked": "验证受阻",
        "unknown": "状态未知",
        "not_evaluated": "无运行态验证记录",
    }
    base: dict[str, object] = {
        "status": "not_evaluated",
        "label": labels["not_evaluated"],
        "scope": "live_retrieval",
        "trigger": None,
        "validated_at": None,
        "attempted_at": None,
        "reason": None,
        "recovery": None,
    }
    if not isinstance(meta, dict) or meta.get("adapter") != "x_api":
        return base
    base["scope"] = "x_timeline_retrieval"
    try:
        runtime = validate_x_runtime_meta(meta, context="public source")
    except ValueError:
        base.update(
            status="unknown",
            label=labels["unknown"],
            reason="internal_state_missing_or_invalid",
            recovery="operator_repair_required_before_x_timeline_fetch",
        )
        return base
    status = str(runtime["x_reference_status"])
    base.update(status=status, label=labels[status])
    if status == "verified":
        base.update(
            trigger="next_successful_x_timeline_fetch",
            validated_at=runtime["x_reference_validated_at"],
        )
    elif status == "pending":
        base["trigger"] = (
            "next_successful_x_identity_lookup"
            if runtime["x_cursor_state"] == "identity_pending"
            else "next_successful_x_timeline_fetch"
        )
    else:
        base.update(
            attempted_at=runtime["x_reference_attempted_at"],
            reason=runtime["x_reference_reason"],
            recovery=runtime["x_reference_recovery"],
        )
    return base


def _legacy_public_meta(value: str | None) -> dict[str, object]:
    meta = json_loads(value, {})
    if not isinstance(meta, dict):
        return {}
    if meta.get("adapter") != "x_api":
        return {str(key): item for key, item in meta.items()}
    return {str(key): item for key, item in meta.items() if key not in X_RUNTIME_META_KEYS}


@router.get("/sources")
def sources(request: Request) -> dict[str, object]:
    with conn_from_request(request) as conn:
        rows = conn.execute(
            "SELECT * FROM sources WHERE id<>? ORDER BY tier, id",
            (ARCHIVE_SOURCE_ID,),
        ).fetchall()
    public_rows = []
    for row in rows:
        keys = row.keys()
        kind = row["kind"] if "kind" in keys else "feed"
        if kind not in V1_SOURCE_KINDS:
            continue
        public_url_override = row["public_url_override"] if "public_url_override" in keys else None
        public_rows.append(
            {
                "id": row["id"],
                "name": row["name"],
                "url": public_url_override or ("https://mp.weixin.qq.com/" if kind == "wechat" else row["url"]),
                "tier": row["tier"],
                "enabled": bool(row["enabled"]),
                "kind": kind,
                "homepage_url": row["homepage_url"] if "homepage_url" in keys else None,
                "icon_url": row["icon_url"] if "icon_url" in keys else None,
                "meta": _legacy_public_meta(row["meta_json"]),
                "synced_at": row["synced_at"],
            }
        )
    return ok({"sources": public_rows})


KIND_LABELS = {"feed": "订阅源", "web": "网页列表", "x": "X 账号", "wechat": "微信专用"}
TIER_LABELS = {"T1": "核心", "T1.5": "重点", "T2": "扩展"}


@v2_router.get("/sources")
def sources_v2(request: Request) -> dict[str, object]:
    with conn_from_request(request) as conn:
        rows = conn.execute(
            "SELECT * FROM sources WHERE enabled=1 AND id<>? ORDER BY tier, id",
            (ARCHIVE_SOURCE_ID,),
        ).fetchall()
    public_rows = []
    for row in rows:
        kind = row["kind"] if "kind" in row.keys() else "feed"
        public_rows.append(
            {
                "id": row["id"],
                "name": row["name"],
                "retrieval_entrypoint_url": None if kind == "wechat" else row["url"],
                "public_landing_url": row["public_url_override"] or row["homepage_url"],
                "tier": row["tier"],
                "tier_label": TIER_LABELS.get(row["tier"], row["tier"]),
                "configuration_status": "enabled",
                "kind": kind,
                "kind_label": KIND_LABELS.get(kind, kind),
                "icon_url": row["icon_url"] if "icon_url" in row.keys() else None,
                "retrieval_validation": _retrieval_validation(row["meta_json"]),
                "configuration_synced_at": row["synced_at"],
            }
        )
    contract = load_source_contract(Path(__file__).resolve().parents[4] / "tests/fixtures/aihot_sources.json")
    wx_contract_rows = [row for row in contract["sources"] if row["slug"] == "wx_mp2rss"]
    if len(wx_contract_rows) != 1:
        raise ValueError(
            "source contract must declare exactly one wx_mp2rss optional source; "
            f"found {len(wx_contract_rows)}"
        )
    wx_contract = wx_contract_rows[0]
    wx_required_env = wx_contract["required_env"]
    wx_enabled_and_loaded = any(row["id"] == wx_contract["slug"] for row in rows)
    wx_environment_configured = bool(os.environ.get(wx_required_env, "").strip())
    if not wx_environment_configured:
        wx_runtime_status = "unavailable_missing_required_environment"
    elif not wx_enabled_and_loaded:
        wx_runtime_status = "unavailable_not_loaded"
    else:
        wx_runtime_status = "configured"
    optional_sources = [
        {
            "id": wx_contract["slug"],
            "name": wx_contract["name"],
            "scope": "wechat_only" if wx_contract["wechat_only"] else "main",
            "required_environment_variable": wx_required_env,
            "declared_in_contract": True,
            "runtime_configuration_status": wx_runtime_status,
        }
    ]
    return ok(
        {
            "sources": public_rows,
            "optional_sources": optional_sources,
            "counts": {
                "declared_contract_source_count": len(contract["sources"]),
                "declared_main_timeline_source_count": sum(
                    source["ai_radar_main_timeline_member"] for source in contract["sources"]
                ),
                "enabled_loaded_source_count": len(rows),
                "enabled_loaded_main_timeline_source_count": sum(row["kind"] != "wechat" for row in rows),
            },
        }
    )
