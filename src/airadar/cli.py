from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import monotonic

from . import db, runtime_env
from .admin.alerts import collect_alert_signals, run_alert_state_machine
from .admin.metrics import SHANGHAI_TZ
from .curator.precompute import (
    DEFAULT_KEEP_DAYS,
    RetentionStats,
    curated_summary_retention_stats,
    precompute_curated_summaries,
    retain_curated_summaries,
)
from .curator.select import curate
from .curator.weights import load_weights
from .enrich.runner import run_enrich
from .eval.judge import DEFAULT_AIHOT_MARKDOWN, DEFAULT_OUTPUT_DIR, run_eval
from .fetcher.runner import fetch_all, refresh_wechat_avatar, reload_sources
from .interpret.runner import run_interpret
from .performance.journey_monitor import (
    DEFAULT_ALERT_STATE_PATH,
    DEFAULT_EVIDENCE_DIR,
    DEFAULT_PIPELINE_LOCK_DIR,
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
from .scorer.runner import run_scoring

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
                run = conn.execute("SELECT id FROM curation_runs ORDER BY created_at DESC LIMIT 1").fetchone()
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
        pipeline_lock_dir=Path(args.pipeline_lock),
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


def _admin(args: argparse.Namespace) -> int:
    if args.admin_command == "db" and args.db_command == "migrate":
        db.migrate()
        from .llm_usage import migrate_usage_db

        usage_path = migrate_usage_db(main_db_path=db.resolve_db_path())
        print(f"migrated {db.resolve_db_path()}")
        print(f"migrated llm_usage {usage_path}")
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
    if args.admin_command == "wechat-avatar" and args.wechat_avatar_command == "refresh":
        with db.get_conn(args.db_path) as conn:
            avatar_url = refresh_wechat_avatar(conn, args.account)
            conn.commit()
        if avatar_url:
            print(f"wechat-avatar account={args.account} avatar_url={avatar_url}")
            return 0
        print(f"wechat-avatar account={args.account} avatar_url=")
        return 1
    if args.admin_command == "curate":
        return _curate(args)
    if args.admin_command == "alert-check":
        signals = collect_alert_signals()
        alert_result = run_alert_state_machine(signals, state_path=args.state_path)
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
            status = "firing" if raw.get("firing") else "ok"
            emit(f"{raw.get('rule_id')} {status} {raw.get('title')} - {raw.get('detail')}")
        return 0
    return _not_implemented("admin")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai-radar")
    subparsers = parser.add_subparsers(dest="command", required=True)

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
    performance_probe.add_argument("--pipeline-lock", default=str(DEFAULT_PIPELINE_LOCK_DIR))
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
    db_checkpoint = db_subparsers.add_parser("checkpoint")
    db_checkpoint.add_argument("--db-path", default=str(db.DEFAULT_DB_PATH))
    for command in ("retain", "slim"):
        db_retention = db_subparsers.add_parser(command)
        db_retention.add_argument("--keep-days", type=_non_negative_int, default=DEFAULT_KEEP_DAYS)
        db_retention.add_argument("--dry-run", action="store_true")
        db_retention.add_argument("--db-path")
    sources_parser = admin_subparsers.add_parser("sources")
    sources_subparsers = sources_parser.add_subparsers(dest="sources_command", required=True)
    sources_subparsers.add_parser("reload")
    sources_subparsers.add_parser("list")
    wechat_avatar_parser = admin_subparsers.add_parser("wechat-avatar")
    wechat_avatar_subparsers = wechat_avatar_parser.add_subparsers(dest="wechat_avatar_command", required=True)
    wechat_avatar_refresh = wechat_avatar_subparsers.add_parser("refresh")
    wechat_avatar_refresh.add_argument("--account", required=True)
    wechat_avatar_refresh.add_argument("--db-path", default=str(db.DEFAULT_DB_PATH))
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

    return parser


def main() -> None:
    _load_runtime_env()
    parser = build_parser()
    args = parser.parse_args()
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
