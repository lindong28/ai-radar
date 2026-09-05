from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from urllib.parse import quote
from uuid import UUID

from . import db, runtime_env
from .admin import edgeone
from .admin.alerts import (
    DEFAULT_EVENT_PATH,
    DEFAULT_SERVE_LAUNCH_AGENT_PATH,
    DEFAULT_STATE_PATH,
    PAGE_SEVERITY,
    WECHAT_BROWSER_PREFLIGHT_RULE_ID,
    AlertRuleResult,
    AlertSender,
    collect_alert_signals,
    prepare_alert_source_pause,
    run_alert_results_state_machine,
    run_alert_state_machine,
    run_pricing_notifications,
    send_alert_message,
)
from .admin.cost_audit import run_cost_audit
from .admin.cost_report import build_cost_report, deliver_cost_report, format_cost_report
from .admin.metrics import SHANGHAI_TZ
from .admin.wechat_kb import import_catalog
from .admin.x_media_backfill import backfill_x_media
from .curator.precompute import (
    DEFAULT_KEEP_DAYS,
    RetentionStats,
    curated_summary_retention_stats,
    precompute_curated_summaries,
    retain_curated_summaries,
)
from .curator.score import ScoredCandidate
from .curator.select import (
    DEFAULT_SOURCE_QUOTA,
    SOURCE_QUOTA_BASELINE,
    SOURCE_QUOTA_POLICY,
    SOURCE_QUOTA_SCORE_SEMANTICS,
    SourceQuota,
    _calibrate_selected_scores,
    curate,
    parse_source_quota,
)
from .curator.weights import load_weights
from .egress import EgressPreflightError, require_selector_policy
from .enrich.runner import run_enrich
from .enrich.runner_v2 import run_enrich as run_enrich_v2
from .eval.aihot_fit.cli import add_eval_fit_parser, run_eval_fit
from .eval.judge import DEFAULT_AIHOT_MARKDOWN, DEFAULT_OUTPUT_DIR, run_eval
from .fetcher.runner import fetch_all, refresh_wechat_avatar, reload_sources
from .fetcher.wechat import (
    WeChatBrowserNotVerified,
    WeChatBrowserUnavailable,
    inspect_wechat_browser_executable,
)
from .interpret.runner import run_interpret
from .performance.journey_monitor import (
    DEFAULT_ALERT_STATE_PATH,
    DEFAULT_EVIDENCE_DIR,
    DEFAULT_SAMPLE_PATH,
    LAUNCHD_INSTALL_HINT,
    run_journey_monitor,
)
from .performance.remediation import (
    DEFAULT_REMEDIATION_EVIDENCE_DIR,
    DEFAULT_REMEDIATION_LOCK_PATH,
    DEFAULT_REMEDIATION_ROOT,
    DEFAULT_REMEDIATION_STATE_PATH,
    DEFAULT_TIMEOUT_SECONDS,
    REMEDIATION_CRONTAB_SAMPLE,
    RemediationConfig,
    remediate_confirmed_incident,
)
from .pipeline_lock import DEFAULT_PIPELINE_LOCK_PATH, pipeline_lock_is_held
from .prefilter.runner import run_prefilter
from .pricing import get_pricing
from .scorer.runner import run_scoring
from .wechat_discovery.config import DEFAULT_CONFIG_PATH, load_discovery_config
from .wechat_discovery.login import (
    DEFAULT_BROWSER_PROFILE,
    DiscoveryLoginError,
    capture_login,
)
from .wechat_discovery.models import (
    DiscoveryArticle,
    DiscoveryAttempt,
    DiscoveryGateState,
    DiscoveryState,
    IdentityResolutionState,
    TargetIdentityEvidence,
)
from .wechat_discovery.protocol import (
    DEFAULT_SESSION_PATH,
    DiscoveryAuthRequired,
    DiscoveryIdentityAmbiguous,
    DiscoveryIdentityMismatch,
    DiscoveryIdentityNoMatch,
    DiscoveryIdentityUnverified,
    DiscoveryPlatformRejected,
    DiscoveryRateLimited,
    DiscoveryRequestFailed,
    DiscoveryResponseInvalid,
    WeChatAdminClient,
    load_credentials,
    normalized_account_name,
    observed_article_biz,
    select_unique_searchbiz_candidate,
    verify_account_identity,
)
from .wechat_discovery.shadow import ShadowNotComparable, compare_shadow_window
from .wechat_discovery.status import backend_request_blocked_until, effective_status
from .wechat_discovery.store import (
    DEFAULT_STATE_DB_PATH,
    DiscoveryCooldownActive,
    DiscoveryStore,
    DiscoveryStoreVersionError,
)

_SOURCE_QUOTA_FROM_ENV = object()
_WECHAT_BROWSER_INSTALL_COMMAND = "uv run playwright install chromium"
_PIPELINE_RUN_RE = re.compile(
    r"^(?:\[[^\]]+\]\s+)?===\s+pipeline RUN generation=(?P<generation>[^\s]+)\s+===$"
)
_PIPELINE_CONTROL_RE = re.compile(
    r"^(?:\[[^\]]+\]\s+)?===\s+"
    r"(?P<stage>egress preflight|wechat_browser_preflight|fetch|prefilter|score|enrich|curate|"
    r"interpret|wechat_browser_preflight_resolve)\s+"
    r"(?P<event>START|OK|FAIL|DEGRADED)(?:\s+\([^)]*\))?\s+===$"
)
_PIPELINE_SUCCESS_CONTROL_SEQUENCE = (
    ("egress preflight", "START"),
    ("egress preflight", "OK"),
    ("wechat_browser_preflight", "START"),
    ("wechat_browser_preflight", "OK"),
    ("fetch", "START"),
    ("fetch", "OK"),
    ("prefilter", "START"),
    ("prefilter", "OK"),
    ("score", "START"),
    ("score", "OK"),
    ("enrich", "START"),
    ("enrich", "OK"),
    ("curate", "START"),
    ("curate", "OK"),
    ("interpret", "START"),
    ("interpret", "OK"),
    ("wechat_browser_preflight_resolve", "START"),
)


class PipelineSuccessNotVerified(RuntimeError):
    pass


def _verify_pipeline_success_evidence(
    pipeline_log: str | Path | None,
    *,
    pipeline_lock_path: str | Path,
    pipeline_lock_fd: int,
    pipeline_capability_fd: int,
) -> None:
    if pipeline_log is None:
        raise PipelineSuccessNotVerified("--pipeline-log is required")
    lock_path = Path(pipeline_lock_path)
    try:
        inherited_lock = os.fstat(pipeline_lock_fd)
        lock_file = lock_path.stat()
        inherited_capability = os.fstat(pipeline_capability_fd)
    except OSError as exc:
        raise PipelineSuccessNotVerified(
            "the inherited pipeline descriptors could not be verified"
        ) from exc
    if not (
        stat.S_ISREG(inherited_lock.st_mode)
        and stat.S_ISREG(lock_file.st_mode)
        and (inherited_lock.st_dev, inherited_lock.st_ino)
        == (lock_file.st_dev, lock_file.st_ino)
        and pipeline_lock_is_held(lock_path) is True
    ):
        raise PipelineSuccessNotVerified(
            "the command is not running inside the active pipeline process tree"
        )

    activity_path = lock_path.with_suffix(".activity")
    try:
        generation = activity_path.read_text(encoding="utf-8").strip()
        parsed_generation = UUID(generation)
    except (OSError, ValueError) as exc:
        raise PipelineSuccessNotVerified("the pipeline activity generation is unavailable") from exc
    if str(parsed_generation) != generation.lower():
        raise PipelineSuccessNotVerified("the pipeline activity generation is not canonical")
    try:
        capability_generation = os.pread(pipeline_capability_fd, 128, 0).decode("utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise PipelineSuccessNotVerified(
            "the inherited pipeline capability could not be verified"
        ) from exc
    if not (
        stat.S_ISREG(inherited_capability.st_mode)
        and inherited_capability.st_nlink == 0
        and capability_generation == generation
    ):
        raise PipelineSuccessNotVerified(
            "the command is not running inside the active pipeline process tree"
        )
    try:
        fcntl.flock(pipeline_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise PipelineSuccessNotVerified(
            "the command is not running inside the active pipeline process tree"
        ) from exc

    log_path = Path(pipeline_log)
    if re.fullmatch(r"pipeline-\d{8}-\d{6}\.log", log_path.name) is None:
        raise PipelineSuccessNotVerified("the pipeline log name is not recognized")
    if not log_path.is_file():
        raise PipelineSuccessNotVerified("the pipeline log is not a regular file")
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise PipelineSuccessNotVerified("the pipeline log could not be read") from exc
    log_generations: list[str] = []
    controls: list[tuple[str, str]] = []
    for raw_line in lines:
        line = raw_line.strip()
        if "=== PIPELINE DONE " in line or "=== pipeline SKIP:" in line:
            raise PipelineSuccessNotVerified(
                "the pipeline log is already complete or belongs to a skipped run"
            )
        if match := _PIPELINE_RUN_RE.match(line):
            log_generations.append(match.group("generation"))
        if match := _PIPELINE_CONTROL_RE.match(line):
            controls.append((match.group("stage"), match.group("event")))
    if log_generations != [generation]:
        raise PipelineSuccessNotVerified(
            "the pipeline log generation does not uniquely match the active run"
        )
    if tuple(controls) != _PIPELINE_SUCCESS_CONTROL_SEQUENCE:
        raise PipelineSuccessNotVerified(
            "the pipeline control events are missing, duplicated, failed, degraded, or out of order"
        )


def _resolve_source_quota(value: object) -> SourceQuota | None:
    if value is not _SOURCE_QUOTA_FROM_ENV:
        if value is None or isinstance(value, SourceQuota):
            return value
        raise TypeError("source quota argument has an unsupported value")
    source_quota_text = os.environ.get("AI_RADAR_CURATE_SOURCE_QUOTA")
    if source_quota_text is None or not source_quota_text.strip():
        return DEFAULT_SOURCE_QUOTA
    return parse_source_quota(source_quota_text)


_load_runtime_env = runtime_env.load_runtime_env


def _load_item_ids(path: str) -> list[str]:
    payload_path = Path(path)
    if payload_path.suffix.lower() == ".json":
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        raw_ids = payload.get("item_ids", payload) if isinstance(payload, dict) else payload
        return [str(item_id) for item_id in raw_ids]
    return [
        line.strip()
        for line in payload_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _not_implemented(name: str) -> int:
    print(f"{name}: not implemented")
    return 0


def _egress_preflight() -> int:
    try:
        policy = require_selector_policy()
    except EgressPreflightError as exc:
        print(f"egress-preflight status=unavailable reason={exc}")
        print("Impact: no managed external pipeline stage was started")
        print("Next: restore a healthy domain-routing selector, then retry")
        return 1
    print(f"egress-preflight status=healthy policy_id={policy.policy_id} policy_sha256={policy.policy_sha256}")
    return 0


def _wechat_browser_alert_result(
    *, status: str, reason: str, firing: bool, after_pipeline: bool = False
) -> AlertRuleResult:
    if firing:
        detail = (
            "Playwright 无法确认预期 Chromium 可执行文件"
            if status == "not_verified"
            else "Playwright 预期 Chromium 可执行文件缺失或不可执行"
        )
        action = (
            "现在查看 `./run.sh wechat-browser-preflight` 的 Details（scheduled 路径见同轮 "
            "`logs/pipeline-*.log`），修复 Playwright driver/runtime 错误后重试；"
            "不要在状态未核实时把重新安装浏览器当成已证实修复。"
            if status == "not_verified"
            else (
                f"现在到 AI Radar 仓库运行 `{_WECHAT_BROWSER_INSTALL_COMMAND}`，再运行 "
                "`./run.sh wechat-browser-preflight`；若仍失败，查看同轮 `logs/pipeline-*.log`。"
            )
        )
        return AlertRuleResult(
            rule_id=WECHAT_BROWSER_PREFLIGHT_RULE_ID,
            title="微信全文浏览器依赖不可用",
            firing=True,
            detail=detail,
            action=action,
            values={"status": status, "reason": reason},
            severity=PAGE_SEVERITY,
            impact=(
                "本轮数据 pipeline 已完成；下一轮在 Chromium 恢复前将在 fetch 前停止"
                if after_pipeline
                else "scheduled pipeline 在 fetch 前停止；本轮 RSS/X 抓取与后续处理不会启动"
            ),
            urgency="是——需立即恢复 Chromium",
        )
    return AlertRuleResult(
        rule_id=WECHAT_BROWSER_PREFLIGHT_RULE_ID,
        title="微信全文浏览器依赖不可用",
        firing=False,
        detail=(
            "仅关闭 executable-path incident：Chromium 预期可执行文件存在且整轮 "
            "pipeline 已成功完成；未验证 Chromium launch、网络或微信全文抓取"
        ),
        action="无需处置",
        values={"status": status, "reason": reason},
        severity=PAGE_SEVERITY,
    )


def _run_wechat_browser_alert_transition(
    result: AlertRuleResult,
    *,
    state_path: str | Path,
    event_path: str | Path,
    send: AlertSender | None,
) -> dict[str, object]:
    return run_alert_results_state_machine(
        [result],
        state_path=state_path,
        event_path=event_path,
        send=send,
    )


def _print_wechat_browser_alert_receipt(transition: dict[str, object]) -> None:
    sent = transition.get("sent")
    receipt = sent[0] if isinstance(sent, list) and sent else None
    if isinstance(receipt, dict) and receipt.get("delivered") is True:
        print("Alert: accepted rule=W1 severity=page")
    elif isinstance(receipt, dict):
        print("Alert: degraded rule=W1 severity=page; notification was not accepted and will retry")
    else:
        print("Alert: deduplicated rule=W1 severity=page; existing incident remains active")


def _wechat_browser_preflight(
    *,
    state_path: str | Path = DEFAULT_STATE_PATH,
    event_path: str | Path = DEFAULT_EVENT_PATH,
    resolve_after_pipeline: bool = False,
    pipeline_log: str | Path | None = None,
    pipeline_lock_path: str | Path = DEFAULT_PIPELINE_LOCK_PATH,
    pipeline_lock_fd: int = 9,
    pipeline_capability_fd: int = 8,
    send: AlertSender | None = None,
) -> int:
    if resolve_after_pipeline:
        try:
            _verify_pipeline_success_evidence(
                pipeline_log,
                pipeline_lock_path=pipeline_lock_path,
                pipeline_lock_fd=pipeline_lock_fd,
                pipeline_capability_fd=pipeline_capability_fd,
            )
        except PipelineSuccessNotVerified as exc:
            print("WeChat browser recovery: NOT VERIFIED — W1 remains open")
            print(f"Details: pipeline success evidence rejected: {exc}")
            print("Impact: recovery cannot be claimed; the next scheduled run will preflight again")
            print(
                "Action: do not repair W1 state manually; resolve is accepted only from the active "
                "pipeline after every data stage succeeds"
            )
            return 2
        try:
            executable = inspect_wechat_browser_executable()
        except WeChatBrowserUnavailable as exc:
            status = "unavailable"
            reason = str(exc)
            exit_code = 1
        except WeChatBrowserNotVerified as exc:
            status = "not_verified"
            reason = str(exc)
            exit_code = 2
        else:
            status = "present"
            reason = f"expected executable present: {executable}"
            exit_code = 0
        if exit_code:
            print(
                f"WeChat browser recovery: {status.replace('_', ' ').upper()} — W1 remains open; "
                "the data pipeline already succeeded"
            )
            print(f"Details: status={status} reason={reason}")
            print("Impact: the next scheduled pipeline will stop before fetch unless this check passes")
            if status == "unavailable":
                print(
                    f"Action now: run {_WECHAT_BROWSER_INSTALL_COMMAND}, then retry "
                    "./run.sh wechat-browser-preflight"
                )
            else:
                print(
                    "Action now: inspect and repair the Playwright driver/runtime error above, then retry "
                    "./run.sh wechat-browser-preflight"
                )
            try:
                transition = _run_wechat_browser_alert_transition(
                    _wechat_browser_alert_result(
                        status=status,
                        reason=reason,
                        firing=True,
                        after_pipeline=True,
                    ),
                    state_path=state_path,
                    event_path=event_path,
                    send=send,
                )
            except Exception as exc:  # noqa: BLE001 - detection result still controls the exit.
                print(f"Alert: degraded; state/notification failed: {type(exc).__name__}: {exc}")
                return exit_code
            _print_wechat_browser_alert_receipt(transition)
            return exit_code
        try:
            transition = _run_wechat_browser_alert_transition(
                _wechat_browser_alert_result(status="present", reason=reason, firing=False),
                state_path=state_path,
                event_path=event_path,
                send=send,
            )
        except Exception as exc:  # noqa: BLE001 - recovery state must remain retryable.
            print(
                "WeChat browser recovery: DEGRADED — data pipeline succeeded; W1 remains open"
            )
            print(f"Details: state transition failed: {type(exc).__name__}: {exc}")
            print(
                "Scope: executable path is present; Chromium launch, network, and WeChat full-text "
                "fetch were not checked"
            )
            print(
                "Action: no manual W1 state repair is required; the transition will retry after "
                "the next fully successful pipeline; data integrity is outside this result"
            )
            return 2
        sent = transition.get("sent")
        receipt = sent[0] if isinstance(sent, list) and sent else None
        if isinstance(receipt, dict) and receipt.get("delivered") is not True:
            print(
                "WeChat browser recovery: DEGRADED — data pipeline succeeded; W1 remains open"
            )
            print("Evidence: expected executable is present and the full scheduled pipeline completed")
            print("Impact: recovery notification was not accepted; W1 remains open")
            print(
                "Scope: executable path is present; Chromium launch, network, and WeChat full-text "
                "fetch were not checked"
            )
            print(
                "Action: no manual W1 state repair is required; recovery delivery will retry "
                "automatically after the next fully successful pipeline; data integrity is outside this result"
            )
            return 2
        if isinstance(receipt, dict):
            print("WeChat browser recovery: RESOLVED — W1 executable-path incident closed")
            print("Evidence: expected executable is present and the full scheduled pipeline completed")
            print(
                "Scope: Chromium launch, network, and WeChat full-text fetch were not "
                "independently checked"
            )
            print(
                "Alert: recovery accepted rule=W1 "
                f"severity={receipt.get('effective_severity', 'notice')}"
            )
        else:
            print("WeChat browser recovery: NOT NEEDED — no announced W1 firing episode")
            print("Evidence: expected executable is present and the full scheduled pipeline completed")
            print(
                "Scope: Chromium launch, network, and WeChat full-text fetch were not "
                "independently checked"
            )
            print("Alert: not sent")
        print("Action: none")
        return 0

    try:
        executable = inspect_wechat_browser_executable()
    except WeChatBrowserUnavailable as exc:
        status = "unavailable"
        reason = str(exc)
        exit_code = 1
    except WeChatBrowserNotVerified as exc:
        status = "not_verified"
        reason = str(exc)
        exit_code = 2
    else:
        print("WeChat browser preflight: PRESENT — scheduled pipeline may continue")
        print(f"Details: status=present executable={executable}")
        print(
            "Scope: expected executable only; browser launch, network, and WeChat full-text fetch "
            "were not checked"
        )
        print("Action: none")
        print("Alert: not sent; recovery is evaluated only after a fully successful pipeline")
        return 0

    headline = "UNAVAILABLE" if status == "unavailable" else "NOT VERIFIED"
    print(f"WeChat browser preflight: {headline} — scheduled pipeline blocked before fetch")
    print(f"Details: status={status} reason={reason}")
    if status == "unavailable":
        print("Impact: scheduled pipeline will stop before fetch; no WeChat article will be downgraded to RSS-only")
        print(
            f"Action now: run {_WECHAT_BROWSER_INSTALL_COMMAND}, then retry "
            "./run.sh wechat-browser-preflight"
        )
    else:
        print("Impact: browser availability was not determined; scheduled pipeline stopped before fetch")
        print(
            "Action now: inspect and repair the Playwright driver/runtime error above, then retry "
            "./run.sh wechat-browser-preflight"
        )
    try:
        transition = _run_wechat_browser_alert_transition(
            _wechat_browser_alert_result(status=status, reason=reason, firing=True),
            state_path=state_path,
            event_path=event_path,
            send=send,
        )
    except Exception as exc:  # noqa: BLE001 - detection result still controls the pipeline exit.
        print(f"Alert: degraded; state/notification failed: {type(exc).__name__}: {exc}")
        return exit_code
    _print_wechat_browser_alert_receipt(transition)
    return exit_code


def _remediation_timeout(value: str) -> int:
    timeout = int(value)
    if not 0 < timeout <= DEFAULT_TIMEOUT_SECONDS:
        raise argparse.ArgumentTypeError(f"timeout must be between 1 and {DEFAULT_TIMEOUT_SECONDS} seconds")
    return timeout


def _non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a non-negative integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return parsed


def _positive_int(value: str) -> int:
    parsed = _non_negative_int(value)
    if parsed == 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


@dataclass(frozen=True)
class DbSlimResult:
    retained: bool
    compacted: bool
    cleared_rows: int
    reclaimed_file_bytes: int
    error: str | None = None


def _existing_db_path(path: str | Path | None) -> Path:
    db_path = db.resolve_db_path(path)
    if not db_path.is_file():
        raise FileNotFoundError(f"database does not exist: {db_path}")
    return db_path


def _connect_existing_db(db_path: Path, *, readonly: bool) -> sqlite3.Connection:
    mode = "ro" if readonly else "rw"
    conn = sqlite3.connect(f"{db_path.as_uri()}?mode={mode}", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    if readonly:
        conn.execute("PRAGMA query_only=ON")
    return conn


def _require_writable_db(db_path: Path) -> None:
    if not db_path.stat().st_mode & 0o222 or not os.access(db_path, os.W_OK):
        raise PermissionError(f"database is not writable: {db_path}")


def _has_vacuum_space(db_path: Path) -> bool:
    target = db_path.resolve(strict=True)
    return shutil.disk_usage(target.parent).free >= target.stat().st_size * 2


def _dry_run_retention_stats(db_path: Path, keep_days: int) -> RetentionStats:
    conn = _connect_existing_db(db_path, readonly=True)
    try:
        return curated_summary_retention_stats(conn, keep_days)
    finally:
        conn.close()


def _vacuum_database(conn: sqlite3.Connection) -> None:
    conn.execute("VACUUM")


def _slim_database(db_path: Path, keep_days: int) -> DbSlimResult:
    _require_writable_db(db_path)
    before_size = db_path.stat().st_size
    conn = _connect_existing_db(db_path, readonly=False)
    try:
        cleared_rows = retain_curated_summaries(conn, keep_days)
    except Exception:
        conn.close()
        raise

    error: str | None = None
    compacted = False
    try:
        if not _has_vacuum_space(db_path):
            error = "insufficient disk space for VACUUM"
        else:
            _vacuum_database(conn)
            compacted = True
    except Exception as exc:
        error = str(exc)
    finally:
        try:
            conn.close()
        except Exception as exc:
            compacted = False
            error = str(exc)

    if not compacted:
        return DbSlimResult(
            retained=True,
            compacted=False,
            cleared_rows=cleared_rows,
            reclaimed_file_bytes=0,
            error=error,
        )
    try:
        reclaimed = max(0, before_size - db_path.stat().st_size)
    except Exception as exc:
        return DbSlimResult(
            retained=True,
            compacted=False,
            cleared_rows=cleared_rows,
            reclaimed_file_bytes=0,
            error=str(exc),
        )
    return DbSlimResult(
        retained=True,
        compacted=True,
        cleared_rows=cleared_rows,
        reclaimed_file_bytes=reclaimed,
    )


def _admin_db_retention(args: argparse.Namespace) -> int:
    try:
        if args.db_path is not None and not str(args.db_path).strip():
            raise ValueError("database path must not be empty or blank")
        db_path = _existing_db_path(args.db_path)
        if args.dry_run:
            stats = _dry_run_retention_stats(db_path, args.keep_days)
            print(f"eligible_rows={stats.eligible_rows} logical_summary_bytes={stats.logical_summary_bytes}")
            return 0
        if args.db_command == "retain":
            _require_writable_db(db_path)
            with _connect_existing_db(db_path, readonly=False) as conn:
                cleared_rows = retain_curated_summaries(conn, args.keep_days)
            print(f"retained=true cleared_rows={cleared_rows}")
            return 0
        result = _slim_database(db_path, args.keep_days)
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"error={exc}", file=sys.stderr)
        return 2
    suffix = f" error={result.error}" if result.error else ""
    print(
        f"retained={str(result.retained).lower()} "
        f"compacted={str(result.compacted).lower()} "
        f"cleared_rows={result.cleared_rows} "
        f"reclaimed_file_bytes={result.reclaimed_file_bytes}{suffix}"
    )
    return 0 if result.compacted else 1


def _fetch(args: argparse.Namespace) -> int:
    summary = fetch_all(Path(args.sources) if args.sources else None)
    for source in summary.sources:
        if source.error:
            print(f"FAIL {source.source_id} {source.error}")
        else:
            print(f"OK {source.source_id} fetched={source.fetched} inserted={source.inserted}")
    print(f"=== attempted={summary.attempted} inserted={summary.inserted} failed={summary.failed}")
    return 0


def _prefilter(args: argparse.Namespace) -> int:
    db.migrate()
    with db.get_conn() as conn:
        item_ids = _load_item_ids(args.item_id_file) if args.item_id_file else None
        summary = run_prefilter(
            conn, since=args.since, limit=args.limit, ruleset_version=args.ruleset, item_ids=item_ids
        )
    print(f"prefilter processed={summary.processed} errors={summary.errors}")
    return 0


def _score(args: argparse.Namespace) -> int:
    db.migrate()
    with db.get_conn() as conn:
        summary = run_scoring(conn, since=args.since, limit=args.limit, ruleset_version=args.ruleset)
    print(f"score processed={summary.processed} errors={summary.errors}")
    return 0


def _enrich(args: argparse.Namespace) -> int:
    db.migrate()
    with db.get_conn() as conn:
        item_ids = None
        if args.item_id_file:
            item_ids = _load_item_ids(args.item_id_file)
        if args.curated_run:
            if item_ids is not None:
                raise ValueError("--curated-run and --item-id-file are mutually exclusive")
            if args.curated_run == "latest":
                run = conn.execute("SELECT id FROM curation_runs ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()
                if run is None:
                    print("enrich processed=0, errors=0")
                    return 0
                run_id = run["id"]
            else:
                run_id = args.curated_run
            rows = conn.execute(
                "SELECT item_id FROM curated_items WHERE run_id=? ORDER BY rank",
                (run_id,),
            ).fetchall()
            item_ids = [row["item_id"] for row in rows]
        started = monotonic()

        def progress(progress) -> None:  # noqa: ANN001
            elapsed = max(0.001, monotonic() - started)
            rate = progress.completed / elapsed
            status = "ERROR" if progress.error else "OK"
            print(
                f"enrich progress {progress.completed}/{progress.total} "
                f"errors={progress.errors} rate={rate:.2f}/s item={progress.item_id} "
                f"latency_ms={progress.latency_ms} {status}",
                flush=True,
            )

        workers = args.workers or int(os.environ.get("AI_RADAR_ENRICH_WORKERS", "1"))
        use_v2 = bool(getattr(args, "v2", False)) or os.environ.get("AI_RADAR_ENRICH_V2") == "1"
        if use_v2:
            # v2 pipeline pins its own r2 ruleset version internally (see
            # ruleset.current_version_v2); --ruleset/$AI_RADAR_ENRICH_RULESET
            # has no effect on this branch, matching the v1/v2 data isolation
            # design (item_evaluations.ruleset_version r1 vs r2).
            summary_v2 = run_enrich_v2(
                conn,
                since=args.since,
                limit=args.limit,
                item_ids=item_ids,
                workers=workers,
                progress_callback=progress,
            )
            processed, errors = summary_v2.processed, summary_v2.errors
        else:
            summary_v1 = run_enrich(
                conn,
                since=args.since,
                limit=args.limit,
                ruleset_version=args.ruleset,
                item_ids=item_ids,
                workers=workers,
                progress_callback=progress,
            )
            processed, errors = summary_v1.processed, summary_v1.errors
    print(f"enrich processed={processed}, errors={errors}")
    return 0


def _curate(args: argparse.Namespace) -> int:
    try:
        source_quota = _resolve_source_quota(args.source_quota)
    except (TypeError, ValueError) as exc:
        print(
            f"curate blocked: AI_RADAR_CURATE_SOURCE_QUOTA is invalid ({exc}); "
            "expected e.g. x=0.20,source=0.075 or off; curate did not run - "
            "fix or unset the variable, then rerun",
            file=sys.stderr,
        )
        return 2
    db.migrate()
    selected_weights = load_weights(Path(args.weights)) if getattr(args, "weights", None) else None
    ruleset = getattr(args, "ruleset", None)
    suffix = getattr(args, "ruleset_suffix", None)
    if suffix:
        ruleset = f"{ruleset or 'manual'}.{suffix}"
    with db.get_conn() as conn:
        run = curate(
            conn,
            ruleset_version=ruleset,
            weights=selected_weights,
            threshold=args.threshold,
            limit=args.limit,
            freshness_quota=args.freshness_quota,
            freshness_floor=args.freshness_floor,
            source_quota=source_quota,
        )
        precompute_curated_summaries(conn, run.id)
        retain_curated_summaries(conn, DEFAULT_KEEP_DAYS)
    print(f"curate run_id={run.id} selected={len(run.output_curated_ids)} threshold={run.threshold}")
    return 0


def _validate_source_quota_shadow(shadow: dict[str, object]) -> None:
    """Reject anything that is not the complete, frozen source-quota-v1 run shape."""
    if shadow.get("baseline") != SOURCE_QUOTA_BASELINE:
        raise ValueError(f"shadow_json.baseline is {shadow.get('baseline')!r}")
    if shadow.get("score_semantics") != SOURCE_QUOTA_SCORE_SEMANTICS:
        raise ValueError(f"shadow_json.score_semantics is {shadow.get('score_semantics')!r}")
    baseline_only = shadow.get("baseline_only")
    if not isinstance(baseline_only, list):
        raise ValueError("shadow_json.baseline_only is not a list")
    seen: set[str] = set()
    for entry in baseline_only:
        if not isinstance(entry, dict) or set(entry) != {"item_id", "raw_weighted_score"}:
            raise ValueError("shadow_json.baseline_only entry is not {item_id, raw_weighted_score}")
        item_id = entry["item_id"]
        score = entry["raw_weighted_score"]
        if not isinstance(item_id, str) or not item_id or item_id in seen:
            raise ValueError(f"shadow_json.baseline_only has a missing or duplicate item_id {item_id!r}")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or score != score:
            raise ValueError(f"shadow_json.baseline_only[{item_id}] has a non-numeric raw_weighted_score")
        seen.add(item_id)
    count = shadow.get("quota_only_count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError(f"shadow_json.quota_only_count is {count!r}")


def _validate_source_quota_block(item_id: str, block: dict[str, object]) -> None:
    """Reject a per-row source_quota block that is not the complete frozen v1 shape."""
    expected = {"policy", "kind", "kind_cap", "source_cap", "baseline", "baseline_selected"}
    if set(block) != expected:
        raise ValueError(f"curated item {item_id} source_quota keys are {sorted(block)}")
    if block["policy"] != SOURCE_QUOTA_POLICY:
        raise ValueError(
            f"curated item {item_id} has source_quota policy {block['policy']!r}, expected {SOURCE_QUOTA_POLICY!r}"
        )
    if block["baseline"] != SOURCE_QUOTA_BASELINE:
        raise ValueError(f"curated item {item_id} has source_quota baseline {block['baseline']!r}")
    if not isinstance(block["kind"], str) or not block["kind"]:
        raise ValueError(f"curated item {item_id} has an empty source_quota kind")
    for key in ("kind_cap", "source_cap"):
        cap = block[key]
        if cap is not None and (isinstance(cap, bool) or not isinstance(cap, int) or cap < 1):
            raise ValueError(f"curated item {item_id} has {key}={cap!r}; expected a positive int or null")
    if not isinstance(block["baseline_selected"], bool):
        raise ValueError(f"curated item {item_id} has non-boolean baseline_selected")


def _rollback_quota_conn(dry_run: bool) -> sqlite3.Connection:
    """dry-run opens the database read-only (no file creation, no journal-mode pragma)."""
    if not dry_run:
        db.migrate()
        return db.get_conn()
    db_path = db.resolve_db_path(None)
    if not db_path.exists():
        raise FileNotFoundError(f"database not found: {db_path}")
    conn = sqlite3.connect(f"file:{quote(str(db_path))}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _admin_curate_rollback_quota(args: argparse.Namespace) -> int:
    try:
        conn_cm = _rollback_quota_conn(args.dry_run)
    except FileNotFoundError as exc:
        print(f"curate rollback-quota FAILED: {exc}; dry-run creates nothing; check AI_RADAR_DB", file=sys.stderr)
        return 1
    total_runs = 0
    total_removed = 0
    total_kept = 0
    rewritten_runs = 0
    with conn_cm as conn:
        try:
            run_ids = [
                row["id"]
                for row in conn.execute(
                    """
                    SELECT id
                    FROM curation_runs
                    WHERE id >= ? AND shadow_json IS NOT NULL AND shadow_json != ''
                    ORDER BY id
                    """,
                    (args.since,),
                )
            ]
        except sqlite3.OperationalError as exc:
            if "shadow_json" not in str(exc):
                raise
            print(
                f"curate rollback-quota no matching runs since={args.since} "
                "(this database has no quota shadow column yet, so no quota run exists; "
                "dry-run applies no migration); nothing to do"
            )
            return 0
        latest_run = conn.execute("SELECT id FROM curation_runs ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()
        latest_run_id = latest_run["id"] if latest_run is not None else None
        for index, run_id in enumerate(run_ids):
            try:
                shadow_row = conn.execute("SELECT shadow_json FROM curation_runs WHERE id=?", (run_id,)).fetchone()
                shadow = json.loads(shadow_row["shadow_json"])
                shadow_policy = shadow.get("policy") if isinstance(shadow, dict) else None
                if shadow_policy != SOURCE_QUOTA_POLICY:
                    raise ValueError(
                        f"run has shadow policy {shadow_policy!r}; this command only understands "
                        f"{SOURCE_QUOTA_POLICY!r}"
                    )
                _validate_source_quota_shadow(shadow)
                rows = conn.execute(
                    """
                    SELECT item_id, reason_json
                    FROM curated_items
                    WHERE run_id=?
                    ORDER BY rank
                    """,
                    (run_id,),
                ).fetchall()
                removed_item_ids: list[str] = []
                kept: list[tuple[str, dict[str, object]]] = []
                for row in rows:
                    reason = json.loads(row["reason_json"])
                    if not isinstance(reason, dict):
                        raise ValueError(f"curated item {row['item_id']} has non-object reason_json")
                    source_quota = reason.get("source_quota")
                    if not isinstance(source_quota, dict):
                        raise ValueError(f"curated item {row['item_id']} has no source_quota block")
                    _validate_source_quota_block(row["item_id"], source_quota)
                    baseline_selected = source_quota["baseline_selected"]
                    if baseline_selected is False:
                        removed_item_ids.append(row["item_id"])
                    else:
                        kept.append((row["item_id"], reason))

                uncalibrated: list[ScoredCandidate] = []
                for item_id, reason in kept:
                    # curate() only writes raw_weighted_score when it calibrated a
                    # multi-row list; a single-row run keeps the raw score in
                    # reason.weighted_score.
                    raw_score = reason.get("raw_weighted_score", reason.get("weighted_score"))
                    if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
                        raise ValueError(f"curated item {item_id} has no numeric raw_weighted_score/weighted_score")
                    uncalibrated.append(
                        ScoredCandidate(
                            eval_id=0,
                            item_id=item_id,
                            content_hash="",
                            url="",
                            published_at="",
                            weighted_score=float(raw_score),
                            reason=reason,
                        )
                    )
                calibrated = _calibrate_selected_scores(uncalibrated)
                if len(calibrated) == 1:
                    # Mirror what curate() stores for a single selected row: the raw
                    # score with no rank-linear calibration block.
                    single = calibrated[0]
                    reason = {
                        key: value
                        for key, value in single.reason.items()
                        if key not in {"score_calibration", "raw_weighted_score"}
                    }
                    calibrated = [replace(single, reason=reason)]
                # run-level count must agree with the row-level flags on every path,
                # including the already-rolled-back no-op path
                if shadow["quota_only_count"] != len(removed_item_ids):
                    raise ValueError(
                        f"shadow_json.quota_only_count={shadow['quota_only_count']} but "
                        f"{len(removed_item_ids)} rows carry baseline_selected=false"
                    )
                if not args.dry_run and removed_item_ids:
                    # A run with nothing to remove is already in its rolled-back state:
                    # leave ranks, scores and cached summaries untouched.
                    with conn:
                        conn.executemany(
                            "DELETE FROM curated_items WHERE run_id=? AND item_id=?",
                            [(run_id, item_id) for item_id in removed_item_ids],
                        )
                        for rank, candidate in enumerate(calibrated, start=1):
                            conn.execute(
                                """
                                UPDATE curated_items
                                SET rank=?, weighted_score=?, reason_json=?, summary_json=NULL
                                WHERE run_id=? AND item_id=?
                                """,
                                (
                                    rank,
                                    candidate.weighted_score,
                                    json.dumps(
                                        candidate.reason,
                                        ensure_ascii=False,
                                        sort_keys=True,
                                        separators=(",", ":"),
                                    ),
                                    run_id,
                                    candidate.item_id,
                                ),
                            )
                        shadow["quota_only_count"] = 0
                        shadow["rollback"] = {
                            "at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
                            "removed_item_ids": list(removed_item_ids),
                        }
                        conn.execute(
                            "UPDATE curation_runs SET output_curated_ids=?, shadow_json=? WHERE id=?",
                            (
                                json.dumps(
                                    [candidate.item_id for candidate in calibrated],
                                    separators=(",", ":"),
                                ),
                                json.dumps(shadow, ensure_ascii=False, separators=(",", ":")),
                                run_id,
                            ),
                        )
                    rewritten_runs += 1
            except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
                later = len(run_ids) - index - 1
                if args.dry_run:
                    earlier = "dry-run, no curated rows were changed"
                elif total_runs and total_removed:
                    earlier = (
                        f"{total_runs} processed before this one (rows_removed={total_removed}, their changes are kept)"
                    )
                elif total_runs:
                    earlier = f"{total_runs} processed before this one (no rows were removed)"
                else:
                    earlier = "none"
                print(
                    f"curate rollback-quota FAILED at run_id={run_id}: {exc}\n"
                    f"  this run: not changed; earlier runs: {earlier}; "
                    f"later matching runs: {later} not processed\n"
                    "  next: fix the offending run/item named above (reason_json needs a "
                    "source-quota-v1 block with boolean baseline_selected and a numeric "
                    "raw_weighted_score), then rerun the same command",
                    file=sys.stderr,
                )
                return 1

            removed_count = len(removed_item_ids)
            kept_count = len(kept)
            if args.dry_run:
                print(
                    f"curate rollback-quota run_id={run_id} "
                    f"rows_would_remove={removed_count} rows_kept={kept_count} mode=dry-run"
                )
            else:
                print(
                    f"curate rollback-quota run_id={run_id} "
                    f"rows_removed={removed_count} rows_kept={kept_count} mode=write"
                )
                if run_id == latest_run_id and removed_count:
                    count = precompute_curated_summaries(conn, run_id)
                    print(f"curate rollback-quota run_id={run_id} summaries recomputed for {count} rows (latest run)")
            total_runs += 1
            total_removed += removed_count
            total_kept += kept_count

    if not total_runs:
        print(
            f"curate rollback-quota no matching runs since={args.since} "
            "(a run qualifies when its id >= since and it has a quota shadow); nothing to do"
        )
    elif args.dry_run:
        print(
            f"curate rollback-quota DRY RUN complete runs={total_runs} "
            f"rows_would_remove={total_removed} rows_kept={total_kept}; no curated rows were changed; "
            "rerun without --dry-run to apply"
        )
    elif total_removed:
        print(
            f"curate rollback-quota complete runs={total_runs} "
            f"rows_removed={total_removed} rows_kept={total_kept}; "
            f"rank/display metadata rewritten for {rewritten_runs} of {total_runs} run(s); "
            "the next db-sync publishes it; no further action needed"
        )
    else:
        print(
            f"curate rollback-quota complete runs={total_runs} "
            f"rows_removed=0 rows_kept={total_kept}; already rolled back, nothing to remove; "
            "no further action needed"
        )
    return 0


def _interpret(args: argparse.Namespace) -> int:
    db.migrate()
    with db.get_conn() as conn:
        summary = run_interpret(
            conn,
            backfill=args.backfill,
            limit=args.limit,
            assistant_root=args.assistant_root,
            user=args.user,
        )
    if summary.skipped:
        print(f"interpret skipped=true message={summary.message}")
    else:
        print(f"interpret processed={summary.processed} errors={summary.errors}")
    return 0


def _eval(args: argparse.Namespace) -> int:
    db.migrate()
    aihot_markdown = Path(args.aihot_markdown) if args.aihot_markdown else DEFAULT_AIHOT_MARKDOWN
    output_dir = Path(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_DIR
    with db.get_conn() as conn:
        artifacts = run_eval(
            conn,
            selected_date=args.date,
            aihot_markdown_path=aihot_markdown,
            output_dir=output_dir,
            match_scope=args.match_scope,
            audit=args.audit,
        )
    print(f"eval report={artifacts.report_path}")
    print(f"eval compare={artifacts.compare_path}")
    if artifacts.audit_path:
        print(f"eval audit={artifacts.audit_path}")
    print(f"eval matched={artifacts.matched_count} sample={artifacts.sample_count}")
    for key, value in artifacts.metrics.items():
        print(f"{key}={'PASS' if value['pass'] else 'FAIL'} {value['detail']}")
    return 0


def _serve(args: argparse.Namespace) -> int:
    pre_migrated_env = "AI_RADAR_PRE_MIGRATED_DB"
    previous = os.environ.get(pre_migrated_env)
    if args.pre_migrated_db:
        os.environ[pre_migrated_env] = "1"
    try:
        from .web.app import serve

        serve(port=args.port, host=args.host)
    finally:
        if args.pre_migrated_db:
            if previous is None:
                os.environ.pop(pre_migrated_env, None)
            else:
                os.environ[pre_migrated_env] = previous
    return 0


def _performance_probe(args: argparse.Namespace) -> int:
    result = run_journey_monitor(
        origin_url=args.origin_url,
        public_url=args.public_url,
        sample_path=Path(args.samples_path),
        state_path=Path(args.state_path),
        evidence_dir=Path(args.evidence_dir),
        pipeline_lock_path=Path(args.pipeline_lock),
        db_path=Path(args.db_path),
    )
    print(str(result["scope"]))
    samples = result.get("samples", [])
    if isinstance(samples, list):
        for sample in samples:
            if not isinstance(sample, dict):
                continue
            print(
                f"{sample.get('journey')} vantage={sample.get('vantage')} "
                f"latency_ms={float(sample.get('value_ms', 0)):.3f} "
                f"load_class={sample.get('load_class')} provisional=true"
            )
    alerts = result.get("alerts", {})
    sent_count = alerts.get("sent_count", 0) if isinstance(alerts, dict) else 0
    print(f"stored={len(samples) if isinstance(samples, list) else 0} alerts_sent={sent_count}")
    return 0


def _performance_remediate(args: argparse.Namespace) -> int:
    result = remediate_confirmed_incident(
        RemediationConfig(
            main_checkout=Path(args.main_checkout),
            alert_state_path=Path(args.alert_state_path),
            performance_evidence_dir=Path(args.performance_evidence_dir),
            worker_root=Path(args.worker_root),
            remediation_state_path=Path(args.remediation_state_path),
            lock_path=Path(args.lock_path),
            remediation_evidence_dir=Path(args.remediation_evidence_dir),
            production_db_path=Path(args.production_db_path),
            codex_binary=args.codex_binary,
            timeout_seconds=args.timeout_seconds,
        )
    )
    print(f"status={result['status']}")
    for key in ("candidate_commit", "worktree", "summary_path", "evidence_path", "reason"):
        if key in result:
            print(f"{key}={result[key]}")
    return 0 if result["status"] != "failed" else 1


def _format_alert_send_status(raw: object) -> str | None:
    if not isinstance(raw, dict):
        return None
    rule_id = raw.get("rule_id")
    notification_type = raw.get("type")
    send_result = raw.get("send_result")
    if not rule_id or not notification_type or not isinstance(send_result, dict):
        return None
    if send_result.get("skipped"):
        return f"send {rule_id} {notification_type} skipped reason={send_result.get('reason')}"
    status_code = send_result.get("status_code")
    if status_code is not None:
        return f"send {rule_id} {notification_type} sent status_code={status_code}"
    return f"send {rule_id} {notification_type} sent"


def _wechat_comparison_not_comparable(
    *,
    account_name: str,
    attempt_id: int,
    reason: str,
    next_step_override: str | None = None,
) -> int:
    print("WeChat discovery comparison: NOT_COMPARABLE")
    print(f"Account: {account_name}")
    print(f"Probe attempt: {attempt_id}")
    print(f"Reason: {reason}")
    print("Impact: no coverage conclusion was produced; Mp2RSS and production items are unchanged")
    if next_step_override is not None:
        next_step = next_step_override
    elif "absent or disabled" in reason:
        next_step = "restore or enable wx_mp2rss before using it as the comparison baseline"
    elif "author bucket" in reason:
        next_step = "correct the configured account name or production author mapping"
    elif "baseline is empty" in reason:
        next_step = "choose a window containing Mp2RSS items for this account"
    elif "page did not reach" in reason:
        next_step = "use a narrower reached window; do not infer coverage beyond this page"
    elif "request page size" in reason or "predates persisted" in reason:
        next_step = "run a new authorized shadow probe after cooldown, then compare that attempt"
    elif "article-URL public-biz verification" in reason:
        next_step = (
            "resolve a provisional account mapping, then run a new authorized identity-checking probe after cooldown"
        )
    elif "fakeid identity resolution" in reason or "fakeid mapping" in reason:
        next_step = "resolve the account identity, then run a new authorized probe after cooldown"
    elif "target does not match" in reason:
        next_step = "select the configured account that matches this shadow attempt"
    elif "ended as" in reason:
        next_step = "wait for an authorized successful shadow probe before comparing"
    elif "URL" in reason or "identity form" in reason:
        next_step = "inspect the URL identities and establish a proven same-article mapping"
    elif "does not exist" in reason:
        next_step = "run status and select an existing successful attempt ID"
    else:
        next_step = "restore the unavailable comparison input, then retry this read-only command"
    print(f"Next: {next_step}")
    return 2


def _parse_stored_datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("stored timestamp has no timezone")
    return parsed.astimezone(UTC)


def _admin_wechat_discovery_compare(args: argparse.Namespace) -> int:
    try:
        config = load_discovery_config(args.config)
        account = next((item for item in config.accounts if item.name == args.account), None)
        if account is None:
            raise ValueError(f"unknown configured account: {args.account}")
        since = _parse_stored_datetime(args.since)
        store = DiscoveryStore(args.state_db)
        attempt = store.attempt(args.attempt)
        identity_issue = store.attempt_identity_issue(args.attempt)
    except DiscoveryStoreVersionError:
        return _wechat_comparison_not_comparable(
            account_name=args.account,
            attempt_id=args.attempt,
            reason="the shadow state schema requires explicit migration",
            next_step_override=(f"run ./run.sh admin wechat-discovery migrate --state-db {args.state_db}"),
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        return _wechat_comparison_not_comparable(
            account_name=args.account,
            attempt_id=args.attempt,
            reason=f"comparison inputs are unavailable ({type(exc).__name__})",
        )
    if attempt is None:
        return _wechat_comparison_not_comparable(
            account_name=account.name,
            attempt_id=args.attempt,
            reason="the requested shadow attempt does not exist",
        )
    success_states = {
        DiscoveryState.SUCCESS,
        DiscoveryState.SUCCESS_NO_NEW_SHADOW_CANDIDATES,
        DiscoveryState.SUCCESS_WITH_NEW_SHADOW_CANDIDATES,
    }
    if attempt.state not in success_states:
        return _wechat_comparison_not_comparable(
            account_name=account.name,
            attempt_id=args.attempt,
            reason=f"the shadow attempt ended as {attempt.state.value}, not success",
        )
    if identity_issue is not None:
        return _wechat_comparison_not_comparable(
            account_name=account.name,
            attempt_id=args.attempt,
            reason=identity_issue,
        )
    if len(attempt.account_results) != 1 or (
        attempt.account_results[0].account_name,
        attempt.account_results[0].biz,
    ) != (account.name, account.public_biz):
        return _wechat_comparison_not_comparable(
            account_name=account.name,
            attempt_id=args.attempt,
            reason="the shadow attempt target does not match the configured account",
        )

    production_path = Path(args.db_path).resolve()
    try:
        conn = sqlite3.connect(f"{production_path.as_uri()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            source = conn.execute("SELECT enabled FROM sources WHERE id='wx_mp2rss'").fetchone()
            if source is None or int(source["enabled"]) != 1:
                raise ShadowNotComparable("the production wx_mp2rss source is absent or disabled")
            authors = {
                str(row["author"])
                for row in conn.execute(
                    "SELECT DISTINCT author FROM items WHERE source_id='wx_mp2rss' AND author IS NOT NULL"
                )
            }
            matched_authors = sorted(
                author for author in authors if normalized_account_name(author) == normalized_account_name(account.name)
            )
            if not matched_authors:
                raise ShadowNotComparable("the configured account name has no normalized production author bucket")
            placeholders = ", ".join("?" for _ in matched_authors)
            baseline_rows = conn.execute(
                f"""
                SELECT url, published_at
                FROM items
                WHERE source_id='wx_mp2rss' AND author IN ({placeholders})
                """,
                matched_authors,
            ).fetchall()
        finally:
            conn.close()
        baseline = [
            DiscoveryArticle(
                account_name=account.name,
                biz=account.public_biz,
                title="",
                url=str(row["url"]),
                author=account.name,
                published_at=_parse_stored_datetime(row["published_at"]),
            )
            for row in baseline_rows
        ]
        comparison = compare_shadow_window(
            account_name=account.name,
            biz=account.public_biz,
            baseline=baseline,
            since=since,
            attempt=attempt,
        )
    except (OSError, sqlite3.Error, ValueError, ShadowNotComparable) as exc:
        reason = str(exc) if isinstance(exc, ShadowNotComparable) else type(exc).__name__
        return _wechat_comparison_not_comparable(
            account_name=account.name,
            attempt_id=args.attempt,
            reason=reason,
        )

    matched_count = comparison.baseline_count - len(comparison.missing_baseline_urls)
    state = "COVERED_IN_WINDOW" if comparison.covered else "MISSING_IN_WINDOW"
    print(f"WeChat discovery comparison: {state}")
    print(f"Account: {account.name}")
    print(f"Probe attempt: {args.attempt}")
    print(f"Window: {since.isoformat()} .. {attempt.finished_at.isoformat()}")
    print(f"Coverage: {matched_count}/{comparison.baseline_count} Mp2RSS baseline URLs matched")
    print(f"Candidate-only URLs: {len(comparison.candidate_only_urls)}")
    for url in comparison.missing_baseline_urls:
        print(f"Missing: {url}")
    for url in comparison.candidate_only_urls:
        print(f"Candidate-only: {url}")
    print(
        "Scope: this result covers only this account, attempt, and window; it does not prove "
        "shared omissions, other accounts, or future coverage"
    )
    print("Impact: Mp2RSS and production items are unchanged")
    if comparison.covered:
        print("Next: repeat across the required multi-day account set; do not cut over yet")
        return 0
    print("Next: keep Mp2RSS and investigate each missing URL before another canary step")
    return 1


def _readonly_wechat_discovery_evidence(
    path: str | Path,
) -> tuple[str, int, int, int | None, str | None, int | None, str | None]:
    state_path = Path(path)
    if not state_path.exists():
        return "none", 0, 0, None, None, None, None
    if not state_path.is_file():
        return "unavailable", 0, 0, None, None, None, None
    uri = f"file:{state_path.resolve()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as conn:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            attempt_count = int(conn.execute("SELECT COUNT(*) FROM discovery_attempts").fetchone()[0])
            tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            resolution_count = (
                int(conn.execute("SELECT COUNT(*) FROM identity_resolution_attempts").fetchone()[0])
                if "identity_resolution_attempts" in tables
                else 0
            )
            verified_row = None
            if version in {7, 8, 9, 10}:
                possible_rows = conn.execute(
                    """
                    SELECT a.id, r.configured_account_name, r.configured_public_biz
                    FROM discovery_attempts a
                    JOIN identity_resolution_attempts r ON r.id=a.identity_resolution_id
                    WHERE a.outcome='success'
                      AND a.identity_resolution_origin='provisional_searchbiz_match'
                      AND a.target_identity_evidence='article_url_public_biz_verified'
                      AND r.outcome='provisional_match'
                      AND r.provisional_match_origin='searchbiz_unique_normalized_name'
                      AND r.invalidated_at IS NULL
                      AND r.superseding_resolution_id IS NULL
                      AND EXISTS (
                        SELECT 1 FROM discovery_attempt_candidates c
                        WHERE c.probe_attempt_id=a.id
                      )
                    ORDER BY a.id DESC
                    """
                ).fetchall()
                for possible_row in possible_rows:
                    candidate_rows = conn.execute(
                        "SELECT url FROM discovery_attempt_candidates WHERE probe_attempt_id=?",
                        (possible_row[0],),
                    ).fetchall()
                    try:
                        if candidate_rows and all(
                            observed_article_biz(str(candidate_row[0])) == str(possible_row[2])
                            for candidate_row in candidate_rows
                        ):
                            verified_row = possible_row
                            break
                    except (
                        DiscoveryIdentityMismatch,
                        DiscoveryIdentityUnverified,
                        DiscoveryResponseInvalid,
                    ):
                        continue
            latest_platform_error = None
            if version in {9, 10}:
                latest_platform_error = conn.execute(
                    """
                    SELECT platform_error_ret, platform_error_ret_origin, started_at
                    FROM identity_resolution_attempts
                    WHERE platform_error_ret_origin != 'not_applicable'
                    UNION ALL
                    SELECT platform_error_ret, platform_error_ret_origin, started_at
                    FROM discovery_attempts
                    WHERE platform_error_ret_origin != 'not_applicable'
                    ORDER BY started_at DESC
                    LIMIT 1
                    """
                ).fetchone()
    except sqlite3.Error:
        return "unavailable", 0, 0, None, None, None, None
    return (
        f"schema v{version}",
        attempt_count,
        resolution_count,
        (int(verified_row[0]) if verified_row is not None else 0 if version in {6, 7, 8, 9, 10} else None),
        str(verified_row[1]) if verified_row is not None else None,
        (
            int(latest_platform_error[0])
            if latest_platform_error is not None and latest_platform_error[0] is not None
            else None
        ),
        (str(latest_platform_error[1]) if latest_platform_error is not None else None),
    )


def _platform_error_evidence_line(
    label: str,
    platform_error_ret: int | None,
    platform_error_ret_origin: str,
) -> str | None:
    if platform_error_ret_origin == "recorded":
        assert platform_error_ret is not None
        return f"{label}: ret={platform_error_ret}"
    if platform_error_ret_origin == "predates_persistence":
        return f"{label}: exact ret was not recorded by the old schema"
    return None


def _print_local_request_timing(
    next_request_at: datetime,
    *,
    timespec: str | None = None,
) -> None:
    print("Request timing basis: local safety policy; not a published WeChat platform window")
    local_time = next_request_at.astimezone(SHANGHAI_TZ)
    rendered = local_time.isoformat(timespec=timespec) if timespec else local_time.isoformat()
    print(f"Next request allowed by local policy after: {rendered}")


def _target_identity_evidence_line(attempt: DiscoveryAttempt) -> str:
    evidence = attempt.target_identity_evidence
    if evidence is TargetIdentityEvidence.EMPTY_ARTICLE_LIST:
        return "Target identity: NOT_VERIFIED — valid empty article list contained no public article URL"
    if evidence is TargetIdentityEvidence.ARTICLE_URL_PUBLIC_BIZ_UNAVAILABLE:
        return "Target identity: NOT_VERIFIED — returned article URL did not expose a unique public biz"
    if evidence is TargetIdentityEvidence.ARTICLE_URL_PUBLIC_BIZ_MISMATCH:
        return "Target identity: MISMATCH — returned article URL public biz contradicted configured target"
    if evidence is TargetIdentityEvidence.ARTICLE_URL_PUBLIC_BIZ_VERIFIED:
        return "Target identity: VERIFIED — all returned article URLs matched configured public biz"
    if evidence is TargetIdentityEvidence.PREDATES_V7_VERIFICATION:
        return "Target identity: NOT_VERIFIED — probe predates persisted article-URL public-biz verification"
    if evidence is TargetIdentityEvidence.PENDING:
        return "Target identity: PENDING — probe request outcome is unknown"
    return "Target identity: NOT_OBSERVED — request produced no public article URL evidence"


def _admin(args: argparse.Namespace) -> int:
    if args.admin_command == "db" and args.db_command == "migrate":
        db.migrate()
        from .llm_usage import migrate_usage_db

        usage_path = migrate_usage_db(main_db_path=db.resolve_db_path())
        print(f"migrated {db.resolve_db_path()}")
        print(f"migrated llm_usage {usage_path}")
        return 0
    if args.admin_command == "db" and args.db_command == "backfill-links":
        from .presentation.links import backfill_item_links, links_ready

        with db.get_conn() as conn:
            already = links_ready(conn)
            # The backfill takes its own write lock per batch and refuses to run
            # inside a transaction it did not start, so leave one open here and
            # it raises. Reads do not open one (verified on SQLite 3.50.4:
            # `in_transaction` is False before and after a SELECT), but a future
            # write added above this line would, hence the guard rather than an
            # assumption about what this block happens to contain.
            if conn.in_transaction:
                conn.commit()
            written = backfill_item_links(conn)
        target = db.resolve_db_path()
        if already:
            print(f"item_links already complete for {target}; re-ran and wrote {written} links")
        else:
            print(f"item_links backfilled for {target}: {written} links")
        print("related discussions now answer from the index instead of a full text scan")
        return 0
    if args.admin_command == "db" and args.db_command == "checkpoint":
        result = db.checkpoint_db(args.db_path)
        print(f"checkpoint busy={result.busy} log={result.log} checkpointed={result.checkpointed}")
        return 0
    if args.admin_command == "db" and args.db_command in {"retain", "slim"}:
        return _admin_db_retention(args)
    if args.admin_command == "sources":
        with db.get_conn() as conn:
            if args.sources_command == "reload":
                sources = reload_sources(conn)
                print(f"reloaded {len(sources)} sources")
                return 0
            if args.sources_command == "list":
                rows = conn.execute("SELECT id, tier, enabled, url FROM sources ORDER BY id").fetchall()
                for row in rows:
                    print(f"{row['id']}\t{row['tier']}\t{row['enabled']}\t{row['url']}")
                return 0
    if args.admin_command == "wechat-kb":
        try:
            db_path = _existing_db_path(args.db_path)
            with db.get_conn(db_path) as conn:
                if args.wechat_kb_command == "import":
                    import_receipt = import_catalog(
                        conn,
                        assistant_root=Path(args.assistant_root).expanduser().resolve(),
                        user=args.user,
                        dry_run=args.dry_run,
                        limit=args.limit,
                    )
                    if import_receipt.dry_run:
                        label = "DRY RUN (no database changes)"
                    elif import_receipt.remaining:
                        label = "BATCH COMPLETE (more eligible articles remain)"
                    elif not import_receipt.changed:
                        label = "COMPLETE (nothing new to import)"
                    else:
                        label = "COMPLETE"
                    print(f"WeChat KB import: {label}")
                    print(f"Run id: {import_receipt.run_id}")
                    print(
                        "Counts: "
                        f"catalog={import_receipt.catalog_articles} eligible={import_receipt.eligible} "
                        f"imported={import_receipt.imported} already_present={import_receipt.already_present} "
                        "existing_without_interpretation="
                        f"{import_receipt.existing_without_interpretation} skipped={import_receipt.skipped} "
                        f"remaining={import_receipt.remaining}"
                    )
                    print(f"Postcheck: {import_receipt.postcheck}")
                    print(f"Changed: {'yes' if import_receipt.changed else 'no'}")
                    if import_receipt.skipped_reasons:
                        labels = {
                            "article_metadata_or_date_invalid": "article metadata or date invalid",
                            "article_or_summary_encoding_invalid": "article or summary encoding invalid",
                            "article_or_summary_file_missing": "article or summary file missing",
                            "article_or_summary_file_unreadable": "article or summary file unreadable",
                            "canonical_url_mismatch": "catalog URL and canonical URL disagree",
                            "entry_status": "catalog entry invalid",
                            "file_both_missing": "article and summary files both missing",
                            "missing_slug": "catalog slug missing",
                            "not_wechat_article": "not a WeChat article URL",
                            "schema_version": "catalog schema version unsupported",
                            "vector_zero_or_nonfinite": "embedding vector zero or non-finite",
                        }
                        details = "; ".join(
                            f"{labels.get(reason, reason.replace('_', ' '))}: {count}"
                            for reason, count in sorted(import_receipt.skipped_reasons.items())
                        )
                        print(f"Skipped reasons: {details}")
                    if import_receipt.dry_run:
                        print("Next: rerun without --dry-run to import this candidate set")
                    elif import_receipt.remaining:
                        print("Next: rerun import to process the remaining eligible articles")
                    elif import_receipt.skipped:
                        print(
                            "Next: review skipped reasons; correct source records you expected to import, "
                            "then rerun; successful imports are already searchable"
                        )
                    else:
                        print("Next: search /wechat for an imported title; reruns are idempotent")
                    return 0
        except (FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as exc:
            print("WeChat KB operation: FAILED")
            print(f"Reason: {exc}")
            print("Changed: no committed changes from this operation")
            print("Next: resolve the reported reason, then rerun the same command")
            return 1
    if args.admin_command == "x-media" and args.x_media_command == "backfill":
        with db.get_conn(args.db_path) as conn:
            backfill_receipt = backfill_x_media(conn, limit=args.limit, dry_run=args.dry_run).as_dict()
        print(json.dumps(backfill_receipt, ensure_ascii=False, sort_keys=True))
        # Non-zero when the receipt does not reconcile: requested must equal
        # what came back plus what we explicitly could not resolve.
        return 0 if backfill_receipt["reconciled"] else 1

    if args.admin_command == "wechat-avatar" and args.wechat_avatar_command == "refresh":
        with db.get_conn(args.db_path) as conn:
            avatar_url = refresh_wechat_avatar(conn, args.account)
            conn.commit()
        if avatar_url:
            print(f"wechat-avatar account={args.account} avatar_url={avatar_url}")
            return 0
        print(f"wechat-avatar account={args.account} avatar_url=")
        return 1
    if args.admin_command == "wechat-discovery" and args.wechat_discovery_command == "login":
        print("WeChat discovery login: WAITING")
        print("Action: scan the QR code in the visible browser; this command will not enable canary")
        try:
            capture_login(
                session_path=Path(args.session_file),
                browser_profile=Path(args.browser_profile),
                timeout_seconds=args.timeout_seconds,
            )
        except (DiscoveryLoginError, OSError, ValueError) as exc:
            print("WeChat discovery login: FAILED")
            print(f"Impact: No new private session was saved ({type(exc).__name__})")
            print("Preserved: any existing session file remains available")
            print("Next: run status; retry login once only if authentication is still required")
            return 1
        print("WeChat discovery login: SAVED")
        print(f"Session: {args.session_file} (0600)")
        print("Canary: unchanged and not scheduled")
        print("Next: resolve one configured account identity; do not probe with its public biz")
        return 0
    if args.admin_command == "wechat-discovery" and args.wechat_discovery_command == "migrate":
        store = DiscoveryStore(args.state_db)
        try:
            before, after = store.migrate()
        except (OSError, sqlite3.Error, DiscoveryStoreVersionError) as exc:
            print("WeChat discovery state migration: FAILED")
            print(f"State: {args.state_db}")
            print(f"Impact: no trusted schema transition completed ({type(exc).__name__})")
            print("Preserved: backend requests, Mp2RSS, and production items are unchanged")
            print("Next: restore the exact pre-migration backup or repair the shadow state")
            return 2
        outcome = "CURRENT" if before == after else "MIGRATED"
        print(f"WeChat discovery state migration: {outcome}")
        print(f"State: {args.state_db}")
        print(f"Schema: v{before} -> v{after}")
        print("Impact: private shadow state only; no backend request, Mp2RSS, production item, or scheduler changed")
        print("Next: run ./run.sh admin wechat-discovery status")
        return 0
    if args.admin_command == "wechat-discovery" and args.wechat_discovery_command == "resolve":
        try:
            config = load_discovery_config(args.config)
            account = next((item for item in config.accounts if item.name == args.account), None)
            if account is None:
                raise ValueError(f"unknown configured account: {args.account}")
            if not config.manual_backend_requests_enabled:
                raise ValueError("manual discovery requests are disabled in the config")
            credentials = load_credentials(args.session_file)
            verify_account_identity(account)
        except (OSError, PermissionError, ValueError, DiscoveryIdentityUnverified) as exc:
            print("WeChat identity resolution: UNCONFIGURED")
            print(f"Account: {args.account}")
            print(f"Impact: no backend request was reserved or sent ({type(exc).__name__})")
            print("Next: validate the account seed proof, config, and private session file")
            return 1

        store = DiscoveryStore(args.state_db)
        started_at = datetime.now(UTC)
        try:
            resolution_id = store.reserve_identity_resolution(account, config=config, started_at=started_at)
        except DiscoveryCooldownActive as exc:
            print("WeChat identity resolution: COOLDOWN")
            print(f"Account: {account.name}")
            print("Impact: no backend request was sent; shadow evidence is unchanged")
            _print_local_request_timing(exc.next_request_at)
            print(f"Next: after that time, rerun resolve for {account.name} once")
            return 1
        except (OSError, sqlite3.Error, DiscoveryStoreVersionError) as exc:
            print("WeChat identity resolution: STATE_UNAVAILABLE")
            print(f"Account: {account.name}")
            print(f"Impact: no backend request was sent ({type(exc).__name__})")
            print("Next: repair or migrate the private shadow state database")
            return 2

        provisional_identity = None
        platform_error_ret = None
        try:
            candidates = WeChatAdminClient(credentials).search_accounts(account_name=account.name)
            provisional_identity = select_unique_searchbiz_candidate(account, candidates)
            resolution_state = IdentityResolutionState.PROVISIONAL_MATCH
        except DiscoveryIdentityNoMatch:
            resolution_state = IdentityResolutionState.NO_MATCH
        except DiscoveryIdentityAmbiguous:
            resolution_state = IdentityResolutionState.AMBIGUOUS_MATCH
        except DiscoveryAuthRequired as exc:
            resolution_state = IdentityResolutionState.AUTH_REQUIRED
            platform_error_ret = exc.platform_error_ret
        except DiscoveryRateLimited as exc:
            resolution_state = IdentityResolutionState.RATE_LIMITED
            platform_error_ret = exc.platform_error_ret
        except DiscoveryPlatformRejected as exc:
            resolution_state = IdentityResolutionState.PLATFORM_REJECTED
            platform_error_ret = exc.platform_error_ret
        except DiscoveryRequestFailed:
            resolution_state = IdentityResolutionState.REQUEST_FAILED
        except DiscoveryResponseInvalid:
            resolution_state = IdentityResolutionState.RESPONSE_INVALID
        finished_at = datetime.now(UTC)
        try:
            store.complete_identity_resolution(
                resolution_id,
                state=resolution_state,
                finished_at=finished_at,
                provisional=provisional_identity,
                platform_error_ret=platform_error_ret,
            )
        except (OSError, sqlite3.Error, ValueError, DiscoveryStoreVersionError) as exc:
            print("WeChat identity resolution: REQUEST_OUTCOME_UNKNOWN")
            print(f"Account: {account.name}")
            print(f"Reservation: {resolution_id} resolve")
            print(f"Impact: no identity result is trusted ({type(exc).__name__})")
            print("Preserved: the reservation still consumes this cooldown window")
            print("Next: repair the private shadow database; do not repeat the request")
            return 2

        print(f"WeChat identity resolution: {resolution_state.value.upper()}")
        print(f"Account: {account.name}")
        print(f"Resolution: {resolution_id} resolve")
        print("Search match: normalized account name only")
        print("Public biz verification: NOT_OBSERVED — searchbiz did not return public biz")
        print("Impact: Mp2RSS, production items, and canary scheduling are unchanged")
        next_request_at = finished_at + config.refresh_interval
        if resolution_state is IdentityResolutionState.PROVISIONAL_MATCH:
            print("Mapping: provisional and available for one identity-checking probe only")
            print("Private mapping identifier: not displayed")
            _print_local_request_timing(next_request_at, timespec="seconds")
            print(f"Next: after that time, run one authorized probe for {account.name}")
            return 0
        if resolution_state is IdentityResolutionState.AUTH_REQUIRED:
            print("Next: sign in again only with the authorized WeChat admin account")
        elif resolution_state is IdentityResolutionState.RATE_LIMITED:
            next_request_at = backend_request_blocked_until(config, store.latest_backend_request(), now=finished_at)
            assert next_request_at is not None
            _print_local_request_timing(next_request_at, timespec="seconds")
            print("Next: wait until the local safety window ends; do not retry in a loop")
        elif resolution_state is IdentityResolutionState.PLATFORM_REJECTED:
            assert platform_error_ret is not None
            print(f"Platform error: ret={platform_error_ret}")
            print(
                "Next: inspect the request target, authorized account conditions, and "
                "platform contract; do not repeat the unchanged request"
            )
        elif resolution_state in {
            IdentityResolutionState.NO_MATCH,
            IdentityResolutionState.AMBIGUOUS_MATCH,
        }:
            print("Next: inspect the configured account name; do not guess a private mapping")
        elif resolution_state is IdentityResolutionState.REQUEST_FAILED:
            print("Next: verify direct HTTPS reachability before one later retry")
        else:
            print("Next: inspect only a redacted response shape before changing the parser")
        return 1
    if args.admin_command == "wechat-discovery" and args.wechat_discovery_command == "compare":
        return _admin_wechat_discovery_compare(args)
    if args.admin_command == "wechat-discovery" and args.wechat_discovery_command == "status":
        latest_successful_attempts: tuple[tuple[int, str, str], ...]
        ready_accounts: tuple[tuple[str, int], ...]
        try:
            config = load_discovery_config(args.config)
            disabled_evidence: tuple[str, int, int, int | None, str | None, int | None, str | None] | None = None
            if not config.manual_backend_requests_enabled:
                disabled_evidence = _readonly_wechat_discovery_evidence(args.state_db)
                latest_attempt_id = None
                latest_attempt = None
                latest_successful_attempts = ()
                latest_request = None
                latest_resolution = None
                ready_accounts = ()
                identity_counts = (0, 0, 0, len(config.accounts))
                latest_verified_probe_id = disabled_evidence[3] if disabled_evidence[3] not in {None, 0} else None
            else:
                status_store = DiscoveryStore(args.state_db)
                latest_attempt_id = status_store.latest_attempt_id()
                latest_attempt = status_store.attempt(latest_attempt_id) if latest_attempt_id is not None else None
                latest_successful_attempts = status_store.latest_successful_attempts()
                latest_request = status_store.latest_backend_request()
                latest_resolution = status_store.latest_identity_resolution()
                ready_accounts, assigned_count, invalidated_count, unresolved_count = status_store.identity_status(
                    config.accounts
                )
                identity_counts = (
                    len(ready_accounts),
                    assigned_count,
                    invalidated_count,
                    unresolved_count,
                )
                latest_verified_probe_id = status_store.latest_identity_verified_successful_probe_id()
            discovery_status = effective_status(
                config,
                credential_path=args.session_file,
                latest_attempt=latest_attempt,
                latest_request=latest_request,
                resolved_ready_count=identity_counts[0],
                assigned_count=identity_counts[1],
                invalidated_count=identity_counts[2],
                unresolved_count=identity_counts[3],
                ready_accounts=ready_accounts,
            )
            if discovery_status.state not in {
                DiscoveryGateState.DISABLED,
                DiscoveryGateState.UNCONFIGURED,
            }:
                try:
                    load_credentials(args.session_file)
                except (OSError, PermissionError, ValueError):
                    discovery_status = effective_status(
                        config,
                        credential_path=Path(args.session_file).with_name("missing-session"),
                    )
        except DiscoveryStoreVersionError:
            print("WeChat discovery: UNAVAILABLE")
            print("Impact: status could not be determined without changing the shadow state")
            print(f"Next: run ./run.sh admin wechat-discovery migrate --state-db {args.state_db}")
            return 2
        except (OSError, ValueError, sqlite3.Error) as exc:
            print("WeChat discovery: UNAVAILABLE")
            print(f"Impact: status could not be determined ({type(exc).__name__})")
            print("Next: validate the discovery config and state database")
            return 2

        print(f"WeChat discovery request gate: {discovery_status.state.value.upper()}")
        print(f"Accounts: {discovery_status.account_count} configured")
        readiness_message = (
            "NOT_VALIDATED — article-URL public-biz-verified probe exists; explicit comparison required"
            if latest_verified_probe_id is not None
            else "NOT_VALIDATED — no article-URL public-biz-verified live probe"
        )
        if disabled_evidence is not None and disabled_evidence[3] is None:
            readiness_message = "UNASSESSED — shadow evidence is unavailable"
        print(f"Replacement readiness: {readiness_message}")
        if discovery_status.state is DiscoveryGateState.DISABLED:
            print("Impact: Mp2RSS remains unchanged; no WeChat admin requests will run")
            print("Credentials: not checked while disabled")
            assert disabled_evidence is not None
            print(
                f"Existing shadow evidence: {disabled_evidence[0]}, "
                f"{disabled_evidence[1]} probe attempts, "
                f"{disabled_evidence[2]} resolution attempts"
            )
            if disabled_evidence[3] is None:
                print("Historical shadow evidence: UNASSESSED")
            elif disabled_evidence[3] == 0:
                print("Historical shadow evidence: NO_ARTICLE_URL_BIZ_VERIFIED_SUCCESS")
            else:
                print(
                    "Historical shadow evidence: ARTICLE_URL_BIZ_VERIFIED_SUCCESSFUL_PROBE "
                    f"— probe {disabled_evidence[3]} ({disabled_evidence[4]}); "
                    "comparison not assessed"
                )
            if disabled_evidence[6] is not None:
                platform_line = _platform_error_evidence_line(
                    "Historical platform failure",
                    disabled_evidence[5],
                    disabled_evidence[6],
                )
                if platform_line is not None:
                    print(platform_line)
            print("Canary: not started")
            print("Next: no action while disabled; explicitly enable only for an authorized one-shot request")
            return 0
        if discovery_status.state is DiscoveryGateState.UNCONFIGURED:
            print("Impact: no WeChat admin requests can run")
            print(
                "Next: only if you have an authorized WeChat admin account, run "
                "./run.sh admin wechat-discovery login "
                f"--session-file {args.session_file}"
            )
            print("Canary: not started")
            return 1
        print(
            "Identity mapping: "
            f"{discovery_status.resolved_ready_count} provisional ready, "
            f"{discovery_status.assigned_count} assigned to probe reservation, "
            f"{discovery_status.invalidated_count} invalidated, "
            f"{discovery_status.unresolved_count} unresolved"
        )
        if discovery_status.latest_request is not None:
            print(
                "Latest backend request: "
                f"{discovery_status.latest_request.kind} "
                f"{discovery_status.latest_request.id} "
                f"({discovery_status.latest_request.account_name}) "
                f"{discovery_status.latest_request.state.upper()}"
            )
            platform_line = _platform_error_evidence_line(
                "Latest backend request platform error",
                discovery_status.latest_request.platform_error_ret,
                discovery_status.latest_request.platform_error_ret_origin,
            )
            if platform_line is not None:
                print(platform_line)
        if latest_resolution is not None:
            print(
                f"Latest identity resolution: {latest_resolution.id} resolve "
                f"({latest_resolution.configured_account_name}) "
                f"request outcome {latest_resolution.state.value.upper()}"
            )
            if latest_resolution.state is IdentityResolutionState.PROVISIONAL_MATCH:
                print("Selection basis: unique normalized account-name match only")
                print("Public biz verification: requires a returned article URL during probe")
            elif latest_resolution.state is IdentityResolutionState.LEGACY_NAME_AND_BIZ_MATCH:
                print("Selection basis: predates the provisional-only searchbiz contract")
            else:
                print("Selection basis: not established")
                print("Public biz verification: NOT_OBSERVED")
            platform_line = _platform_error_evidence_line(
                "Latest identity resolution platform error",
                latest_resolution.platform_error_ret,
                latest_resolution.platform_error_ret_origin,
            )
            if platform_line is not None:
                print(platform_line)
        if latest_attempt is not None:
            target_names = ", ".join(result.account_name for result in latest_attempt.account_results)
            print(
                f"Latest attempt: {latest_attempt_id} {latest_attempt.kind.value} "
                f"({target_names}) request outcome {latest_attempt.state.value.upper()}"
            )
            print(_target_identity_evidence_line(latest_attempt))
            platform_line = _platform_error_evidence_line(
                "Latest attempt platform error",
                latest_attempt.platform_error_ret,
                latest_attempt.platform_error_ret_origin,
            )
            if platform_line is not None:
                print(platform_line)
        for successful_id, successful_account, _successful_biz in latest_successful_attempts:
            print(f"Latest successful attempt: {successful_id} probe ({successful_account})")
        print(
            "Latest article-URL-biz-verified successful probe: "
            + (str(latest_verified_probe_id) if latest_verified_probe_id else "none")
        )
        latest_verified_probe_account = next(
            (
                successful_account
                for successful_id, successful_account, _successful_biz in latest_successful_attempts
                if successful_id == latest_verified_probe_id
            ),
            None,
        )
        compare_next = (
            "Next: compare probe "
            f"{latest_verified_probe_id} for {latest_verified_probe_account} "
            "before making another backend request"
            if latest_verified_probe_id is not None and latest_verified_probe_account is not None
            else None
        )
        if discovery_status.state is DiscoveryGateState.REQUEST_OUTCOME_UNKNOWN:
            print("Impact: a reserved backend request has no trusted terminal outcome")
            print("Preserved: its cooldown remains active and no mapping can be reused")
            if discovery_status.next_request_at is not None:
                _print_local_request_timing(discovery_status.next_request_at)
            if discovery_status.latest_request is not None:
                print(
                    "Next: after that time, run one new identity resolution for "
                    f"{discovery_status.latest_request.account_name}; "
                    "do not repeat the unknown request"
                )
            return 1
        if discovery_status.state is DiscoveryGateState.COOLDOWN:
            print("Impact: no backend request is currently allowed; Mp2RSS remains unchanged")
            assert discovery_status.next_request_at is not None
            _print_local_request_timing(discovery_status.next_request_at)
            if compare_next is not None:
                print(compare_next)
            elif len(ready_accounts) == 1:
                print(f"Next: after that time, run one authorized probe for {ready_accounts[0][0]}")
            elif ready_accounts:
                ready_names = ", ".join(name for name, _resolution_id in ready_accounts)
                print(f"Next: after that time, run one authorized probe for one ready account: {ready_names}")
            else:
                print("Next: after that time, resolve one unresolved account before probing")
            return 1
        if discovery_status.state is DiscoveryGateState.READY_TO_PROBE:
            ready_names = ", ".join(name for name, _resolution_id in ready_accounts)
            print(f"Ready accounts: {ready_names}")
            print("Impact: each listed mapping can be atomically assigned to only one future probe reservation")
            print(compare_next or "Next: run one authorized probe for one listed account; do not schedule it")
            return 0
        print("Impact: no provisional searchbiz mapping is ready; Mp2RSS remains unchanged")
        if (
            discovery_status.latest_request is not None
            and discovery_status.latest_request.state == DiscoveryState.PLATFORM_REJECTED.value
        ):
            print(
                "Next: inspect the rejected request target, authorized account conditions, "
                "and platform contract; do not repeat it unchanged"
            )
        else:
            print(compare_next or "Next: resolve one configured account identity before any probe")
        return 0
    if args.admin_command == "wechat-discovery" and args.wechat_discovery_command == "probe":
        try:
            config = load_discovery_config(args.config)
            account = next((item for item in config.accounts if item.name == args.account), None)
            if account is None:
                raise ValueError(f"unknown configured account: {args.account}")
            if not config.manual_backend_requests_enabled:
                raise ValueError("manual probing is disabled in the discovery config")
            credentials = load_credentials(args.session_file)
        except (OSError, PermissionError, ValueError) as exc:
            print("WeChat discovery probe: UNCONFIGURED")
            print(f"Account: {args.account}")
            print(f"Impact: no request was sent ({type(exc).__name__})")
            print(f"Next: validate {args.config} and the private session file {args.session_file}")
            return 1

        store = DiscoveryStore(args.state_db)
        try:
            started_at = datetime.now(UTC)
            reservation = store.reserve_probe(
                account,
                config=config,
                started_at=started_at,
                requested_page_size=args.count,
            )
        except DiscoveryCooldownActive as exc:
            ready_accounts, _assigned, _invalidated, _unresolved = store.identity_status((account,))
            print("WeChat discovery probe: COOLDOWN")
            print(f"Account: {account.name}")
            print("Impact: no request was sent; Mp2RSS and production items are unchanged")
            _print_local_request_timing(exc.next_request_at)
            if ready_accounts:
                print(f"Next: after that time, rerun probe for {account.name} once")
            else:
                print(f"Next: after that time, resolve {account.name} first; do not rerun probe yet")
            return 1
        except DiscoveryIdentityNoMatch:
            print("WeChat discovery probe: IDENTITY_UNRESOLVED")
            print(f"Account: {account.name}")
            print("Impact: no request was sent; no public biz was used as fakeid")
            print("Next: run one authorized identity resolution for this account")
            return 1
        except (OSError, sqlite3.Error, DiscoveryStoreVersionError) as exc:
            print("WeChat discovery probe: STATE_UNAVAILABLE")
            print(f"Account: {account.name}")
            print(f"Impact: no request was sent ({type(exc).__name__})")
            print("Next: repair or migrate the private shadow state database before retrying")
            return 2
        articles = []
        platform_error_ret = None
        try:
            articles = WeChatAdminClient(credentials).fetch_latest(
                account_name=account.name,
                biz=account.public_biz,
                fakeid=reservation.fakeid,
                count=args.count,
            )
            if articles:
                state = DiscoveryState.SUCCESS
                target_identity_evidence = TargetIdentityEvidence.ARTICLE_URL_PUBLIC_BIZ_VERIFIED
            else:
                state = DiscoveryState.IDENTITY_UNVERIFIED
                target_identity_evidence = TargetIdentityEvidence.EMPTY_ARTICLE_LIST
        except DiscoveryIdentityMismatch:
            state = DiscoveryState.IDENTITY_MISMATCH
            target_identity_evidence = TargetIdentityEvidence.ARTICLE_URL_PUBLIC_BIZ_MISMATCH
        except DiscoveryIdentityUnverified:
            state = DiscoveryState.IDENTITY_UNVERIFIED
            target_identity_evidence = TargetIdentityEvidence.ARTICLE_URL_PUBLIC_BIZ_UNAVAILABLE
        except DiscoveryAuthRequired as exc:
            state = DiscoveryState.AUTH_REQUIRED
            target_identity_evidence = TargetIdentityEvidence.NOT_OBSERVED
            platform_error_ret = exc.platform_error_ret
        except DiscoveryRateLimited as exc:
            state = DiscoveryState.RATE_LIMITED
            target_identity_evidence = TargetIdentityEvidence.NOT_OBSERVED
            platform_error_ret = exc.platform_error_ret
        except DiscoveryPlatformRejected as exc:
            state = DiscoveryState.PLATFORM_REJECTED
            target_identity_evidence = TargetIdentityEvidence.NOT_OBSERVED
            platform_error_ret = exc.platform_error_ret
        except DiscoveryRequestFailed:
            state = DiscoveryState.REQUEST_FAILED
            target_identity_evidence = TargetIdentityEvidence.NOT_OBSERVED
        except DiscoveryResponseInvalid:
            state = DiscoveryState.RESPONSE_INVALID
            target_identity_evidence = TargetIdentityEvidence.NOT_OBSERVED
        finished_at = datetime.now(UTC)
        try:
            completion = store.complete_probe(
                reservation.attempt_id,
                finished_at=finished_at,
                candidates=tuple(articles),
                state=state,
                target_identity_evidence=target_identity_evidence,
                platform_error_ret=platform_error_ret,
            )
        except (OSError, sqlite3.Error, ValueError, DiscoveryStoreVersionError) as exc:
            print("WeChat discovery probe: REQUEST_OUTCOME_UNKNOWN")
            print(f"Account: {account.name}")
            print(f"Reservation: {reservation.attempt_id} probe")
            print(f"Impact: the request result was not trusted ({type(exc).__name__})")
            print("Preserved: its mapping is assigned to this reservation and cooldown remains active")
            print("Next: repair the private shadow database; do not repeat the request")
            return 2

        print(f"WeChat discovery probe: {completion.state.value.upper()}")
        print(f"Account: {account.name}")
        completed_attempt = store.attempt(completion.attempt_id)
        assert completed_attempt is not None
        print(_target_identity_evidence_line(completed_attempt))
        if state is DiscoveryState.SUCCESS:
            print(
                f"Returned articles: {completion.returned_article_count} "
                f"({completion.new_candidate_count} new to shadow state URL set)"
            )
        elif state is DiscoveryState.RATE_LIMITED:
            print("Article result: unavailable — request was rate-limited")
        elif state is DiscoveryState.PLATFORM_REJECTED:
            assert platform_error_ret is not None
            print(f"Platform error: ret={platform_error_ret}")
            print("Article result: unavailable — platform rejected the request")
        else:
            print("Article result: unavailable — request did not produce trusted candidates")
        print(f"Attempt: {completion.attempt_id} probe")
        print(f"Shadow state: {args.state_db}")
        print("Impact: Mp2RSS and production items are unchanged")
        if state is DiscoveryState.SUCCESS:
            print(
                "Next: run ./run.sh admin wechat-discovery compare "
                f"--account '{account.name}' --attempt {completion.attempt_id} "
                "--since '<observation-window-start-with-timezone>'"
            )
            return 0
        if state is DiscoveryState.IDENTITY_UNVERIFIED:
            print("Stored candidates: 0")
            print("Mapping: consumed; resolve again later before another probe")
            print("Next: do not compare this attempt; no public-biz verification exists")
            return 1
        if state is DiscoveryState.IDENTITY_MISMATCH:
            print("Preserved: no candidates were stored; the provisional mapping was invalidated")
            print("Next: inspect the returned article URL identity before resolving again")
        elif state is DiscoveryState.AUTH_REQUIRED:
            print("Preserved: existing shadow candidates remain available; this failure was recorded")
            print(f"Next: run ./run.sh admin wechat-discovery login --session-file {args.session_file}")
        elif state is DiscoveryState.RATE_LIMITED:
            print("Preserved: existing shadow candidates remain available; this failure was recorded")
            next_request_at = backend_request_blocked_until(config, store.latest_backend_request(), now=finished_at)
            assert next_request_at is not None
            _print_local_request_timing(next_request_at, timespec="seconds")
            print("Next: wait until the local safety window ends; do not retry in a loop")
        elif state is DiscoveryState.PLATFORM_REJECTED:
            print("Preserved: no candidates were stored; the provisional mapping was consumed")
            print(
                "Next: inspect the request target, authorized account conditions, and "
                "platform contract; do not repeat the unchanged request"
            )
        elif state is DiscoveryState.REQUEST_FAILED:
            print("Preserved: existing shadow candidates remain available; this failure was recorded")
            print("Next: verify network and HTTP reachability before retrying once")
        else:
            print("Preserved: existing shadow candidates remain available; this failure was recorded")
            print("Next: inspect a redacted response shape before changing the parser")
        return 1
    if args.admin_command == "curate":
        if getattr(args, "admin_curate_command", None) == "rollback-quota":
            return _admin_curate_rollback_quota(args)
        return _curate(args)
    if args.admin_command == "alert-prepare-source-pause":
        state_path = str(Path(args.state_path).expanduser().resolve())
        event_path = str(Path(args.event_path).expanduser().resolve())
        preparation = prepare_alert_source_pause(
            source_id=args.source_id,
            state_path=state_path,
            event_path=event_path,
            dry_run=args.dry_run,
            expected_input_digest=args.expected_input_digest,
        )
        status = str(preparation["status"])
        print(f"Alert source-pause preparation: {status}")
        print(f"Source: {args.source_id}")
        print(f"State path: {state_path}")
        print(f"Event path: {event_path}")
        print("Runbook: docs/operations/monitoring-alerting.md")
        raw_source_ids = preparation.get("source_ids", [])
        source_ids = raw_source_ids if isinstance(raw_source_ids, list) else []
        rendered_ids = ",".join(str(source_id) for source_id in source_ids)
        print(f"Episode source ids: {rendered_ids or '(none)'}")
        print(f"Input digest: {preparation.get('input_digest', '(none)')}")
        print(f"Changed: {'yes' if preparation.get('changed') is True else 'no'}")
        if preparation.get("reason"):
            print(f"Reason: {preparation['reason']}")
        if status == "SEEDABLE":
            print(
                "Next: obtain approval, then rerun without --dry-run and pass "
                "--expected-input-digest with this exact digest"
            )
        elif status == "SEEDED":
            print("Next: rerun this command to confirm READY before changing source configuration")
        elif status == "READY":
            print("Next: source-pause preparation is complete; source configuration is unchanged")
        elif status == "NO_ACTIVE_EPISODE":
            print("Next: no A7 episode identity needs preparation; source configuration is unchanged")
        else:
            print("Next: repair the exact A7 state/ledger identity before pausing the source")
        return 2 if status == "BLOCKED_MISSING_EPISODE_IDENTITY" else 0
    if args.admin_command == "alert-check":
        now = datetime.fromisoformat(args.now) if args.now else None
        catalog = get_pricing(cache_path=args.pricing_cache_path) if args.pricing_cache_path else None
        signals = collect_alert_signals(
            db_path=args.db_path,
            usage_db_path=args.usage_db_path,
            now=now,
            pricing_catalog=catalog,
        )
        test_sender = (
            (lambda text, *, severity="page": send_alert_message(f"{args.message_prefix}{text}", severity=severity))
            if args.message_prefix
            else None
        )
        alert_result = run_alert_state_machine(
            signals,
            state_path=args.state_path,
            event_path=args.event_path,
            now=now,
            send=test_sender,
            serve_plist_path=DEFAULT_SERVE_LAUNCH_AGENT_PATH,
        )
        d3_report = build_cost_report(
            db_path=args.db_path,
            usage_db_path=args.usage_db_path,
            window_days=1,
            now=now,
            pricing_catalog=catalog,
        )
        d3_result = run_pricing_notifications(
            d3_report,
            state_path=args.notification_state_path,
            event_path=args.event_path,
            now=now,
            message_prefix=args.message_prefix,
        )
        # One timestamp per run so the log is forensically usable (when did a
        # rule fire, how long it lasted, correlation with deploys).
        stamp = datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M:%S%z")
        emit = lambda line: print(f"[{stamp}] {line}")  # noqa: E731
        ruleset = alert_result.get("ruleset", [])
        if not isinstance(ruleset, list):
            ruleset = []
        emit(
            f"alert-check ruleset={{{','.join(str(rule) for rule in ruleset)}}} "
            f"sent={alert_result.get('sent_count', 0)}"
        )
        sent = alert_result.get("sent", [])
        if isinstance(sent, list):
            for raw_sent in sent:
                line = _format_alert_send_status(raw_sent)
                if line:
                    emit(line)
        results = alert_result.get("results", [])
        if not isinstance(results, list):
            results = []
        for raw in results:
            if not isinstance(raw, dict):
                continue
            status = (
                "firing"
                if raw.get("firing")
                else (
                    "degraded"
                    if raw.get("evaluation_state") == "degraded"
                    else (
                        "in-progress"
                        if raw.get("evaluation_state") == "in_progress"
                        else ("recorded-scope" if raw.get("evaluation_state") == "scope_limited" else "ok")
                    )
                )
            )
            emit(f"{raw.get('rule_id')} {status} {raw.get('title')} - {raw.get('detail')}")
        d3_sent = d3_result.get("sent", [])
        d3_cleared = d3_result.get("cleared", [])
        emit(
            f"D3 notices={len(d3_sent) if isinstance(d3_sent, list) else 0} "
            f"dedup_clears={len(d3_cleared) if isinstance(d3_cleared, list) else 0}"
        )
        return 0
    if args.admin_command == "cost-report":
        report = build_cost_report(
            db_path=args.db_path,
            usage_db_path=args.usage_db_path,
            window_days=args.window_days,
            pipeline_log_dir=db.PROJECT_ROOT / "logs",
        )
        text = format_cost_report(report)
        if args.send:
            receipt = deliver_cost_report(text)
            print(text)
            print(
                f"cost-report delivery={'sent' if receipt.get('sent') else 'failed'} "
                f"returncode={receipt.get('returncode', 'n/a')}"
            )
            return 0 if receipt.get("sent") else 1
        print(text)
        print("cost-report dry-run: not sent")
        return 0
    if args.admin_command == "cost-audit":
        audit_report = run_cost_audit(
            db_path=args.db_path,
            usage_db_path=args.usage_db_path,
            days=args.days,
        )
        if args.format == "json":
            print(json.dumps(audit_report.json_payload, ensure_ascii=False, sort_keys=True))
            return 0 if audit_report.passed else 1
        lines = audit_report.kv_lines if args.format == "kv" else audit_report.human_lines
        for line in lines:
            print(line)
        return 0 if audit_report.passed else 1
    if args.admin_command == "edgeone":
        return _admin_edgeone(args)
    return _not_implemented("admin")


def _admin_edgeone(args: argparse.Namespace) -> int:
    config = edgeone.load_config()
    if config is None:
        missing = ", ".join(edgeone.missing_env())
        print(f"NOT VERIFIED: EdgeOne cache rules were not checked ({missing} not set).")
        print("Impact: a force-cache rule added in the console would go unnoticed here.")
        print(f"Next: put {', '.join(edgeone.REQUIRED_ENV)} in .env, then re-run.")
        return edgeone.EXIT_NOT_VERIFIED

    if args.edgeone_command == "purge":
        try:
            response = edgeone.purge_urls(config, args.url)
        except Exception as error:  # SDK raises provider-specific errors; all are "not purged".
            print(f"FAILED: purge was not submitted ({error}).")
            print("Next: verify the credential has teo:CreatePurgeTask and the zone id is right.")
            return edgeone.EXIT_DRIFT
        # A JobId comes back even when some targets were rejected, so the failure list --
        # not the presence of a job -- decides whether those URLs were actually purged.
        failures = edgeone.purge_failures(response)
        job_id = response.get("JobId")
        if failures:
            for target, reason in failures:
                print(f"NOT PURGED: {target} -- {reason}")
            print(f"Impact: {len(failures)} of {len(args.url)} URL(s) still serve the cached copy.")
            print("Next: re-submit the failed targets, or clear them in the EdgeOne console.")
            return edgeone.EXIT_DRIFT
        if not job_id:
            print(f"NOT VERIFIED: no JobId came back, so the purge is unconfirmed ({response}).")
            return edgeone.EXIT_NOT_VERIFIED
        print(f"Submitted purge task {job_id} for {len(args.url)} URL(s).")
        print("Next: re-request each URL and confirm eo-cache-status flips to MISS.")
        return edgeone.EXIT_CLEAN

    try:
        rules = edgeone.fetch_rules(config)
        current = edgeone.normalize_rules(rules)
    except Exception as error:
        print(f"NOT VERIFIED: could not read the EdgeOne rules ({error}).")
        print("Impact: drift between the console and this repo remains unchecked.")
        print("Next: re-run; a short or contradictory read is never recorded as a baseline.")
        return edgeone.EXIT_NOT_VERIFIED

    print(f"Read {len(current)} enabled rule(s); caching-relevant branches:")
    for rule in current:
        for branch, nested in edgeone.iter_cache_branches(rule):
            condition = branch.get("Condition") or ""
            paths = edgeone.summarize_paths(condition)
            label = rule.get("RuleName") or "(unnamed)"
            print(f"  - {label}{' [nested]' if nested else ''}: {', '.join(paths) or condition}")

    coverage = edgeone.check_asset_coverage(current, edgeone.pinned_assets())
    for path in coverage.uncovered:
        print(f"UNCOVERED: {path} lets the edge override the origin but has no ?v= in ASSETS.")
    for condition in coverage.unparseable:
        print(f"UNPARSEABLE: could not read the paths out of {condition!r}.")

    for path in coverage.origin_governed:
        print(f"ORIGIN-GOVERNED: {path} defers to the origin's Cache-Control; not checked here.")

    if args.update_snapshot:
        # A snapshot only ever proves "same as last time"; refusing to pin an unsafe state
        # is what stops an uncovered path from becoming the permanently expected baseline.
        if not coverage.verified:
            print("REFUSED: will not pin a state whose force-cached paths are unverified.")
            print("Next: add each path above to scripts/bump_frontend_assets.py ASSETS")
            print("(so it gets a content-derived version), then re-run with --update-snapshot.")
            return edgeone.EXIT_NOT_VERIFIED
        edgeone.write_snapshot(current)
        print(f"Recorded {len(current)} rule(s) to {edgeone.SNAPSHOT_PATH}.")
        return edgeone.EXIT_CLEAN

    recorded = edgeone.load_snapshot()
    if recorded is None:
        print("NOT VERIFIED: no snapshot recorded yet, so there is nothing to compare against.")
        print("Next: review the rules above, then run with --update-snapshot to pin them.")
        return edgeone.EXIT_NOT_VERIFIED

    drift = edgeone.compare_to_snapshot(current, recorded)
    if not drift.has_drift and coverage.verified:
        print("OK: the console rules match the pinned snapshot.")
        return edgeone.EXIT_CLEAN
    if not drift.has_drift:
        print("NOT VERIFIED: the snapshot matches, but the paths above could not be verified.")
        return edgeone.EXIT_NOT_VERIFIED

    for rule in drift.added:
        print(f"DRIFT (console has, repo does not): {rule.get('RuleName') or '(unnamed)'} [{rule.get('RuleId')}]")
    for rule in drift.removed:
        print(f"DRIFT (repo has, console does not): {rule.get('RuleName') or '(unnamed)'} [{rule.get('RuleId')}]")
    if drift.reordered:
        print("DRIFT: same rules, different order -- the engine evaluates top to bottom, so")
        print("the effective cache TTL for an overlapping path may have changed.")
    print("Impact: a newly force-cached path whose asset carries no ?v= can go stale at the")
    print("edge for the full TTL, and no in-repo test can see it (ADR-039).")
    print("Next: if the change is intended, make sure every force-cached path is in")
    print("scripts/bump_frontend_assets.py ASSETS, then re-run with --update-snapshot.")
    return edgeone.EXIT_DRIFT


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai-radar")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("egress-preflight")
    wechat_browser_preflight = subparsers.add_parser("wechat-browser-preflight")
    wechat_browser_preflight.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    wechat_browser_preflight.add_argument("--event-path", default=str(DEFAULT_EVENT_PATH))
    wechat_browser_preflight.add_argument("--resolve-after-pipeline", action="store_true")
    wechat_browser_preflight.add_argument("--pipeline-log")

    fetch_parser = subparsers.add_parser("fetch")
    fetch_parser.add_argument("--sources", help="Override sources.toml path")

    prefilter_parser = subparsers.add_parser("prefilter")
    prefilter_parser.add_argument("--since", default="24h")
    prefilter_parser.add_argument("--limit", type=int)
    prefilter_parser.add_argument("--ruleset")
    prefilter_parser.add_argument("--item-id-file", help="JSON list/object or newline file of item ids to prefilter")

    score_parser = subparsers.add_parser("score")
    score_parser.add_argument("--since", default="24h")
    score_parser.add_argument("--limit", type=int)
    score_parser.add_argument("--ruleset")

    enrich_parser = subparsers.add_parser("enrich")
    enrich_parser.add_argument("--since", default="24h")
    enrich_parser.add_argument("--limit", type=int)
    enrich_parser.add_argument("--ruleset")
    enrich_parser.add_argument(
        "--v2",
        action="store_true",
        help="Use the content-v2 enrichment pipeline (equivalent to AI_RADAR_ENRICH_V2=1); default is v1",
    )
    enrich_parser.add_argument("--curated-run", help="Enrich only items from a curated run id, or 'latest'")
    enrich_parser.add_argument("--item-id-file", help="JSON list/object or newline file of item ids to enrich")
    enrich_parser.add_argument("--workers", type=int, help="Concurrent enrich LLM calls")

    curate_parser = subparsers.add_parser(
        "curate",
        description=(
            "Select the curated set for the latest run. Exit 0: run written. "
            "Exit 2: AI_RADAR_CURATE_SOURCE_QUOTA is invalid (expected e.g. x=0.20,source=0.075 "
            "or off); nothing was run. An empty value means the default quota."
        ),
    )
    curate_parser.add_argument("--threshold", type=float)
    curate_parser.add_argument("--weights")
    curate_parser.add_argument("--ruleset")
    curate_parser.add_argument("--limit", type=int, default=40)
    curate_parser.add_argument("--freshness-quota", type=int, default=36)
    curate_parser.add_argument("--freshness-floor", type=float, default=4.0)
    curate_parser.add_argument(
        "--source-quota",
        type=parse_source_quota,
        default=_SOURCE_QUOTA_FROM_ENV,
    )

    interpret_parser = subparsers.add_parser("interpret")
    interpret_parser.add_argument("--backfill", action="store_true")
    interpret_parser.add_argument("--limit", type=int)
    interpret_parser.add_argument("--assistant-root")
    interpret_parser.add_argument("--user")

    eval_parser = subparsers.add_parser("eval")
    eval_parser.add_argument("--date")
    eval_parser.add_argument("--aihot-markdown")
    eval_parser.add_argument("--output-dir")
    eval_parser.add_argument("--match-scope", choices=("curated", "all-db-url"), default="curated")
    eval_parser.add_argument("--audit", action="store_true")

    add_eval_fit_parser(subparsers)

    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--pre-migrated-db", action="store_true", help=argparse.SUPPRESS)

    performance_probe = subparsers.add_parser(
        "performance-probe",
        epilog=f"launchd install: {LAUNCHD_INSTALL_HINT}",
    )
    performance_probe.add_argument("--origin-url", default="http://127.0.0.1:8000")
    performance_probe.add_argument(
        "--public-url",
        default=os.environ.get("AI_RADAR_PUBLIC_URL", ""),
        help="public site URL to probe; defaults to AI_RADAR_PUBLIC_URL, empty skips the public vantage",
    )
    performance_probe.add_argument("--samples-path", default=str(DEFAULT_SAMPLE_PATH))
    performance_probe.add_argument("--state-path", default=str(DEFAULT_ALERT_STATE_PATH))
    performance_probe.add_argument("--evidence-dir", default=str(DEFAULT_EVIDENCE_DIR))
    performance_probe.add_argument("--pipeline-lock", default=str(DEFAULT_PIPELINE_LOCK_PATH))
    performance_probe.add_argument("--db-path", default=str(db.DEFAULT_DB_PATH))

    performance_remediate = subparsers.add_parser(
        "performance-remediate",
        epilog=f"crontab example: {REMEDIATION_CRONTAB_SAMPLE}",
    )
    performance_remediate.add_argument("--main-checkout", default=str(db.PROJECT_ROOT))
    performance_remediate.add_argument("--alert-state-path", default=str(DEFAULT_ALERT_STATE_PATH))
    performance_remediate.add_argument("--performance-evidence-dir", default=str(DEFAULT_EVIDENCE_DIR))
    performance_remediate.add_argument("--worker-root", default=str(DEFAULT_REMEDIATION_ROOT / "worktrees"))
    performance_remediate.add_argument("--remediation-state-path", default=str(DEFAULT_REMEDIATION_STATE_PATH))
    performance_remediate.add_argument("--lock-path", default=str(DEFAULT_REMEDIATION_LOCK_PATH))
    performance_remediate.add_argument(
        "--remediation-evidence-dir",
        default=str(DEFAULT_REMEDIATION_EVIDENCE_DIR),
    )
    performance_remediate.add_argument("--production-db-path", default=str(db.DEFAULT_DB_PATH))
    performance_remediate.add_argument("--codex-binary", default="codex")
    performance_remediate.add_argument(
        "--timeout-seconds",
        type=_remediation_timeout,
        default=DEFAULT_TIMEOUT_SECONDS,
    )

    admin = subparsers.add_parser("admin")
    admin_subparsers = admin.add_subparsers(dest="admin_command", required=True)
    db_parser = admin_subparsers.add_parser("db")
    db_subparsers = db_parser.add_subparsers(dest="db_command", required=True)
    db_subparsers.add_parser("migrate")
    db_subparsers.add_parser(
        "backfill-links",
        help=(
            "Populate item_links for existing items. Resumable. Until this "
            "reports complete, related discussions fall back to the slow scan."
        ),
    )
    db_checkpoint = db_subparsers.add_parser("checkpoint")
    db_checkpoint.add_argument("--db-path", default=str(db.DEFAULT_DB_PATH))
    for command in ("retain", "slim"):
        db_retention = db_subparsers.add_parser(command)
        db_retention.add_argument("--keep-days", type=_non_negative_int, default=DEFAULT_KEEP_DAYS)
        db_retention.add_argument("--dry-run", action="store_true")
        db_retention.add_argument("--db-path")
    edgeone_parser = admin_subparsers.add_parser("edgeone")
    edgeone_subparsers = edgeone_parser.add_subparsers(dest="edgeone_command", required=True)
    edgeone_check = edgeone_subparsers.add_parser("check")
    edgeone_check.add_argument("--update-snapshot", action="store_true")
    edgeone_purge = edgeone_subparsers.add_parser("purge")
    edgeone_purge.add_argument("--url", action="append", required=True)
    sources_parser = admin_subparsers.add_parser("sources")
    sources_subparsers = sources_parser.add_subparsers(dest="sources_command", required=True)
    sources_subparsers.add_parser("reload")
    sources_subparsers.add_parser("list")
    wechat_avatar_parser = admin_subparsers.add_parser("wechat-avatar")
    wechat_avatar_subparsers = wechat_avatar_parser.add_subparsers(dest="wechat_avatar_command", required=True)
    wechat_avatar_refresh = wechat_avatar_subparsers.add_parser("refresh")
    wechat_avatar_refresh.add_argument("--account", required=True)
    wechat_avatar_refresh.add_argument("--db-path", default=str(db.DEFAULT_DB_PATH))
    wechat_kb_parser = admin_subparsers.add_parser("wechat-kb")
    wechat_kb_subparsers = wechat_kb_parser.add_subparsers(dest="wechat_kb_command", required=True)
    wechat_kb_import = wechat_kb_subparsers.add_parser("import")
    wechat_kb_import.add_argument("--dry-run", action="store_true")
    wechat_kb_import.add_argument("--limit", type=_positive_int)
    wechat_kb_import.add_argument(
        "--assistant-root",
        default=os.environ.get("AI_ASSISTANT_ROOT", str(Path.home() / "research" / "ai-assistant")),
    )
    wechat_kb_import.add_argument("--user", default=os.environ.get("AI_RADAR_INTERPRET_USER", "dong_lin"))
    wechat_kb_import.add_argument("--db-path", default=str(db.DEFAULT_DB_PATH))
    x_media_parser = admin_subparsers.add_parser("x-media")
    x_media_subparsers = x_media_parser.add_subparsers(dest="x_media_command", required=True)
    x_media_backfill_parser = x_media_subparsers.add_parser("backfill")
    x_media_backfill_parser.add_argument(
        "--limit",
        type=_positive_int,
        default=None,
        help="cap how many items this run looks up (X bills per returned post)",
    )
    x_media_backfill_parser.add_argument(
        "--dry-run", action="store_true", help="report the candidate count without issuing any request"
    )
    x_media_backfill_parser.add_argument("--db-path", default=str(db.DEFAULT_DB_PATH))
    wechat_discovery_parser = admin_subparsers.add_parser("wechat-discovery")
    wechat_discovery_subparsers = wechat_discovery_parser.add_subparsers(dest="wechat_discovery_command", required=True)
    wechat_discovery_status = wechat_discovery_subparsers.add_parser("status")
    wechat_discovery_status.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    wechat_discovery_status.add_argument("--state-db", default=str(DEFAULT_STATE_DB_PATH))
    wechat_discovery_status.add_argument("--session-file", default=str(DEFAULT_SESSION_PATH))
    wechat_discovery_migrate = wechat_discovery_subparsers.add_parser("migrate")
    wechat_discovery_migrate.add_argument("--state-db", default=str(DEFAULT_STATE_DB_PATH))
    wechat_discovery_compare = wechat_discovery_subparsers.add_parser("compare")
    wechat_discovery_compare.add_argument("--account", required=True)
    wechat_discovery_compare.add_argument("--attempt", type=_positive_int, required=True)
    wechat_discovery_compare.add_argument("--since", required=True)
    wechat_discovery_compare.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    wechat_discovery_compare.add_argument("--state-db", default=str(DEFAULT_STATE_DB_PATH))
    wechat_discovery_compare.add_argument("--db-path", default=str(db.DEFAULT_DB_PATH))
    wechat_discovery_resolve = wechat_discovery_subparsers.add_parser("resolve")
    wechat_discovery_resolve.add_argument("--account", required=True)
    wechat_discovery_resolve.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    wechat_discovery_resolve.add_argument("--state-db", default=str(DEFAULT_STATE_DB_PATH))
    wechat_discovery_resolve.add_argument("--session-file", default=str(DEFAULT_SESSION_PATH))
    wechat_discovery_probe = wechat_discovery_subparsers.add_parser("probe")
    wechat_discovery_probe.add_argument("--account", required=True)
    wechat_discovery_probe.add_argument("--count", type=int, choices=range(1, 21), default=5)
    wechat_discovery_probe.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    wechat_discovery_probe.add_argument("--state-db", default=str(DEFAULT_STATE_DB_PATH))
    wechat_discovery_probe.add_argument("--session-file", default=str(DEFAULT_SESSION_PATH))
    wechat_discovery_login = wechat_discovery_subparsers.add_parser("login")
    wechat_discovery_login.add_argument("--session-file", default=str(DEFAULT_SESSION_PATH))
    wechat_discovery_login.add_argument("--browser-profile", default=str(DEFAULT_BROWSER_PROFILE))
    wechat_discovery_login.add_argument("--timeout-seconds", type=_positive_int, default=300)
    admin_curate = admin_subparsers.add_parser(
        "curate",
        description=(
            "Select the curated set for the latest run. Exit 0: run written. "
            "Exit 2: AI_RADAR_CURATE_SOURCE_QUOTA is invalid (expected e.g. x=0.20,source=0.075 "
            "or off); nothing was run. An empty value means the default quota."
        ),
    )
    admin_curate.add_argument("--threshold", type=float)
    admin_curate.add_argument("--weights")
    admin_curate.add_argument("--ruleset")
    admin_curate.add_argument("--ruleset-suffix")
    admin_curate.add_argument("--limit", type=int, default=40)
    admin_curate.add_argument("--freshness-quota", type=int, default=36)
    admin_curate.add_argument("--freshness-floor", type=float, default=4.0)
    admin_curate.add_argument(
        "--source-quota",
        type=parse_source_quota,
        default=_SOURCE_QUOTA_FROM_ENV,
    )
    admin_curate_subparsers = admin_curate.add_subparsers(dest="admin_curate_command")
    rollback_quota = admin_curate_subparsers.add_parser(
        "rollback-quota",
        description=(
            "Remove quota-only curated rows (reason_json.source_quota.baseline_selected=false) "
            "from every curation run whose id >= SINCE and that has a quota shadow, then "
            "renumber ranks, recalibrate display scores and clear cached summaries. "
            "Progress goes to stdout, failure diagnostics to stderr. "
            "Exit 0: every matching run processed (or none matched). "
            "Exit 1: stopped at a run that could not be processed; earlier runs keep their "
            "changes, later matching runs are untouched; rerun after fixing it. "
            "Counts are curated rows (rows_removed/rows_kept) per run."
        ),
    )
    rollback_quota.add_argument("--since", required=True, help="First run id to include (lexical order)")
    rollback_quota.add_argument(
        "--dry-run",
        action="store_true",
        help="Read-only preview: print counts, change no curated rows (SQLite may still create empty -wal/-shm sidecars)",
    )
    admin_subparsers.add_parser("rerun-eval")
    alert_prepare_source_pause = admin_subparsers.add_parser(
        "alert-prepare-source-pause"
    )
    alert_prepare_source_pause.add_argument("--source-id", required=True)
    alert_prepare_source_pause.add_argument("--state-path", required=True)
    alert_prepare_source_pause.add_argument("--event-path", required=True)
    alert_prepare_source_pause.add_argument("--dry-run", action="store_true")
    alert_prepare_source_pause.add_argument("--expected-input-digest")
    alert_check = admin_subparsers.add_parser("alert-check")
    alert_check.add_argument("--state-path", default=str(db.PROJECT_ROOT / "data" / "alert-state.json"))
    alert_check.add_argument("--event-path", default=str(db.PROJECT_ROOT / "data" / "alert-events.jsonl"))
    alert_check.add_argument(
        "--notification-state-path", default=str(db.PROJECT_ROOT / "data" / "pricing-notification-state.json")
    )
    alert_check.add_argument("--db-path", default=str(db.DEFAULT_DB_PATH))
    alert_check.add_argument("--usage-db-path")
    alert_check.add_argument("--pricing-cache-path")
    alert_check.add_argument("--message-prefix", default="")
    alert_check.add_argument("--now", help="Inject an ISO timestamp for deterministic smoke tests")
    cost_report = admin_subparsers.add_parser("cost-report")
    cost_report.add_argument("--db-path", default=str(db.DEFAULT_DB_PATH))
    cost_report.add_argument("--usage-db-path")
    cost_report.add_argument("--window-days", type=_positive_int)
    cost_report_mode = cost_report.add_mutually_exclusive_group()
    cost_report_mode.add_argument("--send", action="store_true")
    cost_report_mode.add_argument("--dry-run", action="store_true")
    cost_audit = admin_subparsers.add_parser(
        "cost-audit",
        help="Reconcile cost calculations against the loaded pricing catalog",
    )
    cost_audit.add_argument("--db-path", default=str(db.DEFAULT_DB_PATH))
    cost_audit.add_argument("--usage-db-path")
    cost_audit.add_argument("--days", type=int, default=30)
    cost_audit.add_argument("--format", choices=("human", "kv", "json"), default="human")

    return parser


def main() -> None:
    _load_runtime_env()
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "egress-preflight":
        raise SystemExit(_egress_preflight())
    if args.command == "wechat-browser-preflight":
        raise SystemExit(
            _wechat_browser_preflight(
                state_path=args.state_path,
                event_path=args.event_path,
                resolve_after_pipeline=args.resolve_after_pipeline,
                pipeline_log=args.pipeline_log,
            )
        )
    if args.command == "fetch":
        raise SystemExit(_fetch(args))
    if args.command == "prefilter":
        raise SystemExit(_prefilter(args))
    if args.command == "score":
        raise SystemExit(_score(args))
    if args.command == "enrich":
        raise SystemExit(_enrich(args))
    if args.command == "curate":
        raise SystemExit(_curate(args))
    if args.command == "interpret":
        raise SystemExit(_interpret(args))
    if args.command == "eval":
        raise SystemExit(_eval(args))
    if args.command == "eval-fit":
        raise SystemExit(run_eval_fit(args))
    if args.command == "serve":
        raise SystemExit(_serve(args))
    if args.command == "performance-probe":
        raise SystemExit(_performance_probe(args))
    if args.command == "performance-remediate":
        raise SystemExit(_performance_remediate(args))
    if args.command == "admin":
        raise SystemExit(_admin(args))
    raise SystemExit(_not_implemented(args.command))


if __name__ == "__main__":
    main()
