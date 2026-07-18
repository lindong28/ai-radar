from __future__ import annotations

import hashlib
import json
import math
import os
import pwd
import subprocess
import tomllib
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any

from .. import db


class PerformanceStatusError(ValueError):
    pass


def prepare_performance_panel(
    status: dict[str, Any],
    *,
    expected_config_hash: str | None = None,
    expected_tuples: Collection[tuple[str, str, str, str]] | None = None,
    expected_budgets: Mapping[tuple[str, str, str, str], Mapping[str, float | None]] | None = None,
) -> dict[str, Any]:
    if expected_budgets is None:
        _hash, _tuples, expected_budgets = tracked_expected_inventory(
            db.PROJECT_ROOT / "config/performance.toml"
        )
    if status.get("project_id") != "ai-radar":
        raise PerformanceStatusError("unexpected performance project")
    if status.get("provider_invocations") != 0:
        raise PerformanceStatusError("provider invocation evidence is nonzero")
    if status.get("remediation") != "disabled":
        raise PerformanceStatusError("remediation is not disabled")
    open_incidents = status.get("open_incidents")
    if isinstance(open_incidents, bool) or not isinstance(open_incidents, int) or open_incidents < 0:
        raise PerformanceStatusError("open_incidents is missing or invalid")
    if expected_config_hash is not None and status.get("config_hash") != expected_config_hash:
        raise PerformanceStatusError("registered config hash does not match tracked config")
    completeness = status.get("completeness")
    if not isinstance(completeness, dict) or completeness.get("complete") is not True:
        raise PerformanceStatusError("expected tuple inventory is incomplete")
    streams = status.get("streams")
    if not isinstance(streams, list):
        raise PerformanceStatusError("streams must be an array")
    identities: set[tuple[object, ...]] = set()
    required_fields = {
        "journey",
        "vantage",
        "load_class",
        "metric",
        "unit",
        "display_unit",
        "window",
        "budget",
        "baseline_qualification",
        "provisional",
        "freshness",
        "state",
        "raw_state",
        "is_green",
    }
    for row in streams:
        if not isinstance(row, dict) or not required_fields <= set(row) or row.get("unit") != "ms":
            raise PerformanceStatusError("stream schema or unit is invalid")
        identity = tuple(row.get(field) for field in ("journey", "vantage", "load_class", "metric"))
        if None in identity or identity in identities:
            raise PerformanceStatusError("stream identity is missing or duplicated")
        identities.add(identity)
        if not isinstance(row.get("is_green"), bool):
            raise PerformanceStatusError("shared is_green is missing")
        latest = row.get("latest")
        if (
            not isinstance(latest, dict)
            or not isinstance(latest.get("observed_at"), str)
            or not latest["observed_at"]
            or not _finite_number(latest.get("value"))
        ):
            raise PerformanceStatusError("latest observation is missing or invalid")
        budget = row.get("budget")
        expected_budget = expected_budgets.get(identity)  # type: ignore[arg-type]
        if (
            expected_budget is None
            or not isinstance(budget, dict)
            or set(budget) != {"p75_ms", "p95_ms"}
            or budget != expected_budget
        ):
            raise PerformanceStatusError("budget differs from tracked config authority")
        for percentile in ("p75", "p95"):
            budget_key = f"{percentile}_ms"
            if expected_budget[budget_key] is not None and (
                not _finite_number(expected_budget[budget_key])
                or not _finite_number(row.get(percentile))
            ):
                raise PerformanceStatusError(f"{percentile} statistic is missing or invalid")
        if row["is_green"] and not (
            row.get("state") in {"healthy", "resolved"}
            and row.get("raw_state") in {"healthy", "resolved"}
            and row.get("freshness") == "fresh"
            and row.get("baseline_qualification") == "promoted"
            and row.get("provisional") is False
        ):
            raise PerformanceStatusError("shared green violates effective green invariants")
    if expected_tuples is not None and identities != set(expected_tuples):
        raise PerformanceStatusError("shared rows differ from tracked expected tuple inventory")
    return {
        "rows": streams,
        "provider_invocations": 0,
        "overall_green": (
            bool(streams) and open_incidents == 0 and all(bool(row["is_green"]) for row in streams)
        ),
        "completeness": True,
        "error": None,
        "open_incidents": open_incidents,
    }


def _finite_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(float(value))


def tracked_expected_inventory(
    path: Path,
) -> tuple[
    str,
    set[tuple[str, str, str, str]],
    dict[tuple[str, str, str, str], dict[str, float | None]],
]:
    raw = path.read_bytes()
    payload = tomllib.loads(raw.decode())
    expected: set[tuple[str, str, str, str]] = set()
    budgets: dict[tuple[str, str, str, str], dict[str, float | None]] = {}
    for journey in payload["journeys"]:
        for vantage in journey["vantages"]:
            for metric in vantage["metrics"]:
                for load_class in vantage["allowed_load_classes"]:
                    identity = (journey["id"], vantage["id"], load_class, metric["id"])
                    expected.add(identity)
                    budgets[identity] = {
                        "p75_ms": metric.get("p75_budget_ms"),
                        "p95_ms": metric.get("p95_budget_ms"),
                    }
    return hashlib.sha256(raw).hexdigest(), expected, budgets


def collect_performance_status(*, executable: Path | None = None) -> dict[str, Any]:
    try:
        executable = executable or (
            Path(pwd.getpwuid(os.getuid()).pw_dir) / ".local/bin/continuous-performance"
        )
        result = subprocess.run(
            [str(executable), "status", "--json", "--project", "ai-radar"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        config_hash, expected, budgets = tracked_expected_inventory(
            db.PROJECT_ROOT / "config/performance.toml"
        )
        return prepare_performance_panel(
            json.loads(result.stdout),
            expected_config_hash=config_hash,
            expected_tuples=expected,
            expected_budgets=budgets,
        )
    except (
        OSError,
        ValueError,
        KeyError,
        subprocess.SubprocessError,
        json.JSONDecodeError,
        PerformanceStatusError,
    ) as error:
        return {
            "rows": [],
            "provider_invocations": 0,
            "overall_green": False,
            "completeness": False,
            "error": str(error),
        }
