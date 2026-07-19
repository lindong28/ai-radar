from __future__ import annotations

import hashlib
import json
import os
import pwd
import sqlite3
import subprocess
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .. import db, runtime_env
from .browser_probe import browser_runtime_available, measure_browser_journey
from .context import collect_probe_context
from .http_probe import measure_http_component
from .stage_ledger import STAGE_REGISTRY, StageLedger, classify_interval


@dataclass(frozen=True, slots=True)
class ProbeRuntime:
    origin_url: str
    public_url: str
    stage_ledger_root: Path
    browser_lock_path: Path
    db_path: Path


def canonical_probe_runtime() -> ProbeRuntime:
    runtime_env.load_runtime_env()
    state_root = Path(pwd.getpwuid(os.getuid()).pw_dir) / ".local/state/continuous-performance/projects/ai-radar"
    return ProbeRuntime(
        origin_url="http://127.0.0.1:8000",
        public_url=os.environ.get("AI_RADAR_PUBLIC_URL", ""),
        stage_ledger_root=state_root / "stage-ledger",
        browser_lock_path=state_root / "browser.lock",
        db_path=db.resolve_db_path(),
    )

ENV_FIELDS = (
    "CONTINUOUS_PERFORMANCE_PROJECT",
    "CONTINUOUS_PERFORMANCE_JOURNEY",
    "CONTINUOUS_PERFORMANCE_VANTAGE",
    "CONTINUOUS_PERFORMANCE_METRIC",
    "CONTINUOUS_PERFORMANCE_ALLOWED_LOAD_CLASSES",
    "CONTINUOUS_PERFORMANCE_CONFIG_HASH",
    "CONTINUOUS_PERFORMANCE_SCHEDULED_SLOT",
    "CONTINUOUS_PERFORMANCE_OBSERVED_AT",
)


class ProbeInfrastructureError(RuntimeError):
    pass


def stage_registry_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "rows": [
            {"canonical_stage": row.canonical_stage, "entrypoint": row.entrypoint, "kind": row.kind}
            for row in STAGE_REGISTRY
        ],
    }


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["/usr/bin/git", "rev-parse", "HEAD"],
            cwd=db.PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def build_observation_payload(
    *,
    environ: Mapping[str, str],
    executable: Path,
    measurement_value_ms: float,
    hard_failure: bool,
    load_class: str,
    context: dict[str, object],
) -> dict[str, object]:
    missing = [field for field in ENV_FIELDS if not environ.get(field)]
    if missing:
        raise ValueError(f"missing shared adapter environment: {missing}")
    allowed = json.loads(environ["CONTINUOUS_PERFORMANCE_ALLOWED_LOAD_CLASSES"])
    if load_class not in allowed:
        raise ValueError("authoritative load class is not allowed")
    identity = "\0".join(
        environ[field]
        for field in (
            "CONTINUOUS_PERFORMANCE_PROJECT",
            "CONTINUOUS_PERFORMANCE_JOURNEY",
            "CONTINUOUS_PERFORMANCE_VANTAGE",
            "CONTINUOUS_PERFORMANCE_METRIC",
            "CONTINUOUS_PERFORMANCE_SCHEDULED_SLOT",
            "CONTINUOUS_PERFORMANCE_CONFIG_HASH",
        )
    )
    event_id = "probe-" + hashlib.sha256(identity.encode()).hexdigest()[:32]
    observation = {
        "schema_version": 1,
        "event_id": event_id,
        "project_id": environ["CONTINUOUS_PERFORMANCE_PROJECT"],
        "journey": environ["CONTINUOUS_PERFORMANCE_JOURNEY"],
        "vantage": environ["CONTINUOUS_PERFORMANCE_VANTAGE"],
        "metric": environ["CONTINUOUS_PERFORMANCE_METRIC"],
        "unit": "ms",
        "load_class": load_class,
        "value": measurement_value_ms,
        "hard_failure": hard_failure,
        "scheduled_slot": environ["CONTINUOUS_PERFORMANCE_SCHEDULED_SLOT"],
        "observed_at": environ["CONTINUOUS_PERFORMANCE_OBSERVED_AT"],
        "config_hash": environ["CONTINUOUS_PERFORMANCE_CONFIG_HASH"],
        "git_sha": _git_sha(),
        "context": context,
    }
    return {
        "schema_version": 1,
        "capability": "observation.v1",
        "executable": {
            "path": str(executable.resolve()),
            "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        },
        "invocations": {"adapter": 1, "provider": 0},
        "observations": [observation],
    }


def _journey_config(journey_id: str) -> dict[str, object]:
    payload = tomllib.loads((db.PROJECT_ROOT / "config/performance.toml").read_text(encoding="utf-8"))
    return next(row for row in payload["journeys"] if row["id"] == journey_id)


def _detail_slug(db_path: Path) -> str:
    if not db_path.exists():
        return "unavailable"
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1)
        try:
            row = connection.execute(
                """
                SELECT wi.slug
                FROM wechat_interpretations wi
                JOIN items i ON i.id=wi.item_id
                WHERE wi.save_decision=1
                ORDER BY i.published_at DESC, i.fetched_at DESC, i.id DESC
                LIMIT 1
                """
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return "unavailable"
    return str(row[0]) if row is not None else "unavailable"


def _probe_expectation(db_path: Path, target: str, detail_slug: str) -> dict[str, object] | None:
    if not db_path.exists():
        return None
    from ..web.app import _prepaint_items
    from ..web.routes import curated_archive
    from ..web.routes.wechat import get_wechat_detail, list_wechat_items

    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1)
        connection.row_factory = sqlite3.Row
        try:
            if target in {"wechat_list_http", "wechat_pagination_http", "wechat_list", "wechat_pagination"}:
                page = 2 if "pagination" in target else 1
                payload = list_wechat_items(connection, page=page, limit=50)
                items = payload["items"]
                return {
                    "page": payload["page"],
                    "limit": 50,
                    "total": payload["total"],
                    "slugs": [str(item["slug"]) for item in items],
                }
            if target in {"wechat_detail_http", "wechat_detail"}:
                return {"item_id": get_wechat_detail(connection, detail_slug)["id"]}
            if target in {"homepage_http", "homepage"}:
                run = curated_archive._latest_run(connection)
                if run is None:
                    return {"item_ids": []}
                items, _total, _page = curated_archive._compute_archive_page(
                    connection, page=1, limit=40, normalized_category=None, q=None
                )
                shaped = _prepaint_items(items, timeline_page=False)
                return {"item_ids": [str(item["item_id"]) for item in shaped]}
        finally:
            connection.close()
    except (sqlite3.Error, KeyError):
        return None
    return None


def run_adapter(
    *, environ: Mapping[str, str], executable: Path, runtime: ProbeRuntime | None = None
) -> dict[str, object]:
    runtime = runtime or canonical_probe_runtime()
    journey = _journey_config(environ["CONTINUOUS_PERFORMANCE_JOURNEY"])
    target = str(journey["target"])
    raw_timeout = journey["hard_timeout_seconds"]
    if not isinstance(raw_timeout, int | float):
        raise ValueError("journey hard timeout is not numeric")
    timeout = float(raw_timeout)
    vantage = environ["CONTINUOUS_PERFORMANCE_VANTAGE"]
    base_url = runtime.origin_url if vantage == "same_host_origin" else runtime.public_url
    if not base_url:
        raise ProbeInfrastructureError(
            f"vantage_unconfigured: {vantage} has no base URL (set AI_RADAR_PUBLIC_URL)"
        )
    ledger = StageLedger(runtime.stage_ledger_root)
    detail_slug = _detail_slug(runtime.db_path)
    expectation = _probe_expectation(runtime.db_path, target, detail_slug)
    before = ledger.snapshot()
    if journey["probe"] == "quick":
        http_measurement = measure_http_component(
            base_url=base_url,
            target=target,
            detail_slug=detail_slug,
            timeout_seconds=timeout,
            expected=expectation,
        )
        measurement_value_ms = http_measurement.value_ms
        hard_failure = http_measurement.hard_failure
    else:
        browser_measurement = measure_browser_journey(
            base_url=base_url,
            target=target,
            detail_slug=detail_slug,
            timeout_seconds=timeout,
            lock_path=runtime.browser_lock_path,
            expected=expectation,
        )
        if browser_measurement.outcome != "observed":
            reason = browser_measurement.incompatible_reason or browser_measurement.outcome
            raise ProbeInfrastructureError(reason)
        measurement_value_ms = browser_measurement.value_ms
        hard_failure = browser_measurement.hard_failure
    after = ledger.snapshot()
    classification = classify_interval(before, after)
    context = collect_probe_context(
        ledger_before=before,
        ledger_after=after,
        db_path=runtime.db_path,
    )
    return build_observation_payload(
        environ=environ,
        executable=executable,
        measurement_value_ms=measurement_value_ms,
        hard_failure=hard_failure,
        load_class=classification.load_class,
        context=context,
    )


def main() -> None:
    if sys.argv[1:] == ["--doctor"]:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "ok" if browser_runtime_available() else "unavailable",
                    "browser_profile": "isolated_ephemeral",
                    "provider_invocations": 0,
                },
                sort_keys=True,
            )
        )
        return
    if sys.argv[1:]:
        raise SystemExit("usage: performance-adapter [--doctor]")
    executable = Path(__file__).parents[3] / "config/performance-adapter"
    try:
        payload = run_adapter(environ=os.environ, executable=executable)
    except ProbeInfrastructureError as error:
        print(
            json.dumps(
                {
                    "status": "incompatible"
                    if str(error).startswith(("browser_runtime", "vantage_unconfigured"))
                    else "skipped_overlap",
                    "reason": str(error),
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        raise SystemExit(78) from error
    print(json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    main()
