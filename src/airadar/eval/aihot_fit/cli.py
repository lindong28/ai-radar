"""``ai-radar eval-fit {build,run,judge,report}`` argument wiring and dispatch."""

from __future__ import annotations

import argparse
from pathlib import Path

from ... import db
from .common import DEFAULT_EVALSET_DIR, DEFAULT_RUNS_DIR, DEFAULT_WORKERS, sha256_file


def add_eval_fit_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    parser = subparsers.add_parser(
        "eval-fit",
        description="Evaluate prefilter/score/enrich against AIHOT reference outputs (radar.db opened read-only).",
    )
    commands = parser.add_subparsers(dest="eval_fit_command", required=True)

    build = commands.add_parser("build", help="Join AIHOT batches to items and write questions.jsonl + manifest.json")
    build.add_argument("--db", default=str(db.DEFAULT_DB_PATH))
    build.add_argument("--out", default=str(DEFAULT_EVALSET_DIR))
    build.add_argument(
        "--source",
        action="append",
        metavar="BATCH=PATH",
        help="Replace the default batches (.jsonl = t2 window capture, .json = t5 raw list); repeatable",
    )

    run = commands.add_parser("run", help="Run the production stages over the evalset (ARK only, concurrency 8)")
    run.add_argument("--questions", default=str(DEFAULT_EVALSET_DIR / "questions.jsonl"))
    run.add_argument("--out", help="Run directory; default data/eval-fit/runs/<UTC stamp>-<questions sha256[:8]>")
    run.add_argument("--limit", type=int)
    run.add_argument("--seed", type=int, default=0)
    run.add_argument("--stages", default="prefilter,score,enrich")
    run.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    run.add_argument(
        "--require-reference",
        choices=("summary", "reason"),
        help="Sample only questions whose AIHOT reference carries this field (reason exists on selected items only)",
    )

    judge = commands.add_parser("judge", help="Judge summary_zh / why_recommend closeness to the AIHOT reference")
    judge.add_argument("--run", required=True)
    judge.add_argument("--questions", default=str(DEFAULT_EVALSET_DIR / "questions.jsonl"))
    judge.add_argument("--model", default="deepseek-v4-flash-ga-260731")
    judge.add_argument("--limit", type=int)
    judge.add_argument(
        "--calibrate", type=int, nargs="?", const=30, help="Positive/negative controls on N questions (default 30)"
    )
    judge.add_argument("--workers", type=int, default=DEFAULT_WORKERS)

    report = commands.add_parser("report", help="Compute metrics.json + report.md for a run")
    report.add_argument("--run", required=True)
    report.add_argument("--questions", default=str(DEFAULT_EVALSET_DIR / "questions.jsonl"))
    report.add_argument("--baseline", help="metrics.json of another run to compare against")
    report.add_argument(
        "--thresholds",
        help="Pass marks to score against; defaults to thresholds.json beside the evalset",
    )


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


def run_eval_fit(args: argparse.Namespace) -> int:
    if args.eval_fit_command == "build":
        from .build import DEFAULT_SOURCES, build_evalset

        manifest = build_evalset(
            db_path=Path(args.db),
            out_dir=Path(args.out),
            sources=_parse_sources(args.source) or DEFAULT_SOURCES,
        )
        for batch, counts in manifest["batches"].items():
            print(
                f"batch={batch} read={counts['read']} matched={counts['matched']} "
                f"unmatched={counts['unmatched']} deduped={counts['deduped']} kept={counts['kept']}"
            )
        print(
            f"questions={manifest['question_count']} duplicates={manifest['duplicate_count']} with_tags={manifest['with_tags']}"
        )
        print(f"questions_sha256={manifest['questions_sha256']}")
        print(f"out={args.out}")
        return 0
    if args.eval_fit_command == "run":
        from .run import default_run_id, run_stages

        questions_path = Path(args.questions)
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
        )
        if summary["calibration_summary"]:
            for key, value in summary["calibration_summary"]["means"].items():
                print(f"calibration {key}: n={value['n']} mean={value['mean']}")
            print(f"calibration scale_ok={summary['calibration_summary']['scale_ok']}")
        print(
            f"judged summary={summary['judged']['summary']} reason={summary['judged']['reason']} "
            f"errors={summary['errors']} skipped={summary['skipped']} stopped_early={summary['stopped_early']}"
        )
        for reason in summary["stop_reasons"]:
            print(f"stop_reason={reason}")
        return 1 if summary["stopped_early"] else 0
    if args.eval_fit_command == "report":
        from .metrics import compute_metrics

        payload = compute_metrics(
            run_dir=Path(args.run),
            questions_path=Path(args.questions),
            baseline_path=Path(args.baseline) if args.baseline else None,
            thresholds_path=Path(args.thresholds) if args.thresholds else None,
        )
        for name, metric in payload["metrics"].items():
            print(f"{name}: n={metric['n']} value={metric['value']} ci95={metric['ci95']}")
        verdicts = payload.get("threshold_verdicts") or {}
        blocked = [name for name, v in verdicts.items() if v.get("confident") is False]
        unknown = [name for name, v in verdicts.items() if v.get("confident") is None]
        if verdicts:
            print(f"thresholds: below={','.join(blocked) or 'none'} undetermined={','.join(unknown) or 'none'}")
        if payload.get("stopped_early"):
            print("WARNING: this run stopped early; the readings above cover only part of the subset")
        calibration = payload.get("judge_calibration") or {}
        if calibration and calibration.get("scale_ok") is not True:
            print(f"WARNING: judge calibration scale_ok={calibration.get('scale_ok')}")
        print(f"metrics={Path(args.run) / 'metrics.json'} report={Path(args.run) / 'report.md'}")
        return 1 if blocked else 0
    raise SystemExit(f"unknown eval-fit command: {args.eval_fit_command}")
