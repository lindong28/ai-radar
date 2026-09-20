"""Offline pointwise O2 scoring with explicit prompts and no ranking pool."""
from __future__ import annotations

import argparse
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from jinja2 import StrictUndefined, Template

from airadar.curator.score import weighted_score
from airadar.curator.weights import DEFAULT_WEIGHTS, DIMENSIONS
from airadar.provider.judgment import require_reason_first
from airadar.scorer.five import FIVE_WEIGHTS, five_score, validated_weights
from airadar.scorer.semantic import SEMANTIC_WEIGHTS, semantic_score

from .assets import (
    ROOT,
    archive_metrics,
    create_run,
    digest,
    file_digest,
    load_dataset,
    read_json,
    read_jsonl,
    rebuild_index,
    slug,
    utc_now,
    write_json,
    write_jsonl,
)
from .inference import _item, _request
from .metrics import score
from .prefilter_eval import select_cases
from .relocations import resolve_asset_path

TARGET = "visible-score"
BENCHMARK = "aihot-score-pointwise"


def prompt_context(raw: dict) -> dict:
    """Only the production item allowlist is available to a prompt."""
    return {"item": _item(raw)}


def project_score(payload: dict, mode: str, tier: str, five_weights: dict | None = None) -> dict:
    """Validate the model's emitted order and values before any score mapping."""
    if mode == "semantic":
        return {"score": semantic_score(payload)}
    if mode == "five":
        return {"score": five_score(payload, five_weights)}
    if mode not in {"dimensions", "direct"}:
        raise ValueError("mode must be dimensions, direct, semantic or five")
    require_reason_first(payload, "relevance" if mode == "dimensions" else "score")
    fields = DIMENSIONS if mode == "dimensions" else ("score",)
    ceiling = 10 if mode == "dimensions" else 100
    for field in fields:
        value = payload.get(field)
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= ceiling:
            raise ValueError(f"{field} must be a finite number in 0..{ceiling}")
    if mode == "direct":
        if payload["score"] != int(payload["score"]):
            raise ValueError("direct score must be an integer")
        return {"score": int(payload["score"])}
    value = weighted_score(payload, DEFAULT_WEIGHTS, tier)
    # JavaScript Math.round for this nonnegative score domain, not Python round.
    return {"score": math.floor(value * 10 + 0.5)}


def object_identity(config: dict, prompt: dict, mode: str) -> dict:
    paths = (
        "evals/_shared/score_eval.py", "evals/_shared/prefilter_eval.py",
        "evals/_shared/inference.py", "evals/_shared/transport.py", "evals/_shared/cli.py",
        "src/airadar/provider/base.py", "src/airadar/provider/judgment.py",
        "src/airadar/provider/deepseek_chat.py", "src/airadar/scorer/prompts.py",
        "src/airadar/curator/score.py", "src/airadar/curator/weights.py", "web/static/app.js",
    )
    mapping = {"function": "identity-integer-0..100", "ranking_pool": False}
    if mode == "dimensions":
        mapping = {"function": "weighted_score; Math.round(score * 10)",
                   "weights": DEFAULT_WEIGHTS.as_record(), "ranking_pool": False}
    elif mode == "semantic":
        paths += ("src/airadar/scorer/semantic.py",)
        mapping = {"function": "sum(coefficient * dimension_score)",
                   "coefficients": SEMANTIC_WEIGHTS, "dimension_domain": "integer-0..10",
                   "output_domain": "integer-0..100", "ranking_pool": False}
    elif mode == "five":
        paths += ("src/airadar/scorer/five.py",)
        mapping = {"function": "Math.round(sum(percent * dimension_score) / 10)",
                   "weights_percent": validated_weights(config.get("five_weights", FIVE_WEIGHTS)),
                   "dimension_domain": "integer-0..10", "output_domain": "integer-0..100",
                   "ranking_pool": False}
    return {
        "surface": "ordinary-nonfeatured-visible-score", "mode": mode,
        "prompt": prompt, "prompt_contract": "model-emitted-reason-first",
        "request": _request("score", config), "transport": config["transport_identity"],
        "mapping": mapping,
        "source_sha256": {p: file_digest(ROOT / p) for p in paths},
        "thinking": "disabled", "retry_count": 0, "fallback": False,
    }


def evaluate(dataset: Path, *, config: dict, prompt: dict, split: str,
             limit: int | None, seed: str, chat_factory, label: str,
             mode: str = "dimensions", workers: int = 8, smoke: bool = False,
             reuse: Path | None = None, root: Path = ROOT,
             exclude_runs: tuple[Path, ...] = ()) -> dict:
    if not 1 <= workers <= 32:
        raise ValueError("workers must be 1..32")
    if mode not in {"dimensions", "direct", "semantic", "five"}:
        raise ValueError("mode must be dimensions, direct, semantic or five")
    five_weights = validated_weights(config.get("five_weights")) if mode == "five" else None
    if mode != "five" and "five_weights" in config:
        raise ValueError("five_weights is only supported in five mode")
    if not isinstance(prompt, dict) or set(prompt) != {"system", "user_template"} or not all(
        isinstance(value, str) and value.strip() for value in prompt.values()
    ):
        raise ValueError("prompt requires only nonempty system and user_template strings")
    if "weights" in config and config["weights"] != DEFAULT_WEIGHTS.as_record():
        raise ValueError("pointwise baseline uses current production weights")
    dataset = resolve_asset_path(dataset.expanduser(), root=root)
    manifest, pool = load_dataset(dataset, TARGET)
    if manifest["benchmark"] != BENCHMARK:
        raise ValueError("this runner requires the independent score-pointwise benchmark")
    exclusions = [{"run": str(path.resolve()), "cases_sha256": file_digest(path / "cases.jsonl"),
                   "case_ids": [case["case_id"] for case in read_jsonl(path / "cases.jsonl")]}
                  for path in exclude_runs]
    excluded = frozenset(key for row in exclusions for key in row["case_ids"])
    cases = select_cases(pool, split, limit, seed, excluded)
    # References are validated before spending, but never passed to the model.
    for case in cases:
        slug(case["case_id"])
        if not isinstance(case["input"], dict) or "score" not in case["reference"]:
            raise ValueError("pointwise cases require source input and reference score")
    score("O2", cases, [])
    identity = object_identity(config, prompt, mode)
    template = Template(prompt["user_template"], undefined=StrictUndefined)
    prompts, preparation_errors = {}, {}
    for case in cases:
        key = case["case_id"]
        try:
            prompts[key] = {"system": prompt["system"],
                            "user": template.render(**prompt_context(case["input"]))}
        except Exception as exc:
            preparation_errors[key] = type(exc).__name__
    scorer_identity = {p: file_digest(ROOT / p) for p in (
        "evals/_shared/metrics.py", f"evals/{TARGET}/{BENCHMARK}/metrics.json")}
    cached = {}
    if reuse is not None:
        reuse = resolve_asset_path(reuse.expanduser(), root=root)
        old = read_json(reuse / "started.json")
        if old["object_identity"] != identity:
            raise ValueError("reuse object identity mismatch")
        if read_jsonl(reuse / "cases.jsonl") != cases or old["case_identity"] != digest(cases):
            raise ValueError("reuse requires the exact same cases and order")
        if old["dataset_manifest_sha256"] != file_digest(dataset / "manifest.json"):
            raise ValueError("reuse dataset identity mismatch")
        for case in cases:
            path = reuse / "items" / f"{case['case_id']}.json"
            if path.is_file():
                row = read_json(path)
                if row["case_id"] != case["case_id"]:
                    raise ValueError("reuse item identity mismatch")
                if row["status"] == "ok":
                    # Do not trust an edited success row without revalidating its response.
                    payload = json.loads(row["response_json"])
                    if row["output"] != project_score(payload, mode, case["input"]["tier"], five_weights):
                        raise ValueError("reuse output mapping mismatch")
                    if row["prompt"] != prompts.get(case["case_id"]):
                        raise ValueError("reuse rendered prompt mismatch")
                    cached[row["case_id"]] = row
    run, experiment = create_run(root, TARGET, manifest["version"], benchmark=BENCHMARK)
    metadata = {
        "target": TARGET, "benchmark": BENCHMARK, "version": manifest["version"], "label": label,
        "dataset": str(dataset.resolve()), "dataset_manifest_sha256": file_digest(dataset / "manifest.json"),
        "dataset_cases_sha256": file_digest(dataset / "cases.jsonl"),
        "case_identity": digest(cases), "case_ids": [case["case_id"] for case in cases],
        "split": split, "smoke": smoke, "mode": mode, "object_identity": identity,
        "scorer_identity": scorer_identity, "config_sha256": digest(config),
        "rendered_prompts_sha256": digest(prompts), "preparation_errors": preparation_errors,
        "selection": {"seed": seed, "limit": limit, "method": "label-blind-hash-order", "exclusions": exclusions},
        "started_at": utc_now(), "directory_timestamp_utc": "/".join(run.parts[-2:]),
        "directory_time_source": "run_created", "workers": workers, "reuse_run": str(reuse) if reuse else None,
        "scope": "smoke only" if smoke else "fixed selected split; pointwise score only",
        "cost_usd": None, "cost_reason": "unpriced; usage and all attempts retained",
    }
    write_json(run / "started.json", metadata)
    write_json(run / "config.json", config)
    write_json(run / "prompt.json", prompt)
    write_jsonl(run / "cases.jsonl", cases)
    write_jsonl(run / "prompts.jsonl", [{"case_id": key, "prompt": value, "request": identity["request"]}
                                       for key, value in prompts.items()])
    print(f"运行目录：{run}；{len(cases)} 题，{split}，{mode}，并发上限 {workers}", flush=True)
    chat_for_case = chat_factory(run / "attempts")
    start = time.monotonic()

    def one(case):
        key = case["case_id"]
        if key in cached:
            row = {**cached[key], "reused_from": str(reuse)}
        else:
            row = {"case_id": key, "status": "error", "output": None, "raw": None,
                   "reason": None, "usage": None, "prompt": prompts.get(key),
                   "request": identity["request"]}
            try:
                if key in preparation_errors:
                    raise ValueError("source input or prompt could not be rendered")
                response = chat_for_case(key)(stage="score", prompt=prompts[key], request=identity["request"])
                row.update({field: response.get(field) for field in
                            ("raw", "usage", "model", "provider", "requested_model", "attempt_id")})
                payload = response["json"]
                # A text envelope preserves invalid NaN/Infinity responses in strict JSON archives.
                row["response_json"] = json.dumps(payload, ensure_ascii=False)
                if row["raw"] is None:
                    row["raw"] = row["response_json"]
                if isinstance(payload, dict) and isinstance(payload.get("reason"), str):
                    row["reason"] = payload["reason"]
                row["output"] = project_score(payload, mode, case["input"]["tier"], five_weights)
                row["status"] = "ok"
            except Exception as exc:
                row["error"] = preparation_errors.get(key, type(exc).__name__)
                if isinstance(getattr(exc, "attempt_id", None), str):
                    row["attempt_id"] = exc.attempt_id
                    attempt = read_json(run / "attempts" / f"{exc.attempt_id}.json")
                    row.update({field: attempt.get(field) for field in
                                ("raw", "usage", "provider", "requested_model")})
                    row["model"] = attempt.get("actual_model")
        write_json(run / "items" / f"{key}.json", row)
        return row

    predictions = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(one, case) for case in cases]
        for future in as_completed(futures):
            predictions.append(future.result())
            if len(predictions) % 25 == 0 or len(predictions) == len(cases):
                errors = sum(row["status"] != "ok" for row in predictions)
                print(f"已完成 {len(predictions)}/{len(cases)}；失败 {errors}", flush=True)
    predictions.sort(key=lambda row: row["case_id"])
    write_jsonl(run / "predictions.jsonl", predictions)
    result = score("O2", cases, predictions)
    metadata.update(ended_at=utc_now(), elapsed_seconds=time.monotonic() - start,
                    status="complete" if result["complete"] else "incomplete")
    archive_metrics(root, run, experiment, result, metadata)
    rebuild_index(root)
    return {"run": str(run), "experiment": str(experiment), "complete": result["complete"],
            "metrics": result["metrics"], "elapsed_seconds": metadata["elapsed_seconds"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="离线逐条评分：reason-first；不执行排名映射或修改生产。")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--dataset", type=Path, required=True)
    rescore = sub.add_parser("rescore", help="原预测补算当前指标；不调用模型，不修改原始实验")
    rescore.add_argument("--source-run", type=Path, required=True)
    rescore.add_argument("--label", required=True)
    rescore.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    run = sub.add_parser("run")
    run.add_argument("--dataset", type=Path, required=True)
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--prompt", type=Path, required=True)
    run.add_argument("--mode", choices=["dimensions", "direct", "semantic", "five"], default="dimensions")
    run.add_argument("--env-file", type=Path)
    run.add_argument("--split", choices=["dev", "regression"], required=True)
    run.add_argument("--limit", type=int)
    run.add_argument("--seed", default="visible-score-20260920")
    run.add_argument("--workers", type=int, default=8)
    run.add_argument("--label", required=True)
    run.add_argument("--reuse", type=Path)
    run.add_argument("--exclude-run", type=Path, action="append", default=[])
    run.add_argument("--smoke", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "validate":
        from .object_entry import main as validate_main
        return validate_main(target=TARGET, benchmark=BENCHMARK,
                             argv=["validate", "--dataset", str(args.dataset)])
    if args.command == "rescore":
        from .score_rescore import rescore as rescore_saved
        result = rescore_saved(args.source_run, label=args.label)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("已归档原预测的指标重评分；新增模型调用 0，原始实验不变。")
            for name, metric in result["metrics"].items():
                value = metric["value"]
                detail = f"{value:.6f}" if value is not None else f"不可计算（{metric['reason']}）"
                print(f"{name}: {detail}；题数 {metric['denominator']}")
            print(f"结果：{result['run']}；仅更新指标，不是新推理或质量达标声明。")
        return 0 if result["complete"] else 1
    from .cli import transport_factory
    config = read_json(args.config)
    factory = transport_factory(config, args.env_file)
    result = evaluate(args.dataset, config=config, prompt=read_json(args.prompt),
                      mode=args.mode, split=args.split, limit=args.limit, seed=args.seed,
                      workers=args.workers, label=args.label, chat_factory=factory,
                      smoke=args.smoke, reuse=args.reuse, exclude_runs=tuple(args.exclude_run))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
