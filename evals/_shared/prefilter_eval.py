"""Pointwise O1 runner: frozen questions, production prefilter, no downstream stages."""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from jinja2 import StrictUndefined, Template
from airadar.prefilter.policy import POLICY, context_flags

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
    utc_now,
    write_json,
    write_jsonl,
)
from .inference import _item, _request, predict_one
from .metrics import score
from .relocations import resolve_asset_path

TARGET = "news-admission"
BENCHMARK = "aihot-prefilter"


def prompt_context(raw: dict) -> dict:
    """Expose only source facts, never reference membership or case metadata."""
    return {"item": _item(raw), **context_flags(raw)}


def select_cases(cases: list[dict], split: str, limit: int | None, seed: str,
                 excluded: frozenset[str] = frozenset()) -> list[dict]:
    """Label-blind stable sampling; never rebalance positive/negative examples."""
    if split not in {"dev", "regression"}:
        raise ValueError("split must be dev or regression")
    pool = sorted((c for c in cases if c["split"] == split and c["case_id"] not in excluded),
                  key=lambda c: digest([seed, c["case_id"]]))
    if limit is not None and not 1 <= limit <= len(pool):
        raise ValueError("limit must be positive and not exceed the selected split")
    selected = pool if limit is None else pool[:limit]
    if not selected:
        raise ValueError("empty selection")
    return selected


def object_identity(config: dict, prompt: dict | None) -> dict:
    from airadar.prefilter.prompts import SYSTEM_PROMPT, USER_TEMPLATE

    paths = ["src/airadar/prefilter/policy.py", "src/airadar/provider/judgment.py", "src/airadar/prefilter/prompts.py", "src/airadar/provider/deepseek_v32.py",
             "src/airadar/prefilter/runner.py", "evals/_shared/inference.py",
             "evals/_shared/prefilter_eval.py", "evals/_shared/transport.py"]
    return {"baseline": "isolated-current-source-prefilter", "surface": "prefilter-boolean-only",
            "admission_policy": POLICY if config.get("prefilter_policy", prompt is None) else None,
            "request": _request("prefilter", config), "transport": config["transport_identity"],
            "prompt_override": prompt, "system_prompt": SYSTEM_PROMPT if prompt is None else prompt["system"],
            "source_sha256": {p: file_digest(ROOT / p) for p in paths},
            "production_template_type": type(USER_TEMPLATE).__name__, "thinking": "disabled",
            "retry_count": 0, "fallback": False}


def evaluate(dataset: Path, *, config: dict, split: str, limit: int | None, seed: str,
             chat_factory, label: str, workers: int = 8, prompt: dict | None = None,
             smoke: bool = False, reuse: Path | None = None, root: Path = ROOT,
             exclude_runs: tuple[Path, ...] = (), benchmark: str = BENCHMARK,
             quote_context: bool = False) -> dict:
    if not 1 <= workers <= 32:
        raise ValueError("workers must be 1..32")
    dataset = resolve_asset_path(dataset, root=root)
    manifest, pool = load_dataset(dataset, TARGET)
    if manifest["benchmark"] != benchmark:
        raise ValueError("this runner requires the independent prefilter benchmark")
    exclusions = [{"run": str(p.resolve()), "cases_sha256": file_digest(p / "cases.jsonl"),
                   "case_ids": [c["case_id"] for c in read_jsonl(p / "cases.jsonl")]}
                  for p in exclude_runs]
    excluded = frozenset(key for row in exclusions for key in row["case_ids"])
    cases = select_cases(pool, split, limit, seed, excluded)
    # Explicit prompt experiments remain model-only unless explicitly enabled.
    config = {**config, "prefilter_policy": config.get("prefilter_policy", prompt is None)}
    identity = object_identity(config, prompt)
    # Validate all input/template paths before creating chargeable attempts.
    template = None
    if prompt is not None:
        if set(prompt) != {"system", "user_template"} or not all(isinstance(v, str) for v in prompt.values()):
            raise ValueError("prompt requires only system and user_template strings")
        template = Template(prompt["user_template"], undefined=StrictUndefined)
    prompts = {c["case_id"]: {"system": prompt["system"], "user": template.render(**prompt_context(c["input"]))}
               for c in cases} if template else {}
    contexts = []
    if quote_context:
        from .quote_context import QuoteContext, render_quotes
        if prompt is None:
            raise ValueError("quote context requires an explicit candidate prompt")
        raw_path = dataset / manifest["shared_evidence"] / "raw-inputs.jsonl"
        if manifest.get("evidence_files", {}).get("raw-inputs.jsonl") != file_digest(raw_path):
            raise ValueError("quote context requires frozen raw evidence")
        index = QuoteContext(raw_path)
        for case in cases:
            rows = index.resolve(case["input"], case["provenance"]["observed_at"])
            contexts.append({"case_id": case["case_id"], "quotes": rows})
            prompts[case["case_id"]]["user"] += render_quotes(rows)
        identity["quote_context"] = {"raw_inputs_sha256": index.sha256,
                                     "code_sha256": file_digest(ROOT / "evals/_shared/quote_context.py"),
                                     "resolved_sha256": digest(contexts)}
    scorer_identity = {p: file_digest(ROOT / p) for p in
                       ("evals/_shared/metrics.py", f"evals/news-admission/{benchmark}/metrics.json")}
    cached = {}
    if reuse is not None:
        old = read_json(reuse / "started.json")
        if old["object_identity"] != identity:
            raise ValueError("reuse object identity mismatch")
        old_cases = {c["case_id"]: c for c in read_jsonl(reuse / "cases.jsonl")}
        for path in (reuse / "items").glob("*.json"):
            row = read_json(path)
            if row["status"] == "ok" and row["case_id"] in old_cases:
                cached[row["case_id"]] = (old_cases[row["case_id"]], row)
    run, experiment = create_run(root, TARGET, manifest["version"], benchmark=benchmark)
    metadata = {"target": TARGET, "benchmark": benchmark, "version": manifest["version"],
                "label": label, "dataset": str(dataset.resolve()),
                "dataset_manifest_sha256": file_digest(dataset / "manifest.json"),
                "case_identity": digest(cases), "split": split, "smoke": smoke,
                "selection": {"seed": seed, "limit": limit, "method": "label-blind-hash-order",
                              "exclusions": exclusions},
                "case_ids": [c["case_id"] for c in cases], "object_identity": identity,
                "scorer_identity": scorer_identity, "started_at": utc_now(),
                "directory_timestamp_utc": "/".join(run.parts[-2:]), "directory_time_source": "run_created",
                "workers": workers, "reuse_run": str(reuse) if reuse else None,
                "scope": "smoke only" if smoke else "fixed sampled split; not full benchmark",
                "cost_usd": None, "cost_reason": "unpriced; usage and all attempts retained"}
    write_json(run / "started.json", metadata)
    write_json(run / "config.json", config)
    write_jsonl(run / "cases.jsonl", cases)
    if quote_context:
        write_jsonl(run / "quote-context.jsonl", contexts)
    print(f"运行目录：{run}；{len(cases)} 题，{split}，并发上限 {workers}", flush=True)
    chat_for_case = chat_factory(run / "attempts")
    start = time.monotonic()

    def one(case):
        key = case["case_id"]
        previous = cached.get(key)
        if previous and previous[0] == case:
            row = {**previous[1], "reused_from": str(reuse)}
        else:
            chat = chat_for_case(key)
            def call(**kwargs):
                if prompts:
                    kwargs["prompt"] = prompts[key]
                return chat(**kwargs)
            raw = {**case["input"], "case_id": key}
            result = predict_one("prefilter", raw, {**config, "chat": call})
            row = {**result, "output": {"member": result["output"]["is_ai_related"]}
                   if result["status"] == "ok" else None}
            if config["prefilter_policy"]:
                row["admission_policy"] = {"name": POLICY, "rejection_reasons": []}
                if result["status"] == "ok":
                    row["admission_policy"] = result["output"]["admission_policy"]
                    row["model_output"] = {"member": result["output"]["model_output"]["is_ai_related"]}
        write_json(run / "items" / f"{key}.json", row)
        return row

    predictions = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(one, c) for c in cases]
        for future in as_completed(futures):
            predictions.append(future.result())
            if len(predictions) % 25 == 0 or len(predictions) == len(cases):
                errors = sum(p["status"] != "ok" for p in predictions)
                print(f"已完成 {len(predictions)}/{len(cases)}；失败 {errors}", flush=True)
    predictions.sort(key=lambda r: r["case_id"])
    write_jsonl(run / "predictions.jsonl", predictions)
    result = score("O1", cases, predictions)
    metadata.update(ended_at=utc_now(), elapsed_seconds=time.monotonic() - start,
                    status="complete" if result["complete"] else "incomplete")
    archive_metrics(root, run, experiment, result, metadata)
    rebuild_index(root)
    return {"run": str(run), "experiment": str(experiment), "complete": result["complete"],
            "metrics": result["metrics"], "elapsed_seconds": metadata["elapsed_seconds"]}


def main(argv=None, *, benchmark=BENCHMARK) -> int:
    parser = argparse.ArgumentParser(description="独立 prefilter：只运行准入，不执行评分/富化，不写生产数据库。")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--dataset", type=Path, required=True)
    run = sub.add_parser("run")
    run.add_argument("--dataset", type=Path, required=True)
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--env-file", type=Path)
    run.add_argument("--split", choices=["dev", "regression"], required=True)
    run.add_argument("--limit", type=int)
    run.add_argument("--seed", default="prefilter-20260919")
    run.add_argument("--workers", type=int, default=8)
    run.add_argument("--label", required=True)
    run.add_argument("--prompt", type=Path)
    run.add_argument("--reuse", type=Path)
    run.add_argument("--exclude-run", type=Path, action="append", default=[])
    run.add_argument("--smoke", action="store_true")
    run.add_argument("--quote-context", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "validate":
        from .object_entry import main as validate_main
        return validate_main(target=TARGET, benchmark=benchmark, argv=["validate", "--dataset", str(args.dataset)])
    from .cli import transport_factory
    config = read_json(args.config)
    factory = transport_factory(config, args.env_file)
    result = evaluate(args.dataset, config=config, split=args.split, limit=args.limit, seed=args.seed,
                      workers=args.workers, label=args.label, chat_factory=factory,
                      prompt=read_json(args.prompt) if args.prompt else None, smoke=args.smoke, reuse=args.reuse,
                      exclude_runs=tuple(args.exclude_run), benchmark=benchmark, quote_context=args.quote_context)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
