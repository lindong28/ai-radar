"""Finite editorial dimension rules: fit on source-held dev training, then replay."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from airadar.scorer.five import five_score
from . import assets
from .score_calibration import load_source, TARGET, BENCHMARK
from .score_context_analysis import measurement, train_case
from .score_editorial import adjusted_dimensions, diagnosis
from .score_weights import source_rows, overlap_keys


def paired_inputs(baseline: Path, editorial: Path, source_root: Path):
    source, metadata, cases, dimensions, hashes = source_rows(baseline, root=source_root)
    from .relocations import resolve_asset_path
    other = resolve_asset_path(editorial)
    if assets.read_json(other / "started.json").get("kind") == "editorial-classification-only":
        from .score_editorial_classify import load
        auxiliary, rows, ah = load(other, cases, hashes)
        system = auxiliary["object_identity"]["prompt"]["editorial_system"]
    else:
        other, auxiliary, inputs, rows, result, ah = load_source(editorial)
        if cases != inputs or not result["complete"] or auxiliary["mode"] != "five-editorial":
            raise ValueError("requires complete, exactly paired two-call editorial outputs")
        system = assets.read_json(other / "prompt.json")["editorial_system"]
    indexed = {row["case_id"]: row for row in rows}
    original_prompts = {r["case_id"]: r["prompt"]["user"] for r in assets.read_jsonl(source / "prompts.jsonl")}
    labels = []
    for case in cases:
        call = indexed[case["case_id"]]["editorial_call"]
        if call["prompt"]["user"] != original_prompts[case["case_id"]]:
            raise ValueError("classifier must receive exactly the baseline input, without labels")
        labels.append(diagnosis(json.loads(call["response_json"]))["news_value_type"])
    return metadata, cases, dimensions, labels, {"baseline_run": str(source), "baseline_hashes": hashes,
        "editorial_run": str(other), "editorial_hashes": ah,
        "classifier_identity": {"request": auxiliary["object_identity"]["request"],
            "system": system}}


def values(cases, dimensions, labels, parameters):
    return [five_score(adjusted_dimensions(d, label, c["input"]["source_id"], **parameters))
            for c, d, label in zip(cases, dimensions, labels, strict=True)]


def views(cases, predictions):
    return {name: measurement([cases[i] for i in indices], [predictions[i] for i in indices])
            for name, indices in (("all", list(range(len(cases)))),
                ("training", [i for i, c in enumerate(cases) if train_case(c)]),
                ("heldout", [i for i, c in enumerate(cases) if not train_case(c)])) if indices}


def fit(baseline: Path, editorial: Path, output: Path, source_root: Path):
    metadata, cases, dimensions, labels, provenance = paired_inputs(baseline, editorial, source_root)
    if metadata["split"] != "dev" or not any(train_case(c) for c in cases) or all(train_case(c) for c in cases):
        raise ValueError("fit requires dev with both source partitions")
    candidates = []
    for family, pairs in (("promotion", [(3, None), (4, None)]),
                          ("official", [(None, 5), (None, 6)]),
                          ("combined", [(c, f) for c in (3, 4) for f in (5, 6)])):
        for cap, floor in pairs:
            parameters = {"promotion_cap": cap, "official_impact_floor": floor}
            candidates.append({"family": family, "parameters": parameters,
                "views": views(cases, values(cases, dimensions, labels, parameters))})
    selected = {}
    for family in ("promotion", "official", "combined"):
        def rank(row):
            metrics = row["views"]["training"]["metrics"]
            p = row["parameters"]
            return (metrics["mae"]["value"], -(metrics["spearman"]["value"] or 0),
                    -(p["promotion_cap"] or 10), p["official_impact_floor"] or 0)
        selected[family] = min((r for r in candidates if r["family"] == family), key=rank)
    result = {"provenance": provenance, "fit_case_ids": [c["case_id"] for c in cases if train_case(c)],
        "baseline": views(cases, [five_score(d) for d in dimensions]), "candidates": candidates,
        "selected": selected, "selection": "training MAE, descending rho, weaker bounds; heldout not fitted",
        "source_sha256": {p: assets.file_digest(assets.ROOT / p) for p in (
            "evals/_shared/score_editorial.py", "evals/_shared/score_editorial_rules.py",
            "evals/_shared/score_context_analysis.py")}}
    result["mapping_id"] = assets.digest(result)
    assets.write_json(output, result)
    return result


def replay(baseline: Path, editorial: Path, mapping: Path, family: str, source_root: Path):
    frozen = assets.read_json(mapping)
    if frozen["mapping_id"] != assets.digest({k: v for k, v in frozen.items() if k != "mapping_id"}):
        raise ValueError("mapping was changed")
    if any(assets.file_digest(assets.ROOT / p) != h for p, h in frozen["source_sha256"].items()):
        raise ValueError("rule implementation changed after fit")
    old, cases, dimensions, labels, provenance = paired_inputs(baseline, editorial, source_root)
    origin = frozen["provenance"]
    fitted, fit_cases, _, _, verified = paired_inputs(Path(origin["baseline_run"]), Path(origin["editorial_run"]), source_root)
    if verified != origin or provenance["classifier_identity"] != origin["classifier_identity"]:
        raise ValueError("fit provenance or classifier changed")
    if old["object_identity"] != fitted["object_identity"]:
        raise ValueError("baseline scoring object differs from the fitted object")
    if old["split"] == "regression":
        seen = set().union(*(overlap_keys(c) for c in fit_cases))
        if any(seen & overlap_keys(c) for c in cases):
            raise ValueError("regression overlaps development data")
    parameters = frozen["selected"][family]["parameters"]
    outputs = values(cases, dimensions, labels, parameters)
    predictions = [{"case_id": c["case_id"], "status": "ok", "output": {"score": y},
        "editorial_type": label, "original_dimensions": d,
        "adjusted_dimensions": adjusted_dimensions(d, label, c["input"]["source_id"], **parameters)}
        for c, d, label, y in zip(cases, dimensions, labels, outputs, strict=True)]
    result = measurement(cases, outputs)
    run, experiment = assets.create_run(assets.ROOT, TARGET, old["version"], benchmark=BENCHMARK)
    metadata = {"target": TARGET, "benchmark": BENCHMARK, "version": old["version"],
        "label": "P4-" + family, "mode": "editorial-rule-replay", "split": old["split"], "smoke": False,
        "case_identity": assets.digest(cases), "case_ids": [c["case_id"] for c in cases],
        "dataset": old["dataset"], "provenance": provenance, "rule_mapping": frozen,
        "object_identity": {"base": old["object_identity"], "mapping_id": frozen["mapping_id"], "parameters": parameters},
        "started_at": assets.utc_now(), "ended_at": assets.utc_now(), "status": "complete",
        "new_model_calls": 0, "cost_usd": 0,
        "cost_reason": "replay only; classifier and baseline calls are charged to source runs",
        "scope": "cached dimensions plus input-only classifier; seen regression is not blind"}
    assets.write_json(run / "started.json", metadata)
    assets.write_jsonl(run / "cases.jsonl", cases)
    assets.write_jsonl(run / "predictions.jsonl", predictions)
    assets.archive_metrics(assets.ROOT, run, experiment, result, metadata)
    assets.rebuild_index(assets.ROOT)
    return {"run": str(run), "experiment": str(experiment), "metrics": result["metrics"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("fit", "replay"))
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--editorial", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=assets.ROOT)
    parser.add_argument("--family", choices=("promotion", "official", "combined"), default="combined")
    args = parser.parse_args()
    result = (fit(args.baseline, args.editorial, args.mapping, args.source_root) if args.command == "fit" else
              replay(args.baseline, args.editorial, args.mapping, args.family, args.source_root))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
