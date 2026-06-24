from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .. import db

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _parse_dt(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(SHANGHAI_TZ)


def _window(days: int, now: datetime | None) -> tuple[datetime, datetime]:
    current = now or datetime.now(SHANGHAI_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SHANGHAI_TZ)
    current = current.astimezone(SHANGHAI_TZ)
    end = current.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return end - timedelta(days=days), end


def _utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _int(value: object) -> int:
    if value in (None, ""):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _float(value: object) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def _round_cost(value: object) -> float:
    return round(_float(value), 8)


def _date_range_desc(start: datetime, end: datetime) -> list[str]:
    days: list[str] = []
    cursor = end.date() - timedelta(days=1)
    first = start.date()
    while cursor >= first:
        days.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    return days


def _safe_json(value: object) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _empty_totals() -> dict[str, int | float]:
    return {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "input_items": 0,
        "input_chars": 0,
        "cost_usd": 0.0,
    }


def collect_usage(
    *,
    db_path: str | Path | None = None,
    days: int = 30,
    now: datetime | None = None,
) -> dict[str, object]:
    start, end = _window(days, now)
    day_order = _date_range_desc(start, end)
    days_by_date: dict[str, dict[str, Any]] = {
        day: {"date": day, "models": [], "totals": _empty_totals()} for day in day_order
    }
    model_maps: dict[str, dict[str, dict[str, Any]]] = {day: {} for day in day_order}

    with db.get_conn(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
              u.id, u.stage, u.provider, u.model, u.item_id, u.input_tokens,
              u.output_tokens, u.total_tokens, u.input_item_count, u.input_char_count,
              u.cost_usd, u.attribution_json, u.created_at,
              i.title AS item_title, s.name AS source_name
            FROM llm_usage u
            LEFT JOIN items i ON i.id = u.item_id
            LEFT JOIN sources s ON s.id = i.source_id
            WHERE u.created_at >= ? AND u.created_at < ?
            ORDER BY u.created_at DESC, u.id DESC
            """,
            (_utc_iso(start), _utc_iso(end)),
        ).fetchall()

    for row in rows:
        created = _parse_dt(row["created_at"])
        if created is None:
            continue
        day_key = created.date().isoformat()
        if day_key not in days_by_date:
            continue
        day_entry = days_by_date[day_key]
        model_key = str(row["model"] or "unknown")
        model_map = model_maps[day_key]
        model_entry = model_map.setdefault(
            model_key,
            {
                "model": model_key,
                "providers": set(),
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "input_items": 0,
                "input_chars": 0,
                "cost_usd": 0.0,
                "_stages": {},
            },
        )
        provider = str(row["provider"] or "unknown")
        input_tokens = _int(row["input_tokens"])
        output_tokens = _int(row["output_tokens"])
        total_tokens = _int(row["total_tokens"])
        input_items = _int(row["input_item_count"])
        input_chars = _int(row["input_char_count"])
        cost_usd = _float(row["cost_usd"])

        for target in (day_entry["totals"], model_entry):
            target["calls"] += 1
            target["input_tokens"] += input_tokens
            target["output_tokens"] += output_tokens
            target["total_tokens"] += total_tokens
            target["input_items"] += input_items
            target["input_chars"] += input_chars
            target["cost_usd"] = _round_cost(target["cost_usd"]) + cost_usd
            target["cost_usd"] = _round_cost(target["cost_usd"])
        model_entry["providers"].add(provider)

        stage_key = str(row["stage"] or "unknown")
        stages = model_entry["_stages"]
        stage_entry = stages.setdefault(
            stage_key,
            {
                "stage": stage_key,
                "calls": 0,
                "input_items": 0,
                "input_chars": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "examples": [],
            },
        )
        stage_entry["calls"] += 1
        stage_entry["input_items"] += input_items
        stage_entry["input_chars"] += input_chars
        stage_entry["input_tokens"] += input_tokens
        stage_entry["output_tokens"] += output_tokens

        examples = stage_entry["examples"]
        if len(examples) < 5:
            attribution = _safe_json(row["attribution_json"])
            examples.append(
                {
                    "item_id": row["item_id"],
                    "title": row["item_title"] or attribution.get("title") or row["item_id"] or "unknown input",
                    "source_name": row["source_name"] or attribution.get("source_id") or "",
                    "created_at": created.isoformat(),
                }
            )

    for day in day_order:
        models = []
        for model_entry in model_maps[day].values():
            stage_values = list(model_entry.pop("_stages").values())
            stage_values.sort(key=lambda entry: str(entry["stage"]))
            model_entry["providers"] = sorted(model_entry["providers"])
            model_entry["stages"] = stage_values
            models.append(model_entry)
        models.sort(key=lambda entry: (-int(entry["calls"]), str(entry["model"])))
        days_by_date[day]["models"] = models

    totals = _empty_totals()
    active_days = 0
    for day in day_order:
        day_totals = days_by_date[day]["totals"]
        if day_totals["calls"]:
            active_days += 1
        for key in totals:
            if key == "cost_usd":
                totals[key] = _round_cost(totals[key]) + _round_cost(day_totals[key])
                totals[key] = _round_cost(totals[key])
            else:
                totals[key] = _int(totals[key]) + _int(day_totals[key])

    return {
        "generated_at": (now or datetime.now(SHANGHAI_TZ)).astimezone(SHANGHAI_TZ).isoformat(),
        "timezone": "Asia/Shanghai",
        "window": {
            "start_date": start.date().isoformat(),
            "end_date": (end.date() - timedelta(days=1)).isoformat(),
            "days": days,
        },
        "totals": totals,
        "active_days": active_days,
        "daily": [days_by_date[day] for day in day_order],
    }
