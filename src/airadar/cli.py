from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from time import monotonic

from dotenv import dotenv_values

from . import db
from .admin.alerts import collect_alert_signals, run_alert_state_machine
from .admin.metrics import SHANGHAI_TZ
from .curator.select import curate
from .curator.weights import load_weights
from .enrich.runner import run_enrich
from .eval.judge import DEFAULT_AIHOT_MARKDOWN, DEFAULT_OUTPUT_DIR, run_eval
from .fetcher.runner import fetch_all, reload_sources
from .interpret.runner import run_interpret
from .prefilter.runner import run_prefilter
from .scorer.runner import run_scoring


def _load_runtime_env(
    *,
    project_env: Path | None = None,
    shared_env: Path | None = None,
) -> None:
    project_env = project_env or db.PROJECT_ROOT / ".env"
    shared_env = shared_env or Path.home() / ".claude" / ".env"

    values: dict[str, str] = {}
    for env_path in (shared_env, project_env):
        if not env_path.exists():
            continue
        for key, value in dotenv_values(env_path).items():
            if value is not None:
                values[key] = value

    for key, value in values.items():
        os.environ.setdefault(key, value)


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
        from .curator.precompute import precompute_curated_summaries

        precompute_curated_summaries(conn, run.id)
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
    from .web.app import serve

    serve(port=args.port, host=args.host)
    return 0


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
        print(f"migrated {db.resolve_db_path()}")
        return 0
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
    if args.admin_command == "curate":
        return _curate(args)
    if args.admin_command == "alert-check":
        signals = collect_alert_signals()
        result = run_alert_state_machine(signals, state_path=args.state_path)
        # One timestamp per run so the log is forensically usable (when did a
        # rule fire, how long it lasted, correlation with deploys).
        stamp = datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M:%S%z")
        emit = lambda line: print(f"[{stamp}] {line}")  # noqa: E731
        ruleset = result.get("ruleset", [])
        if not isinstance(ruleset, list):
            ruleset = []
        emit(f"alert-check ruleset={{{','.join(str(rule) for rule in ruleset)}}} sent={result.get('sent_count', 0)}")
        sent = result.get("sent", [])
        if isinstance(sent, list):
            for raw_sent in sent:
                line = _format_alert_send_status(raw_sent)
                if line:
                    emit(line)
        results = result.get("results", [])
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
    interpret_parser.add_argument("--user", default="dong_lin")

    eval_parser = subparsers.add_parser("eval")
    eval_parser.add_argument("--date")
    eval_parser.add_argument("--aihot-markdown")
    eval_parser.add_argument("--output-dir")
    eval_parser.add_argument("--match-scope", choices=("curated", "all-db-url"), default="curated")
    eval_parser.add_argument("--audit", action="store_true")

    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.add_argument("--host", default="127.0.0.1")

    admin = subparsers.add_parser("admin")
    admin_subparsers = admin.add_subparsers(dest="admin_command", required=True)
    db_parser = admin_subparsers.add_parser("db")
    db_subparsers = db_parser.add_subparsers(dest="db_command", required=True)
    db_subparsers.add_parser("migrate")
    sources_parser = admin_subparsers.add_parser("sources")
    sources_subparsers = sources_parser.add_subparsers(dest="sources_command", required=True)
    sources_subparsers.add_parser("reload")
    sources_subparsers.add_parser("list")
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
    if args.command == "admin":
        raise SystemExit(_admin(args))
    raise SystemExit(_not_implemented(args.command))


if __name__ == "__main__":
    main()
