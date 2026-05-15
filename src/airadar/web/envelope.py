from __future__ import annotations

from typing import Any


def ok(data: Any, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"success": True, "data": data, "error": None}
    if meta is not None:
        payload["meta"] = meta
    return payload


def error(message: str, data: Any = None) -> dict[str, Any]:
    return {"success": False, "data": data, "error": message}
