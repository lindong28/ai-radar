"""Fit five-dimensional weights on dev; replay frozen outputs without LLM calls."""
from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from pathlib import Path
from time import perf_counter

from airadar.scorer.five import FIVE_WEIGHTS, five_score, validated_weights

from .assets import (
    ROOT,
    archive_metrics,
    create_run,
    digest,
    file_digest,
    read_json,
    rebuild_index,
    slug,
    utc_now,
    write_json,
    write_jsonl,
)
from .metrics import score
from .score_calibration import BENCHMARK, TARGET, load_source

NAMES = tuple(FIVE_WEIGHTS)
FORMULA = "floor(sum(weight_percent * dimension) / 10 + 0.5)"


def continuous_score(payload: dict, weights: dict) -> float:
    return sum(payload[k] * weights[k] for k in NAMES) / 10


def solve(payloads: list[dict], references: list[float], *, method: str = "lad",
          lower: float = 5, upper: float = 60) -> dict:
    """LAD minimizes unrounded MAE; the grid comparator minimizes rounded MAE."""
    if not payloads or len(payloads) != len(references):
        raise ValueError("requires nonempty, paired dimensions and references")
    for payload in payloads:
        five_score(payload)
    if any(type(y) not in (int, float) or not math.isfinite(y) or not 0 <= y <= 100 for y in references):
        raise ValueError("reference scores must be finite numbers in 0..100")
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in (lower, upper)):
        raise ValueError("bounds must be finite numbers")
    if not 0 <= lower <= 20 <= upper <= 100:
        raise ValueError("bounds must admit five nonnegative weights summing to 100")
    if method not in ("lad", "grid"):
        raise ValueError("method must be lad or grid")
    if method == "lad":
        try:
            import numpy as np
            import scipy
            from scipy.optimize import linprog
            from scipy.sparse import csr_matrix, eye, hstack, vstack
        except ImportError as exc:
            raise ValueError("LAD requires SciPy: run with uv run --with scipy==1.17.1") from exc
        # O(n*d) sparse storage, not an n-by-n dense residual matrix.
        x = csr_matrix([[p[k] / 10 for k in NAMES] for p in payloads])
        y = np.array(references)
        n = len(y)
        a = vstack([hstack([x, -eye(n)]), hstack([-x, -eye(n)])]).tocsr()
        started = perf_counter()
        solution = linprog(
            np.r_[np.zeros(5), np.full(n, 1 / n)], A_ub=a, b_ub=np.r_[y, -y],
            A_eq=csr_matrix([[1] * 5 + [0] * n]), b_eq=[100],
            bounds=[(lower, upper)] * 5 + [(0, None)] * n, method="highs",
            options={"time_limit": 60},
        )
        elapsed = perf_counter() - started
        if not solution.success:
            raise ValueError(f"weight optimization failed: {solution.status}: {solution.message}")
        values = [float(v) for v in solution.x[:5]]
        # Preserve the solver's values, only correct floating-point sum drift.
        values[4] = 100 - sum(values[:4])
        weights = validated_weights(dict(zip(NAMES, values, strict=True)))
        if any(not lower - 1e-8 <= v <= upper + 1e-8 for v in values):
            raise ValueError("solver returned weights outside bounds")
        diagnostic = {"solver": "scipy.optimize.linprog/highs", "scipy_version": scipy.__version__,
                      "objective": "unrounded_mae", "solver_objective": float(solution.fun),
                      "solver_iterations": int(solution.nit), "fit_seconds": elapsed}
    else:
        if (lower, upper) != (5, 60):
            raise ValueError("historical grid comparator requires bounds 5..60")
        started = perf_counter()
        best = None
        count = 0
        for first in itertools.product(range(5, 61, 5), repeat=4):
            last = 100 - sum(first)
            if not 5 <= last <= 60:
                continue
            values = (*first, last)
            candidate = dict(zip(NAMES, values, strict=True))
            # Inputs validated above; identical arithmetic/order to five_score.
            mae = sum(abs(math.floor(continuous_score(p, candidate) + .5) - y)
                      for p, y in zip(payloads, references, strict=True)) / len(payloads)
            key = (mae, sum(abs(candidate[k] - FIVE_WEIGHTS[k]) for k in NAMES), values)
            count += 1
            if best is None or key < best:
                best = key
        weights = dict(zip(NAMES, best[2], strict=True))
        diagnostic = {"solver": "historical-5-percentage-point-grid", "objective": "rounded_mae",
                      "candidate_count": count, "fit_seconds": perf_counter() - started,
                      "tie_break": "rounded_mae, L1 distance to FIVE_WEIGHTS, lexicographic weights"}
    continuous_mae = sum(abs(continuous_score(p, weights) - y)
                         for p, y in zip(payloads, references, strict=True)) / len(payloads)
    if method == "lad" and not math.isclose(continuous_mae, diagnostic["solver_objective"], abs_tol=1e-7):
        raise ValueError("solver objective does not reproduce with returned weights")
    return {"weights_percent": weights, "bounds_percent": [lower, upper], **diagnostic,
            "unrounded_mae": continuous_mae,
            "rounded_mae": sum(abs(five_score(p, weights) - y)
                               for p, y in zip(payloads, references, strict=True)) / len(payloads)}


def source_rows(run: Path, *, root: Path = ROOT):
    source, metadata, cases, predictions, result, hashes = load_source(run, root=root)
    if metadata.get("mode") != "five" or metadata["object_identity"].get("mode") != "five":
        raise ValueError("requires archived five-dimensional, single-call outputs")
    if not cases or not result["complete"] or result["metrics"]["mae"]["denominator"] != len(cases):
        raise ValueError("requires a complete nonempty run, with every case scored")
    if metadata["split"] not in ("dev", "regression") or any(c["split"] != metadata["split"] for c in cases):
        raise ValueError("source split is inconsistent")
    weights = validated_weights(metadata["object_identity"]["mapping"]["weights_percent"])
    indexed = {row["case_id"]: row for row in predictions}
    payloads = []
    for case in cases:
        row = indexed[case["case_id"]]
        payload = json.loads(row["response_json"])
        if five_score(payload, weights) != row["output"]["score"]:
            raise ValueError("archived dimensions do not reproduce source scores")
        payloads.append(payload)
    return source, metadata, cases, payloads, hashes


def fit(run: Path, output: Path, *, method: str = "lad", lower: float = 5, upper: float = 60,
        root: Path = ROOT) -> dict:
    output = output.expanduser()
    if output.exists():
        raise FileExistsError("refusing to overwrite frozen weights")
    source, metadata, cases, payloads, hashes = source_rows(run, root=root)
    if metadata["split"] != "dev":
        raise ValueError("weight fitting requires dev; regression cannot choose weights")
    optimization = solve(payloads, [c["reference"]["score"] for c in cases], method=method, lower=lower, upper=upper)
    mapping = {"schema_version": 1, "formula": FORMULA, "fit_run": str(source), "fit_split": "dev",
               "fit_source_sha256": hashes, "original_object_identity": metadata["object_identity"],
               "optimization": optimization, "created_at": utc_now(), "new_model_calls": 0,
               "source_sha256": file_digest(Path(__file__))}
    mapping["mapping_id"] = digest(mapping)
    write_json(output, mapping)
    return mapping


def load_mapping(path: Path, *, root: Path = ROOT) -> tuple[dict, list[dict]]:
    mapping = read_json(path.expanduser())
    if mapping["mapping_id"] != digest({k: v for k, v in mapping.items() if k != "mapping_id"}):
        raise ValueError("weight mapping identity mismatch")
    if (mapping["schema_version"], mapping["formula"], mapping["fit_split"]) != (1, FORMULA, "dev"):
        raise ValueError("unsupported weight mapping contract")
    _, metadata, cases, _, hashes = source_rows(Path(mapping["fit_run"]), root=root)
    if hashes != mapping["fit_source_sha256"] or metadata["object_identity"] != mapping["original_object_identity"]:
        raise ValueError("fit source has changed")
    validated_weights(mapping["optimization"]["weights_percent"])
    return mapping, cases


def overlap_keys(case: dict) -> set[tuple[str, str]]:
    keys = {("case_id", case["case_id"])}
    for name in ("url", "content_text"):
        value = case.get("input", {}).get(name)
        if value:
            keys.add((name, value))
    return keys


def replay(run: Path, mapping_path: Path, *, label: str, root: Path = ROOT) -> dict:
    mapping, fitted_cases = load_mapping(mapping_path, root=root)
    source, old, cases, payloads, hashes = source_rows(run, root=root)
    if old["object_identity"] != mapping["original_object_identity"]:
        raise ValueError("source object differs from the fitted object")
    if old["split"] == "regression":
        seen = set().union(*(overlap_keys(case) for case in fitted_cases))
        if any(seen & overlap_keys(case) for case in cases):
            raise ValueError("regression overlaps fit data by ID, URL or exact body")
    weights = mapping["optimization"]["weights_percent"]
    predictions = []
    for case, payload in zip(cases, payloads, strict=True):
        slug(case["case_id"])
        predictions.append({"case_id": case["case_id"], "status": "ok",
                            "output": {"score": five_score(payload, weights)},
                            "source_prediction": {"file": str(source / "predictions.jsonl"),
                                                  "case_id": case["case_id"], "sha256": hashes["predictions.jsonl"]}})
    result = score("O2", cases, predictions)
    destination, experiment = create_run(root, TARGET, old["version"], benchmark=BENCHMARK)
    metadata = {
        "target": TARGET, "benchmark": BENCHMARK, "version": old["version"], "label": label,
        "mode": "five-weight-replay", "split": old["split"], "smoke": old.get("smoke", False),
        "case_identity": old["case_identity"], "case_ids": old["case_ids"], "dataset": old.get("dataset"),
        "source_run": str(source), "source_files_sha256": hashes, "weight_mapping": mapping,
        "object_identity": {"base": old["object_identity"], "mapping_id": mapping["mapping_id"],
                            "source_sha256": file_digest(Path(__file__))},
        "new_model_calls": 0, "cost_usd": 0, "cost_reason": "zero new calls; inference cost remains in source run",
        "scope": "frozen dimensions, dev-fitted weights; reused regression is not fresh blind validation",
        "started_at": utc_now(), "directory_timestamp_utc": "/".join(destination.parts[-2:]),
        "directory_time_source": "run_created",
    }
    write_json(destination / "started.json", metadata)
    write_jsonl(destination / "cases.jsonl", cases)
    write_jsonl(destination / "predictions.jsonl", predictions)
    metadata.update(ended_at=utc_now(), status="complete")
    archive_metrics(root, destination, experiment, result, metadata)
    rebuild_index(root)
    return {"run": str(destination), "experiment": str(experiment), "complete": result["complete"],
            "metrics": result["metrics"], "new_model_calls": 0}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="开发集拟合五维权重，零新增模型调用重放。不会更改原run或生产。")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("fit")
    p.add_argument("--run", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--method", choices=("lad", "grid"), default="lad")
    p.add_argument("--lower", type=float, default=5)
    p.add_argument("--upper", type=float, default=60)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("replay")
    p.add_argument("--run", type=Path, required=True)
    p.add_argument("--mapping", type=Path, required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "fit":
            result = fit(args.run, args.output, method=args.method, lower=args.lower, upper=args.upper)
            message = (f"权重已冻结：{args.output}；开发集整数MAE={result['optimization']['rounded_mae']:.3f}。"
                       "尚未验证泛化，未改生产，新增模型调用0。")
        else:
            result = replay(args.run, args.mapping, label=args.label)
            message = (f"重放完成：{result['run']}；MAE={result['metrics']['mae']['value']:.3f}，"
                       f"Spearman={result['metrics']['spearman']['value']}。仅该冻结题集，新增模型调用0。")
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"权重实验失败：{exc}。未更改原run或生产；修正输入后使用新的输出路径重试。", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
