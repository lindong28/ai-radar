from __future__ import annotations

import pytest

from airadar.admin.performance import (
    PerformanceStatusError,
    collect_performance_status,
    prepare_performance_panel,
)


def _row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "project": "ai-radar",
        "journey": "homepage.first_card",
        "vantage": "same_host_public",
        "load_class": "idle",
        "metric": "duration",
        "unit": "ms",
        "display_unit": "s",
        "window": {
            "cadence_seconds": 3600,
            "window_seconds": 86400,
            "minimum_samples": 6,
            "percentile_estimator": "nearest-rank",
        },
        "latest": {"value": 10.0, "observed_at": "2026-07-16T00:00:00Z"},
        "p75": 10.0,
        "p95": 11.0,
        "budget": {"p75_ms": 2000, "p95_ms": 3000},
        "baseline_qualification": "missing",
        "provisional": True,
        "freshness": "not_observed",
        "state": "insufficient_data",
        "raw_state": None,
        "is_green": False,
        "streak": 0,
        "outcome_identity": None,
        "stream_identity": "a" * 64,
        "config_hash": "b" * 64,
    }
    return {**base, **overrides}


def test_admin_panel_consumes_shared_status_without_re_evaluating() -> None:
    status = {
        "schema_version": 1,
        "project_id": "ai-radar",
        "config_hash": "b" * 64,
        "remediation": "disabled",
        "provider_invocations": 0,
        "adapter_invocations": 1,
        "open_incidents": 0,
        "incidents": [],
        "streams": [_row()],
        "completeness": {"complete": True, "expected": 1, "actual": 1, "missing": [], "extra": []},
        "outbox_pending": 0,
        "store_failures": 0,
        "storage": {"pressure": False, "actions": []},
        "regional": {"east_asia": "not_observed", "us": "not_observed", "europe": "not_observed"},
    }

    panel = prepare_performance_panel(status)

    assert panel["rows"] == status["streams"]
    assert panel["provider_invocations"] == 0
    assert panel["overall_green"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        lambda status: status["completeness"].update(complete=False, missing=["x"]),
        lambda status: status["streams"].append(dict(status["streams"][0])),
        lambda status: status["streams"][0].update(unit="seconds"),
        lambda status: status.update(provider_invocations=1),
        lambda status: status.update(remediation="enabled"),
    ],
)
def test_admin_panel_fails_closed_on_completeness_schema_or_provider_drift(mutation) -> None:
    status = {
        "schema_version": 1,
        "project_id": "ai-radar",
        "config_hash": "b" * 64,
        "remediation": "disabled",
        "provider_invocations": 0,
        "adapter_invocations": 0,
        "open_incidents": 0,
        "incidents": [],
        "streams": [_row()],
        "completeness": {"complete": True, "expected": 1, "actual": 1, "missing": [], "extra": []},
        "outbox_pending": 0,
        "store_failures": 0,
        "storage": {"pressure": False, "actions": []},
        "regional": {"east_asia": "not_observed", "us": "not_observed", "europe": "not_observed"},
    }
    mutation(status)

    with pytest.raises(PerformanceStatusError):
        prepare_performance_panel(status)


def test_admin_panel_rejects_shared_green_that_is_not_effectively_green() -> None:
    status = {
        "project_id": "ai-radar",
        "config_hash": "b" * 64,
        "remediation": "disabled",
        "provider_invocations": 0,
        "open_incidents": 0,
        "incidents": [],
        "streams": [_row(state="not_observed", freshness="not_observed", is_green=True)],
        "completeness": {"complete": True},
    }

    with pytest.raises(PerformanceStatusError, match="effective green"):
        prepare_performance_panel(status)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda status: status.pop("open_incidents"),
        lambda status: status.update(open_incidents="0"),
        lambda status: status["streams"][0].update(latest=None),
        lambda status: status["streams"][0].update(latest={"value": float("nan"), "observed_at": "x"}),
        lambda status: status["streams"][0].update(p75=None),
        lambda status: status["streams"][0].update(p95=float("inf")),
    ],
)
def test_admin_panel_requires_typed_incidents_latest_and_finite_budget_statistics(mutation) -> None:
    status = {
        "project_id": "ai-radar",
        "config_hash": "b" * 64,
        "remediation": "disabled",
        "provider_invocations": 0,
        "open_incidents": 0,
        "incidents": [],
        "streams": [_row()],
        "completeness": {"complete": True},
    }
    mutation(status)

    with pytest.raises(PerformanceStatusError):
        prepare_performance_panel(status)


@pytest.mark.parametrize(
    "budget",
    [
        {},
        {"p75_ms": 2000},
        {"p75_ms": 9999, "p95_ms": 3000},
        {"p75_ms": 2000, "p95_ms": 9999},
    ],
)
def test_admin_panel_rejects_budget_not_exactly_bound_to_tracked_config(
    budget: dict[str, int]
) -> None:
    status = {
        "project_id": "ai-radar",
        "config_hash": "b" * 64,
        "remediation": "disabled",
        "provider_invocations": 0,
        "open_incidents": 0,
        "incidents": [],
        "streams": [_row(budget=budget)],
        "completeness": {"complete": True},
    }

    with pytest.raises(PerformanceStatusError, match="budget"):
        prepare_performance_panel(status)


@pytest.mark.parametrize("error", [KeyError(501), ValueError("unknown uid")])
def test_collect_performance_status_contains_user_lookup_failures(monkeypatch, error: Exception) -> None:  # noqa: ANN001
    def fail_lookup(uid: int) -> None:
        raise error

    monkeypatch.setattr("airadar.admin.performance.pwd.getpwuid", fail_lookup)

    status = collect_performance_status()

    assert status["completeness"] is False
    assert str(error) in status["error"]
