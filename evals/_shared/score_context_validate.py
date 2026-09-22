"""Freeze source-conditional weights on development, then validate on an existing regression cohort."""
from __future__ import annotations

import argparse
import signal
import time
from pathlib import Path

from airadar.scorer.five import five_score
from jinja2 import Template, StrictUndefined

from . import assets
from .cli import transport_factory
from .metrics import score
from .prefilter_eval import select_cases
from .score_context_analysis import source_role, OFFICIAL, OTHER
from .score_context_study import check_identity, arm_deadline
from .score_eval import evaluate, prompt_context
from .score_weights import source_rows, solve, overlap_keys
from .relocations import resolve_asset_path


def fit_all(run, root):
    _, metadata, cases, dims, hashes = source_rows(run, root=root)
    if metadata["split"] != "dev":
        raise ValueError("fit requires development outputs")
    y = [c["reference"]["score"] for c in cases]
    result = {"global": solve(dims, y), "branches": {}, "fit_cases": [c["case_id"] for c in cases],
              "fit_source_sha256": hashes, "fit_run": str(run),
              "fit_identity": metadata["object_identity"]}
    for role in ("organization-official", "individual-media-aggregator"):
        indices = [i for i,c in enumerate(cases) if source_role(c["input"]) == role]
        if indices:
            result["branches"][role] = solve([dims[i] for i in indices], [y[i] for i in indices])
    return result


def replay(run, mapping, label, root):
    source, old, cases, dims, hashes = source_rows(run, root=root)
    fit_cases = assets.read_jsonl(Path(mapping["fit_run"]) / "cases.jsonl")
    seen = set().union(*(overlap_keys(c) for c in fit_cases))
    if any(seen & overlap_keys(c) for c in cases):
        raise ValueError("regression overlaps fitting cases by ID/URL/body")
    if {k: v for k,v in mapping["fit_identity"].items() if k != "source_sha256"} != {
            k:v for k,v in old["object_identity"].items() if k != "source_sha256"}:
        raise ValueError("regression prompt/model/mapping differs from fitted object")
    results = {}
    for mode in ("global", "conditional"):
        predictions = []
        for c, d in zip(cases, dims, strict=True):
            fit = mapping["branches"].get(source_role(c["input"]), mapping["global"]) if mode == "conditional" else mapping["global"]
            predictions.append({"case_id": c["case_id"], "status": "ok",
                                "output": {"score": five_score(d, fit["weights_percent"])}})
        measured = score("O2", cases, predictions)
        for attempt in range(21):
            try:
                dest, experiment = assets.create_run(assets.ROOT, "visible-score", old["version"], benchmark=old["benchmark"])
                break
            except FileExistsError:
                if attempt == 20:
                    raise
                time.sleep(.1)
        metadata = {"target": "visible-score", "benchmark": old["benchmark"], "version": old["version"],
            "label": label+"-"+mode, "split": "regression", "mode": "conditional-replay", "source_run": str(source),
            "source_sha256": hashes, "mapping": mapping, "mapping_mode": mode, "new_model_calls": 0,
            "object_identity": {"base": old["object_identity"], "mapping_sha256": assets.digest(mapping), "mode": mode},
            "status": "complete", "started_at": assets.utc_now(), "scope": "previously exposed regression; not fresh blind",
            "unknown_source_count": sum(source_role(c["input"]) == "unknown" for c in cases),
            "analysis_sha256": assets.file_digest(Path(__file__))}
        assets.write_json(dest / "started.json", metadata)
        assets.write_jsonl(dest / "cases.jsonl", cases)
        assets.write_jsonl(dest / "predictions.jsonl", predictions)
        assets.archive_metrics(assets.ROOT, dest, experiment, measured, metadata)
        results[mode] = {"run": str(dest), "metrics": measured["metrics"], "unknown_source_count": metadata["unknown_source_count"]}
    assets.rebuild_index()
    return results


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for arg in ("dataset", "baseline-dev", "candidate-dev", "baseline-regression", "source-root", "support", "env-file"):
        p.add_argument("--"+arg, type=Path, required=True)
    a = p.parse_args(); a.support.mkdir(parents=True, exist_ok=True)
    arm_deadline()
    mappings = {"A11": fit_all(a.baseline_dev, a.source_root), "A12": fit_all(a.candidate_dev, assets.ROOT),
        "roles": {"organization-official": sorted(OFFICIAL), "individual-media-aggregator": sorted(OTHER)}}
    assets.write_json(a.support / "full-dev-weights-frozen.json", mappings)
    old = assets.read_json(a.baseline_regression / "started.json")
    old_cases = assets.read_jsonl(a.baseline_regression / "cases.jsonl")
    _, pool = assets.load_dataset(a.dataset, "visible-score")
    exclude_runs = tuple(resolve_asset_path(Path(e["run"]), root=a.source_root)
                         for e in old["selection"]["exclusions"])
    for entry, path in zip(old["selection"]["exclusions"], exclude_runs, strict=True):
        if assets.file_digest(path / "cases.jsonl") != entry["cases_sha256"]:
            raise ValueError("historical exclusions changed")
    excluded = frozenset(key for e in old["selection"]["exclusions"] for key in e["case_ids"])
    selected = select_cases(pool, "regression", len(old_cases), old["selection"]["seed"], excluded)
    if selected != old_cases:
        raise ValueError("regression cohort changed")
    original_prompt = assets.read_json(a.baseline_regression / "prompt.json")
    renderer = Template(original_prompt["user_template"], undefined=StrictUndefined)
    saved = {r["case_id"]: r["prompt"] for r in assets.read_jsonl(a.baseline_regression / "prompts.jsonl")}
    if any(renderer.render(**prompt_context(c["input"])) != saved[c["case_id"]]["user"] for c in selected):
        raise ValueError("baseline rendering changed")
    config = assets.read_json(assets.ROOT / "evals/_shared/configs/baseline-gateway.json")
    prompt = assets.read_json(assets.ROOT / "evals/visible-score/prompts/five-source-context-v1.json")
    check_identity(a.dataset, config, prompt, a.support, "A12-regression")
    with (a.support / "A12-regression.started").open("x") as f:
        f.write("Inspect durable attempts before recovery.\n")
    result = evaluate(a.dataset, config=config, prompt=prompt, split="regression", limit=len(old_cases),
        seed=old["selection"]["seed"], label="A12-source-context-regression", mode="five", workers=8,
        chat_factory=transport_factory(config, a.env_file), exclude_runs=exclude_runs)
    assets.write_json(a.support / "A12-regression.json", result)
    if not result["complete"]:
        raise RuntimeError("incomplete regression")
    summary = {"A11": replay(a.baseline_regression, mappings["A11"], "A11-source-rule", a.source_root),
               "A12": replay(Path(result["run"]), mappings["A12"], "A12-source-rule", assets.ROOT)}
    assets.write_json(a.support / "regression-analysis.json", summary)
    signal.alarm(0)


if __name__ == "__main__":
    main()
