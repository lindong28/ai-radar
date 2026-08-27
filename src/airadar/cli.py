from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from . import db, runtime_env
from .admin import edgeone
from .admin.alerts import (
    DEFAULT_SERVE_LAUNCH_AGENT_PATH,
    collect_alert_signals,
    run_alert_state_machine,
    run_pricing_notifications,
    send_alert_message,
)
from .admin.cost_audit import run_cost_audit
from .admin.cost_report import build_cost_report, deliver_cost_report, format_cost_report
from .admin.metrics import SHANGHAI_TZ
from .admin.x_media_backfill import backfill_x_media
from .curator.precompute import (
    DEFAULT_KEEP_DAYS,
    RetentionStats,
    curated_summary_retention_stats,
    precompute_curated_summaries,
    retain_curated_summaries,
)
from .curator.select import curate
from .curator.weights import load_weights
from .egress import EgressPreflightError, require_selector_policy
from .enrich.runner import run_enrich
from .eval.judge import DEFAULT_AIHOT_MARKDOWN, DEFAULT_OUTPUT_DIR, run_eval
from .fetcher.runner import fetch_all, refresh_wechat_avatar, reload_sources
from .interpret.runner import run_interpret
from .performance.journey_monitor import (
    DEFAULT_ALERT_STATE_PATH,
    DEFAULT_EVIDENCE_DIR,
    DEFAULT_PIPELINE_LOCK_PATH,
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
    print(
        "egress-preflight status=healthy "
        f"policy_id={policy.policy_id} policy_sha256={policy.policy_sha256}"
    )
    return 0


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
            print(
                f"eligible_rows={stats.eligible_rows} "
                f"logical_summary_bytes={stats.logical_summary_bytes}"
            )
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
        summary = run_enrich(
            conn,
            since=args.since,
            limit=args.limit,
            ruleset_version=args.ruleset,
            item_ids=item_ids,
            workers=workers,
            progress_callback=progress,
        )
    print(f"enrich processed={summary.processed}, errors={summary.errors}")
    return 0


def _curate(args: argparse.Namespace) -> int:
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
        )
        precompute_curated_summaries(conn, run.id)
        retain_curated_summaries(conn, DEFAULT_KEEP_DAYS)
    print(f"curate run_id={run.id} selected={len(run.output_curated_ids)} threshold={run.threshold}")
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
            "resolve a provisional account mapping, then run a new authorized "
            "identity-checking probe after cooldown"
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
            next_step_override=(
                "run ./run.sh admin wechat-discovery migrate "
                f"--state-db {args.state_db}"
            ),
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
            source = conn.execute(
                "SELECT enabled FROM sources WHERE id='wx_mp2rss'"
            ).fetchone()
            if source is None or int(source["enabled"]) != 1:
                raise ShadowNotComparable("the production wx_mp2rss source is absent or disabled")
            authors = {
                str(row["author"])
                for row in conn.execute(
                    "SELECT DISTINCT author FROM items "
                    "WHERE source_id='wx_mp2rss' AND author IS NOT NULL"
                )
            }
            matched_authors = sorted(
                author
                for author in authors
                if normalized_account_name(author) == normalized_account_name(account.name)
            )
            if not matched_authors:
                raise ShadowNotComparable(
                    "the configured account name has no normalized production author bucket"
                )
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
            attempt_count = int(
                conn.execute("SELECT COUNT(*) FROM discovery_attempts").fetchone()[0]
            )
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            resolution_count = (
                int(
                    conn.execute(
                        "SELECT COUNT(*) FROM identity_resolution_attempts"
                    ).fetchone()[0]
                )
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
                        "SELECT url FROM discovery_attempt_candidates "
                        "WHERE probe_attempt_id=?",
                        (possible_row[0],),
                    ).fetchall()
                    try:
                        if candidate_rows and all(
                            observed_article_biz(str(candidate_row[0]))
                            == str(possible_row[2])
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
        (
            int(verified_row[0])
            if verified_row is not None
            else 0 if version in {6, 7, 8, 9, 10} else None
        ),
        str(verified_row[1]) if verified_row is not None else None,
        (
            int(latest_platform_error[0])
            if latest_platform_error is not None
            and latest_platform_error[0] is not None
            else None
        ),
        (
            str(latest_platform_error[1])
            if latest_platform_error is not None
            else None
        ),
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
    print(
        "Request timing basis: local safety policy; "
        "not a published WeChat platform window"
    )
    local_time = next_request_at.astimezone(SHANGHAI_TZ)
    rendered = local_time.isoformat(timespec=timespec) if timespec else local_time.isoformat()
    print(f"Next request allowed by local policy after: {rendered}")


def _target_identity_evidence_line(attempt: DiscoveryAttempt) -> str:
    evidence = attempt.target_identity_evidence
    if evidence is TargetIdentityEvidence.EMPTY_ARTICLE_LIST:
        return (
            "Target identity: NOT_VERIFIED — valid empty article list contained no "
            "public article URL"
        )
    if evidence is TargetIdentityEvidence.ARTICLE_URL_PUBLIC_BIZ_UNAVAILABLE:
        return (
            "Target identity: NOT_VERIFIED — returned article URL did not expose a "
            "unique public biz"
        )
    if evidence is TargetIdentityEvidence.ARTICLE_URL_PUBLIC_BIZ_MISMATCH:
        return (
            "Target identity: MISMATCH — returned article URL public biz contradicted "
            "configured target"
        )
    if evidence is TargetIdentityEvidence.ARTICLE_URL_PUBLIC_BIZ_VERIFIED:
        return (
            "Target identity: VERIFIED — all returned article URLs matched configured "
            "public biz"
        )
    if evidence is TargetIdentityEvidence.PREDATES_V7_VERIFICATION:
        return (
            "Target identity: NOT_VERIFIED — probe predates persisted article-URL "
            "public-biz verification"
        )
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
        print(
            "Impact: private shadow state only; no backend request, Mp2RSS, production "
            "item, or scheduler changed"
        )
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
            resolution_id = store.reserve_identity_resolution(
                account, config=config, started_at=started_at
            )
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
            candidates = WeChatAdminClient(credentials).search_accounts(
                account_name=account.name
            )
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
            print(
                f"Next: after that time, run one authorized probe for {account.name}"
            )
            return 0
        if resolution_state is IdentityResolutionState.AUTH_REQUIRED:
            print("Next: sign in again only with the authorized WeChat admin account")
        elif resolution_state is IdentityResolutionState.RATE_LIMITED:
            next_request_at = backend_request_blocked_until(
                config, store.latest_backend_request(), now=finished_at
            )
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
            disabled_evidence: (
                tuple[str, int, int, int | None, str | None, int | None, str | None]
                | None
            ) = None
            if not config.manual_backend_requests_enabled:
                disabled_evidence = _readonly_wechat_discovery_evidence(args.state_db)
                latest_attempt_id = None
                latest_attempt = None
                latest_successful_attempts = ()
                latest_request = None
                latest_resolution = None
                ready_accounts = ()
                identity_counts = (0, 0, 0, len(config.accounts))
                latest_verified_probe_id = (
                    disabled_evidence[3]
                    if disabled_evidence[3] not in {None, 0}
                    else None
                )
            else:
                status_store = DiscoveryStore(args.state_db)
                latest_attempt_id = status_store.latest_attempt_id()
                latest_attempt = (
                    status_store.attempt(latest_attempt_id)
                    if latest_attempt_id is not None
                    else None
                )
                latest_successful_attempts = status_store.latest_successful_attempts()
                latest_request = status_store.latest_backend_request()
                latest_resolution = status_store.latest_identity_resolution()
                ready_accounts, assigned_count, invalidated_count, unresolved_count = (
                    status_store.identity_status(config.accounts)
                )
                identity_counts = (
                    len(ready_accounts),
                    assigned_count,
                    invalidated_count,
                    unresolved_count,
                )
                latest_verified_probe_id = (
                    status_store.latest_identity_verified_successful_probe_id()
                )
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
            print(
                "Next: run ./run.sh admin wechat-discovery migrate "
                f"--state-db {args.state_db}"
            )
            return 2
        except (OSError, ValueError, sqlite3.Error) as exc:
            print("WeChat discovery: UNAVAILABLE")
            print(f"Impact: status could not be determined ({type(exc).__name__})")
            print("Next: validate the discovery config and state database")
            return 2

        print(f"WeChat discovery request gate: {discovery_status.state.value.upper()}")
        print(f"Accounts: {discovery_status.account_count} configured")
        readiness_message = (
            "NOT_VALIDATED — article-URL public-biz-verified probe exists; "
            "explicit comparison required"
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
            print(
                "Next: no action while disabled; explicitly enable only for an "
                "authorized one-shot request"
            )
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
            target_names = ", ".join(
                result.account_name for result in latest_attempt.account_results
            )
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
                for successful_id, successful_account, _successful_biz
                in latest_successful_attempts
                if successful_id == latest_verified_probe_id
            ),
            None,
        )
        compare_next = (
            "Next: compare probe "
            f"{latest_verified_probe_id} for {latest_verified_probe_account} "
            "before making another backend request"
            if latest_verified_probe_id is not None
            and latest_verified_probe_account is not None
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
                print(
                    "Next: after that time, run one authorized probe for "
                    f"{ready_accounts[0][0]}"
                )
            elif ready_accounts:
                ready_names = ", ".join(name for name, _resolution_id in ready_accounts)
                print(
                    "Next: after that time, run one authorized probe for one ready account: "
                    f"{ready_names}"
                )
            else:
                print("Next: after that time, resolve one unresolved account before probing")
            return 1
        if discovery_status.state is DiscoveryGateState.READY_TO_PROBE:
            ready_names = ", ".join(name for name, _resolution_id in ready_accounts)
            print(f"Ready accounts: {ready_names}")
            print(
                "Impact: each listed mapping can be atomically assigned to only one future "
                "probe reservation"
            )
            print(
                compare_next
                or "Next: run one authorized probe for one listed account; do not schedule it"
            )
            return 0
        print("Impact: no provisional searchbiz mapping is ready; Mp2RSS remains unchanged")
        if (
            discovery_status.latest_request is not None
            and discovery_status.latest_request.state
            == DiscoveryState.PLATFORM_REJECTED.value
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
            ready_accounts, _assigned, _invalidated, _unresolved = store.identity_status(
                (account,)
            )
            print("WeChat discovery probe: COOLDOWN")
            print(f"Account: {account.name}")
            print("Impact: no request was sent; Mp2RSS and production items are unchanged")
            _print_local_request_timing(exc.next_request_at)
            if ready_accounts:
                print(f"Next: after that time, rerun probe for {account.name} once")
            else:
                print(
                    f"Next: after that time, resolve {account.name} first; "
                    "do not rerun probe yet"
                )
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
                target_identity_evidence = (
                    TargetIdentityEvidence.ARTICLE_URL_PUBLIC_BIZ_VERIFIED
                )
            else:
                state = DiscoveryState.IDENTITY_UNVERIFIED
                target_identity_evidence = TargetIdentityEvidence.EMPTY_ARTICLE_LIST
        except DiscoveryIdentityMismatch:
            state = DiscoveryState.IDENTITY_MISMATCH
            target_identity_evidence = (
                TargetIdentityEvidence.ARTICLE_URL_PUBLIC_BIZ_MISMATCH
            )
        except DiscoveryIdentityUnverified:
            state = DiscoveryState.IDENTITY_UNVERIFIED
            target_identity_evidence = (
                TargetIdentityEvidence.ARTICLE_URL_PUBLIC_BIZ_UNAVAILABLE
            )
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
            print(
                "Preserved: its mapping is assigned to this reservation and cooldown remains active"
            )
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
            print(
                "Preserved: no candidates were stored; the provisional mapping was "
                "invalidated"
            )
            print("Next: inspect the returned article URL identity before resolving again")
        elif state is DiscoveryState.AUTH_REQUIRED:
            print("Preserved: existing shadow candidates remain available; this failure was recorded")
            print(
                "Next: run ./run.sh admin wechat-discovery login "
                f"--session-file {args.session_file}"
            )
        elif state is DiscoveryState.RATE_LIMITED:
            print("Preserved: existing shadow candidates remain available; this failure was recorded")
            next_request_at = backend_request_blocked_until(
                config, store.latest_backend_request(), now=finished_at
            )
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
        return _curate(args)
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
            (lambda text, *, severity="page": send_alert_message(
                f"{args.message_prefix}{text}", severity=severity
            ))
            if args.message_prefix else None
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
                        else (
                            "recorded-scope"
                            if raw.get("evaluation_state") == "scope_limited"
                            else "ok"
                        )
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
    enrich_parser.add_argument("--curated-run", help="Enrich only items from a curated run id, or 'latest'")
    enrich_parser.add_argument("--item-id-file", help="JSON list/object or newline file of item ids to enrich")
    enrich_parser.add_argument("--workers", type=int, help="Concurrent enrich LLM calls")

    curate_parser = subparsers.add_parser("curate")
    curate_parser.add_argument("--threshold", type=float)
    curate_parser.add_argument("--weights")
    curate_parser.add_argument("--ruleset")
    curate_parser.add_argument("--limit", type=int, default=40)
    curate_parser.add_argument("--freshness-quota", type=int, default=36)
    curate_parser.add_argument("--freshness-floor", type=float, default=4.0)

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
    x_media_parser = admin_subparsers.add_parser("x-media")
    x_media_subparsers = x_media_parser.add_subparsers(dest="x_media_command", required=True)
    x_media_backfill_parser = x_media_subparsers.add_parser("backfill")
    x_media_backfill_parser.add_argument("--limit", type=_positive_int, default=None,
                                         help="cap how many items this run looks up (X bills per returned post)")
    x_media_backfill_parser.add_argument("--dry-run", action="store_true",
                                         help="report the candidate count without issuing any request")
    x_media_backfill_parser.add_argument("--db-path", default=str(db.DEFAULT_DB_PATH))
    wechat_discovery_parser = admin_subparsers.add_parser("wechat-discovery")
    wechat_discovery_subparsers = wechat_discovery_parser.add_subparsers(
        dest="wechat_discovery_command", required=True
    )
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
    admin_curate = admin_subparsers.add_parser("curate")
    admin_curate.add_argument("--threshold", type=float)
    admin_curate.add_argument("--weights")
    admin_curate.add_argument("--ruleset")
    admin_curate.add_argument("--ruleset-suffix")
    admin_curate.add_argument("--limit", type=int, default=40)
    admin_curate.add_argument("--freshness-quota", type=int, default=36)
    admin_curate.add_argument("--freshness-floor", type=float, default=4.0)
    admin_subparsers.add_parser("rerun-eval")
    alert_check = admin_subparsers.add_parser("alert-check")
    alert_check.add_argument("--state-path", default=str(db.PROJECT_ROOT / "data" / "alert-state.json"))
    alert_check.add_argument("--event-path", default=str(db.PROJECT_ROOT / "data" / "alert-events.jsonl"))
    alert_check.add_argument("--notification-state-path", default=str(db.PROJECT_ROOT / "data" / "pricing-notification-state.json"))
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
