"""Dev-fitted global affine score calibration; replay only, no model calls."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import median

from .assets import (
    ROOT,
    archive_metrics,
    create_run,
    digest,
    file_digest,
    read_json,
    read_jsonl,
    rebuild_index,
    slug,
    utc_now,
    write_json,
    write_jsonl,
)
from .metrics import score
from .relocations import resolve_asset_path

TARGET = "visible-score"
BENCHMARK = "aihot-score-pointwise"
FORMULA = "floor(clamp(scale * score + offset, 0, 100) + 0.5)"
SCALES = [value / 100 for value in range(20, 141, 2)]
SOURCE_FILES = ("started.json", "cases.jsonl", "predictions.jsonl", "scores.json")


def load_source(run: Path, *, root: Path = ROOT):
    run = resolve_asset_path(run.expanduser(), root=root).resolve()
    metadata = read_json(run / "started.json")
    if (metadata["target"], metadata["benchmark"]) != (TARGET, BENCHMARK):
        raise ValueError("requires a visible-score pointwise run")
    cases, predictions = read_jsonl(run / "cases.jsonl"), read_jsonl(run / "predictions.jsonl")
    if digest(cases) != metadata["case_identity"] or [c["case_id"] for c in cases] != metadata["case_ids"]:
        raise ValueError("source case identity mismatch")
    result = score("O2", cases, predictions)
    if result != read_json(run / "scores.json"):
        raise ValueError("source predictions or scores have changed")
    hashes = {name: file_digest(run / name) for name in SOURCE_FILES}
    return run, metadata, cases, predictions, result, hashes


def transform(value: int | float, scale: float, offset: float) -> int:
    """Prediction-only transformation; references are not accepted here."""
    if any(type(x) not in (int, float) or not math.isfinite(x) for x in (value, scale, offset)):
        raise ValueError("score, scale and offset must be finite numbers")
    if not 0 <= value <= 100 or scale < 0:
        raise ValueError("score must be 0..100 and scale nonnegative")
    return math.floor(min(100, max(0, scale * value + offset)) + 0.5)


def fit(run: Path, output: Path, *, root: Path = ROOT) -> dict:
    run, metadata, cases, predictions, result, hashes = load_source(run, root=root)
    if metadata["split"] != "dev" or any(case["split"] != "dev" for case in cases):
        raise ValueError("calibration may be fitted on dev only")
    if not cases or not result["complete"] or result["metrics"]["mae"]["denominator"] != len(cases):
        raise ValueError("fit requires a complete nonempty run with every case scored")
    indexed = {row["case_id"]: row for row in predictions}
    pairs = [(indexed[case["case_id"]]["output"]["score"], case["reference"]["score"]) for case in cases]
    candidates = []
    for scale in SCALES:
        offset = median(gold - scale * predicted for predicted, gold in pairs)
        mae = sum(abs(transform(predicted, scale, offset) - gold) for predicted, gold in pairs) / len(pairs)
        candidates.append((mae, abs(scale - 1), abs(offset), scale, offset))
    mae, _, _, scale, offset = min(candidates)
    mapping = {
        "schema_version": 1, "formula": FORMULA, "scale": scale, "offset": offset,
        "scale_grid": SCALES, "offset_fit": "median(gold - scale * prediction)",
        "tie_break": "MAE, abs(scale-1), abs(offset), scale, offset",
        "fit_run": str(run), "fit_split": "dev", "fit_case_ids": metadata["case_ids"],
        "fit_case_identity": metadata["case_identity"], "fit_source_sha256": hashes,
        "original_object_identity": metadata["object_identity"], "fit_mae": mae,
        "source_sha256": {"evals/_shared/score_calibration.py": file_digest(Path(__file__))},
        "created_at": utc_now(), "new_model_calls": 0,
    }
    mapping["mapping_id"] = digest(mapping)
    write_json(output.expanduser(), mapping)
    return mapping


def load_mapping(path: Path, *, root: Path = ROOT) -> dict:
    mapping = read_json(path.expanduser())
    if mapping["mapping_id"] != digest({key: value for key, value in mapping.items() if key != "mapping_id"}):
        raise ValueError("mapping identity mismatch")
    if mapping["schema_version"] != 1 or mapping["formula"] != FORMULA or mapping["fit_split"] != "dev":
        raise ValueError("unsupported mapping contract")
    transform(0, mapping["scale"], mapping["offset"])
    source = resolve_asset_path(Path(mapping["fit_run"]), root=root)
    if any(file_digest(source / name) != expected for name, expected in mapping["fit_source_sha256"].items()):
        raise ValueError("fit source artifact has changed")
    return mapping


def apply(run: Path, mapping_path: Path, *, label: str, root: Path = ROOT) -> dict:
    mapping = load_mapping(mapping_path, root=root)
    source, old, cases, predictions, _, hashes = load_source(run, root=root)
    if old["object_identity"] != mapping["original_object_identity"]:
        raise ValueError("source object identity differs from fitted object")
    for case in cases:
        slug(case["case_id"])
    mapped = []
    for prediction in predictions:
        row = {key: prediction[key] for key in ("case_id", "status", "error") if key in prediction}
        row["source_prediction"] = {"file": str(source / "predictions.jsonl"),
                                    "case_id": prediction["case_id"], "sha256": hashes["predictions.jsonl"]}
        row["output"] = None
        if prediction["status"] == "ok":
            try:
                row["output"] = {"score": transform(prediction["output"]["score"], mapping["scale"], mapping["offset"])}
            except (KeyError, TypeError, ValueError) as exc:
                row.update(status="error", error=type(exc).__name__)
        mapped.append(row)
    result = score("O2", cases, mapped)
    destination, experiment = create_run(root, TARGET, old["version"], benchmark=BENCHMARK)
    metadata = {
        "target": TARGET, "benchmark": BENCHMARK, "version": old["version"], "label": label,
        "split": old["split"], "smoke": old.get("smoke", False),
        "case_identity": old["case_identity"], "case_ids": old["case_ids"],
        "dataset": old.get("dataset"), "source_run": str(source), "source_files_sha256": hashes,
        "mapping": mapping, "mapping_file_sha256": file_digest(mapping_path),
        "original_object_identity": old["object_identity"],
        "object_identity": {"base": old["object_identity"], "mapping_id": mapping["mapping_id"],
                            "source_sha256": file_digest(Path(__file__))},
        "new_model_calls": 0, "cost_usd": 0, "cost_reason": "zero new calls; original inference cost belongs to source run",
        "started_at": utc_now(), "scope": "fixed dev-fitted mapping applied to unchanged source cases",
        "directory_timestamp_utc": "/".join(destination.parts[-2:]), "directory_time_source": "run_created",
    }
    write_json(destination / "started.json", metadata)
    write_jsonl(destination / "cases.jsonl", cases)
    write_jsonl(destination / "predictions.jsonl", mapped)
    for row in mapped:
        write_json(destination / "items" / f"{row['case_id']}.json", row)
    metadata.update(ended_at=utc_now(), status="complete" if result["complete"] else "incomplete")
    archive_metrics(root, destination, experiment, result, metadata)
    rebuild_index(root)
    return {"run": str(destination), "experiment": str(experiment), "complete": result["complete"],
            "metrics": result["metrics"], "new_model_calls": 0}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="零模型调用：仅用 dev 拟合统一仿射分数映射，再应用固定映射。")
    sub = parser.add_subparsers(dest="command", required=True)
    fit_parser = sub.add_parser("fit")
    fit_parser.add_argument("--run", type=Path, required=True)
    fit_parser.add_argument("--output", type=Path, required=True)
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--run", type=Path, required=True)
    apply_parser.add_argument("--mapping", type=Path, required=True)
    apply_parser.add_argument("--label", required=True)
    args = parser.parse_args(argv)
    result = fit(args.run, args.output) if args.command == "fit" else apply(args.run, args.mapping, label=args.label)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("complete", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
