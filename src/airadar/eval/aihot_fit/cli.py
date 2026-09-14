"""``ai-radar eval-fit`` three-layer evaluation workflow wiring and dispatch."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ... import db
from .common import (
    DEFAULT_EVALSET_DIR,
    DEFAULT_EVALSET_DIR_V2,
    DEFAULT_RUNS_DIR,
    DEFAULT_WORKERS,
    prune_old_runs,
    sha256_file,
)
from .governance import DEFAULT_LEDGER_PATH, identity_spec_template, record_event, start_attempt


def add_eval_fit_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    parser = subparsers.add_parser(
        "eval-fit",
        description="Evaluate AI Radar user-visible outputs against AIHOT references (radar.db opened read-only).",
    )
    commands = parser.add_subparsers(dest="eval_fit_command", required=True)

    identity = commands.add_parser("identity-spec", help="Print an unapproved explicit identity spec skeleton")
    identity.add_argument("--stage", choices=("run", "judge"), required=True)
    identity.add_argument("--questions", default=str(DEFAULT_EVALSET_DIR / "questions.jsonl"))
    identity.add_argument("--run", help="Existing run directory; required for judge")
    identity.add_argument("--model", default="deepseek-v4-flash-ga-260731")
    identity.add_argument("--limit", type=int)
    identity.add_argument("--seed", type=int, default=0)
    identity.add_argument("--stages", default="prefilter,score,enrich")
    identity.add_argument("--require-reference", choices=("title", "summary", "reason"))
    identity.add_argument("--calibrate", type=int)
    identity.add_argument(
        "--dimensions",
        default="summary,reason",
        help="Judge dimensions for identity; title is diagnostic-only and must be explicitly included",
    )

    audit = commands.add_parser("audit", help="Inspect the three-layer AIHOT eval system without LLM calls")
    audit.add_argument("--runs", default=str(DEFAULT_RUNS_DIR), help="Local eval-fit runs directory to inspect")
    audit.add_argument("--ledger", default=str(DEFAULT_LEDGER_PATH), help="Tracked append-only round ledger")

    build = commands.add_parser("build", help="Join AIHOT batches to items and write questions.jsonl + manifest.json")
    build.add_argument("--db", default=str(db.DEFAULT_DB_PATH))
    build.add_argument("--out", help="Output directory; defaults to the selected evalset version's staging path")
    build.add_argument("--evalset-version", choices=("v1", "v2"), default="v1")
    build.add_argument("--base-questions", help="Frozen v1 questions authority used when building v2")
    build.add_argument("--round-id")
    build.add_argument("--ledger", default=str(DEFAULT_LEDGER_PATH))
    build.add_argument(
        "--source",
        action="append",
        metavar="BATCH=PATH",
        help="Replace the default batches (.jsonl = t2 window capture, .json = t5 raw list); repeatable",
    )

    validate = commands.add_parser("validate", help="Validate a persisted v1/v2 evalset offline")
    validate.add_argument("--evalset", required=True, help="Directory containing questions.jsonl and manifest.json")
    validate.add_argument("--base-questions", help="Frozen v1 questions authority; inferred for sibling v2 by default")

    run = commands.add_parser("run", help="Run the production stages over the evalset (ARK only, concurrency 8)")
    run.add_argument("--questions", default=str(DEFAULT_EVALSET_DIR / "questions.jsonl"))
    run.add_argument("--out", help="Run directory; default data/eval-fit/runs/<UTC stamp>-<questions sha256[:8]>")
    run.add_argument("--limit", type=int)
    run.add_argument("--seed", type=int, default=0)
    run.add_argument("--stages", default="prefilter,score,enrich")
    run.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    run.add_argument("--identity-spec", required=True, help="Explicit eval-identity spec for this attempt")
    run.add_argument("--round-id")
    run.add_argument("--ledger", default=str(DEFAULT_LEDGER_PATH))
    run.add_argument(
        "--require-reference",
        choices=("title", "summary", "reason"),
        help="Sample only questions whose AIHOT reference carries this field (reason exists on selected items only)",
    )

    judge = commands.add_parser(
        "judge", help="Judge title_zh / summary_zh / why_recommend closeness to the AIHOT reference"
    )
    judge.add_argument("--run", required=True)
    judge.add_argument("--questions", default=str(DEFAULT_EVALSET_DIR / "questions.jsonl"))
    judge.add_argument("--model", default="deepseek-v4-flash-ga-260731")
    judge.add_argument("--limit", type=int)
    judge.add_argument(
        "--dimensions",
        default="summary,reason",
        help="Comma-separated dimensions (default summary,reason; title is diagnostic-only and adds paid calls)",
    )
    judge.add_argument(
        "--calibrate", type=int, nargs="?", const=30, help="Positive/negative controls on N questions (default 30)"
    )
    judge.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    judge.add_argument("--identity-spec", required=True, help="Explicit eval-identity spec for this attempt")
    judge.add_argument("--round-id")
    judge.add_argument("--ledger", default=str(DEFAULT_LEDGER_PATH))

    report = commands.add_parser("report", help="Compute metrics.json + report.md for a run")
    report.add_argument("--run", required=True)
    report.add_argument("--questions", default=str(DEFAULT_EVALSET_DIR / "questions.jsonl"))
    report.add_argument("--baseline", help="metrics.json of another run to compare against")
    report.add_argument(
        "--thresholds",
        help="Pass marks to score against; defaults to thresholds.json beside the evalset",
    )
    report.add_argument("--round-id")
    report.add_argument("--ledger", default=str(DEFAULT_LEDGER_PATH))


def _parse_sources(values: list[str] | None) -> tuple[tuple[str, Path], ...] | None:
    if not values:
        return None
    sources: list[tuple[str, Path]] = []
    for value in values:
        name, separator, path = value.partition("=")
        if not separator or not name or not path:
            raise SystemExit(f"--source expects BATCH=PATH, got {value!r}")
        sources.append((name, Path(path)))
    return tuple(sources)


def _parse_dimensions(value: str) -> tuple[str, ...]:
    allowed = ("title", "summary", "reason")
    dimensions = tuple(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))
    unknown = [dimension for dimension in dimensions if dimension not in allowed]
    if unknown or not dimensions:
        raise SystemExit(f"--dimensions must be a non-empty comma-separated subset of {allowed}; got {value!r}")
    return dimensions


def run_eval_fit(args: argparse.Namespace) -> int:
    if args.eval_fit_command == "identity-spec":
        questions_path = Path(args.questions)
        if args.stage == "run":
            from .run import planned_behavior_identity

            stages = tuple(stage.strip() for stage in args.stages.split(",") if stage.strip())
            behavior = planned_behavior_identity(
                questions_path=questions_path,
                stages=stages,
                limit=args.limit,
                seed=args.seed,
                require_reference=args.require_reference,
            )
        else:
            from .judge import planned_judge_behavior_identity

            if not args.run:
                raise SystemExit("identity-spec --stage judge requires --run")
            behavior = planned_judge_behavior_identity(
                run_dir=Path(args.run),
                questions_path=questions_path,
                model=args.model,
                limit=args.limit,
                calibrate=args.calibrate,
                dimensions=_parse_dimensions(args.dimensions),
            )
        print(
            json.dumps(
                identity_spec_template(behavior_identity=behavior, stage=args.stage), ensure_ascii=False, indent=2
            )
        )
        return 0
    if args.eval_fit_command == "audit":
        from .audit import audit_eval_system, render_audit

        result = audit_eval_system(
            project_root=db.PROJECT_ROOT,
            runs_dir=Path(args.runs),
            ledger_path=Path(args.ledger),
        )
        print(render_audit(result))
        return 0 if result["summary"]["missing"] == 0 and result["summary"]["invalid"] == 0 else 1
    if args.eval_fit_command == "build":
        from .build import DEFAULT_SOURCES, build_evalset

        out_dir = (
            Path(args.out)
            if args.out
            else (DEFAULT_EVALSET_DIR if args.evalset_version == "v1" else DEFAULT_EVALSET_DIR_V2)
        )
        ledger_path = Path(args.ledger)
        attempt = start_attempt("build", round_id=args.round_id, ledger_path=ledger_path)
        try:
            manifest = build_evalset(
                db_path=Path(args.db),
                out_dir=out_dir,
                sources=_parse_sources(args.source) or DEFAULT_SOURCES,
                evalset_version=args.evalset_version,
                base_questions_path=Path(args.base_questions) if args.base_questions else None,
            )
            record_event(
                attempt=attempt,
                event="completed",
                status="succeeded",
                artifacts={"questions": str(out_dir / "questions.jsonl"), "manifest": str(out_dir / "manifest.json")},
                relations={"questions_sha256": str(manifest["questions_sha256"])},
                ledger_path=ledger_path,
            )
        except Exception:
            record_event(attempt=attempt, event="failed", status="failed", ledger_path=ledger_path)
            raise
        for batch, counts in manifest["batches"].items():
            print(
                f"batch={batch} read={counts['read']} matched={counts['matched']} "
                f"unmatched={counts['unmatched']} deduped={counts['deduped']} kept={counts['kept']}"
            )
        print(
            f"questions={manifest['question_count']} duplicates={manifest['duplicate_count']} with_tags={manifest['with_tags']}"
        )
        print(f"questions_sha256={manifest['questions_sha256']}")
        print(f"out={out_dir}")
        return 0
    if args.eval_fit_command == "validate":
        from .build import validate_evalset

        result = validate_evalset(
            evalset_dir=Path(args.evalset),
            base_questions_path=Path(args.base_questions) if args.base_questions else None,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.eval_fit_command == "run":
        from .run import default_run_id, run_stages

        questions_path = Path(args.questions)
        # Before writing a new run, not after: pruning is about the disk this run is
        # about to use, and a run that fails partway still leaves its directory behind.
        for pruned in prune_old_runs():
            print(f"pruned old run: {pruned}")
        out_dir = Path(args.out) if args.out else DEFAULT_RUNS_DIR / default_run_id(sha256_file(questions_path))
        stages = tuple(stage.strip() for stage in args.stages.split(",") if stage.strip())
        summary = run_stages(
            questions_path=questions_path,
            out_dir=out_dir,
            limit=args.limit,
            seed=args.seed,
            stages=stages,
            workers=args.workers,
            require_reference=args.require_reference,
            identity_spec_path=Path(args.identity_spec),
            round_id=args.round_id,
            ledger_path=Path(args.ledger),
        )
        for stage, counts in summary["stage_summary"].items():
            print(
                f"stage={stage} ok={counts['ok']} errors={counts['errors']} skipped={counts['skipped']} "
                f"latency_ms_median={counts['latency_ms_median']}"
            )
        print(
            f"run_dir={out_dir} n={summary['n']} pool_eligible={summary['pool_eligible']}/{summary['pool_total']} "
            f"require_reference={summary['require_reference']} stopped_early={summary['stopped_early']}"
        )
        for reason in summary["stop_reasons"]:
            print(f"stop_reason={reason}")
        return 1 if summary["stopped_early"] else 0
    if args.eval_fit_command == "judge":
        from .judge import run_judge

        summary = run_judge(
            run_dir=Path(args.run),
            questions_path=Path(args.questions),
            model=args.model,
            limit=args.limit,
            calibrate=args.calibrate,
            workers=args.workers,
            dimensions=_parse_dimensions(args.dimensions),
            identity_spec_path=Path(args.identity_spec),
            round_id=args.round_id,
            ledger_path=Path(args.ledger),
        )
        if summary["calibration_summary"]:
            for key, value in summary["calibration_summary"]["means"].items():
                print(f"calibration {key}: n={value['n']} mean={value['mean']}")
            print(f"calibration scale_ok={summary['calibration_summary']['scale_ok']}")
        judged = " ".join(f"{name}={count}" for name, count in summary["judged"].items())
        print(
            f"judged {judged} errors={summary['errors']} skipped={summary['skipped']} "
            f"stopped_early={summary['stopped_early']}"
        )
        for reason in summary["stop_reasons"]:
            print(f"stop_reason={reason}")
        return 1 if summary["stopped_early"] else 0
    if args.eval_fit_command == "report":
        from .metrics import compute_metrics

        ledger_path = Path(getattr(args, "ledger", DEFAULT_LEDGER_PATH))
        attempt = start_attempt("report", round_id=getattr(args, "round_id", None), ledger_path=ledger_path)
        try:
            payload = compute_metrics(
                run_dir=Path(args.run),
                questions_path=Path(args.questions),
                baseline_path=Path(args.baseline) if args.baseline else None,
                thresholds_path=Path(args.thresholds) if args.thresholds else None,
            )
            record_event(
                attempt=attempt,
                event="completed",
                status="succeeded",
                artifacts={
                    "metrics": str(Path(args.run) / "metrics.json"),
                    "report": str(Path(args.run) / "report.md"),
                },
                relations={"questions_sha256": str(payload.get("questions_sha256") or "unknown")},
                ledger_path=ledger_path,
            )
        except Exception:
            record_event(attempt=attempt, event="failed", status="failed", ledger_path=ledger_path)
            raise
        for name, metric in payload["metrics"].items():
            print(f"{name}: n={metric['n']} value={metric['value']} ci95={metric['ci95']}")
        comparison = payload.get("comparison") or {}
        if comparison.get("comparable"):
            regressed = [n for n, d in comparison["metrics"].items() if d.get("regressed") is True]
            improved = [n for n, d in comparison["metrics"].items() if d.get("improved") is True]
            print(f"vs baseline: improved={','.join(improved) or 'none'} regressed={','.join(regressed) or 'none'}")
            drifted = comparison.get("stage_identity_diff") or {}
            if drifted:
                print(f"WARNING: pipeline identity differs from the baseline on {','.join(sorted(drifted))}")
        elif comparison:
            print(f"vs baseline: NOT COMPARABLE ({comparison.get('reason')})")
        excluded = (comparison or {}).get("excluded_metrics") or {}
        if excluded:
            print(f"NOT ACCEPTED in baseline comparison: {','.join(sorted(excluded))}")
        verdicts = payload.get("threshold_verdicts") or {}
        blocked = [name for name, v in verdicts.items() if v.get("confident") is False]
        unknown = [
            name
            for name, v in verdicts.items()
            if v.get("confident") is None and "subset" not in (v.get("reason") or "")
        ]
        if verdicts:
            print(f"thresholds: below={','.join(blocked) or 'none'} undetermined={','.join(unknown) or 'none'}")
        if payload.get("stopped_early"):
            print("WARNING: this run stopped early; the readings above cover only part of the subset")
        judge_acceptance = payload.get("judge_acceptance") or {}
        judge_values_exist = any(
            (payload["metrics"].get(name) or {}).get("n", 0) > 0
            for name in ("title_closeness_mean", "summary_closeness_mean", "reason_closeness_mean")
        )
        if judge_values_exist and judge_acceptance.get("accepted") is not True:
            print(
                "NOT ACCEPTED: judge-dependent summary/reason metrics are diagnostic only "
                f"({judge_acceptance.get('reason')})"
            )
        title_metric = payload["metrics"].get("title_closeness_mean") or {}
        if title_metric.get("n", 0) > 0:
            print("NOT ACCEPTED: title_closeness_mean is diagnostic only (title-specific judge validation missing)")
        print(f"metrics={Path(args.run) / 'metrics.json'} report={Path(args.run) / 'report.md'}")
        return 1 if blocked else 0
    raise SystemExit(f"unknown eval-fit command: {args.eval_fit_command}")
