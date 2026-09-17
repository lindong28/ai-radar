"""Human-first CLI. --json is the explicit machine-readable result channel."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .assets import DEFAULT_DATA_ROOT, ROOT, read_json, rebuild_index, validate_layout


def transport_factory(config: dict, env_file: Path | None):
    from airadar.runtime_env import load_runtime_env, read_value

    from .transport import DurableChat

    load_runtime_env(project_env=env_file)
    provider = config["transport_identity"]["provider"]
    key = read_value("ARK_API_KEY" if provider == "ark" else "DEEPSEEK_API_KEY", project_env=env_file)
    if not key:
        raise ValueError("configured provider credential is missing; no fallback attempted")

    def factory(attempts):
        return lambda case_id: DurableChat(attempts, provider=provider, base_url=config["transport_identity"]["base_url"],
                                           api_key=key, case_id=case_id)
    return factory


def main(argv: list[str] | None = None, *, entry_target: str | None = None) -> int:
    parser = argparse.ArgumentParser(description="AIHOT 对齐评测：冻结数据、smoke、完整重放、同题比较；不修改生产。")
    parser.add_argument("--json", action="store_true", help="输出机器可读结果")
    sub = parser.add_subparsers(dest="command", required=True)
    capture = sub.add_parser("capture", help="采集已关闭的 AIHOT 小时窗，保留 API/SSR 原始证据")
    capture.add_argument("--start", required=True)
    capture.add_argument("--end", required=True)
    capture.add_argument("--output", type=Path, required=True)
    build = sub.add_parser("build", help="从共同连续窗口创建四对象的新版本题库")
    build.add_argument("--raw-root", type=Path, required=True)
    build.add_argument("--reference", type=Path, required=True)
    build.add_argument("--version", required=True)
    build.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    run = sub.add_parser("run", help="执行新推理；先 --smoke 3，再完整池；已有成功阶段自动复用")
    run.add_argument("--dataset", type=Path, required=True)
    run.add_argument("--config", type=Path, required=True, help="显式模型与固定供应商配置，不含凭据")
    run.add_argument("--env-file", type=Path)
    run.add_argument("--smoke", type=int)
    run.add_argument("--workers", type=int, default=8)
    run.add_argument("--fixed-pool", type=Path, help="复用 pool.jsonl 调整选择规则；零模型调用")
    run.add_argument("--label", default="baseline")
    compare = sub.add_parser("compare", help="比较同题、同尺的两轮，不将 smoke 当成绩")
    compare.add_argument("baseline", type=Path)
    compare.add_argument("candidate", type=Path)
    judge = sub.add_parser("judge", help="复用富化输出，逐文本字段调用判官；没有用户校准则只作诊断")
    judge.add_argument("experiment", type=Path)
    judge.add_argument("--config", type=Path, required=True)
    judge.add_argument("--env-file", type=Path)
    judge.add_argument("--calibration", type=Path)
    judge.add_argument("--workers", type=int, default=8)
    export = sub.add_parser("export-calibration", help="生成十二条待用户标注的材料，不自动填答案")
    export.add_argument("experiment", type=Path)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--config", type=Path, required=True)
    calibrate = sub.add_parser("calibrate", help="仅接受用户确认的标签文件SHA；先开发集后独立验证集")
    calibrate.add_argument("material", type=Path)
    calibrate.add_argument("labels", type=Path)
    calibrate.add_argument("--user-confirmed-sha256", required=True)
    calibrate.add_argument("--config", type=Path, required=True)
    calibrate.add_argument("--env-file", type=Path)
    sub.add_parser("validate", help="检查四对象代码/指标目录约定")
    sub.add_parser("index", help="从原始评分重建指标总表")
    args = parser.parse_args(argv)
    try:
        if args.command == "capture":
            from .interval import capture_interval
            result = {"manifest": str(capture_interval(args.start, args.end, args.output))}
        elif args.command == "build":
            from .dataset import build as build_dataset
            result = build_dataset(raw_root=args.raw_root, reference=args.reference, version=args.version, data_root=args.data_root)
        elif args.command == "run":
            from .runner import evaluate
            config = read_json(args.config)
            factory = None if args.fixed_pool else transport_factory(config, args.env_file)
            result = evaluate(args.dataset, config=config, chat_factory=factory, smoke=args.smoke,
                              workers=args.workers, fixed_pool=args.fixed_pool, label=args.label)
        elif args.command == "compare":
            from .runner import compare as compare_runs
            result = compare_runs(args.baseline, args.candidate)
        elif args.command in {"judge", "export-calibration", "calibrate"}:
            from .assessment import calibrate_file, export_calibration, judge_run
            from .judge import DEFAULT_MODEL
            config = read_json(args.config)
            kwargs = {"model": config.get("judge_model", DEFAULT_MODEL), "provider": config["transport_identity"]["provider"]}
            if args.command == "export-calibration":
                result = export_calibration(args.experiment, args.output, **kwargs)
            else:
                factory = transport_factory(config, args.env_file)
                if args.command == "judge":
                    result = judge_run(args.experiment, chat_factory=factory, calibration=args.calibration, workers=args.workers, **kwargs)
                else:
                    result = calibrate_file(args.material, args.labels, args.user_confirmed_sha256, chat_factory=factory, **kwargs)
        elif args.command == "index":
            result = {"metric_rows": len(rebuild_index()), "index": str(ROOT / "experiments/metrics/summary.json")}
        else:
            validate_layout()
            result = {"objects": 4, "layout": "valid", "live_evaluation": "not_run"}
    except (ValueError, OSError, KeyError) as exc:
        if args.json:
            print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"未完成 {args.command}：{exc}\n已落盘的证据保留；修正该条件后重跑，成功阶段可复用。")
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    elif args.command == "run":
        print("运行已归档；smoke 仅验证链路，未校验文本判官不作为有效成绩。" if args.smoke else "运行已归档；各指标按其采信状态解释，不代表已拟合达标。")
        for target, row in result.items():
            print(f"{target}：{'计算完成' if row['complete'] else '有未计算项'}；{row['experiment']}")
    elif args.command == "build":
        counts = result["counts"]
        print(f"题库 {result['version']} 已冻结：{counts['hours']:g} 小时，{counts['source_count']} 个共同来源，{counts['unique_raw_news']} 条未过滤新闻，{counts['reference_members']} 条参照成员。")
        print(f"重复抓取记录 {counts['raw_observation_count']} 条；参照缺 raw {counts['missing_raw']} 条，保留在召回分母。")
    else:
        print(f"{args.command} 已完成。")
        for name, value in result.items():
            print(f"{name}：{value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
