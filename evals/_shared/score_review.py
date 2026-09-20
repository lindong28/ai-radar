"""Build a local, read-only score review page from archived pointwise runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .assets import ROOT, digest, file_digest, read_json, read_jsonl
from .score_calibration import load_source

CATEGORIES = ("疑似参考分", "疑似Radar错误", "输入缺口", "待判断")
PAGE_DIR = Path(__file__).resolve().parents[1] / "visible-score"


def _indexed(rows: list[dict], description: str) -> dict[str, dict]:
    result = {row["case_id"]: row for row in rows}
    if len(result) != len(rows):
        raise ValueError(f"duplicate case_id in {description}")
    return result


def _load_run(path: Path, *, root: Path) -> dict:
    run, metadata, cases, predictions, _, hashes = load_source(path, root=root)
    prompts = {}
    prompt_path = run / "prompts.jsonl"
    if prompt_path.is_file():
        rows = _indexed(read_jsonl(prompt_path), "prompts")
        prompts = {key: row["prompt"] for key, row in rows.items()}
        expected = metadata.get("rendered_prompts_sha256")
        if expected is not None and digest(prompts) != expected:
            raise ValueError("source rendered prompt identity mismatch")
    indexed_predictions = _indexed(predictions, "predictions")
    for key, prediction in indexed_predictions.items():
        if key in prompts and prediction.get("prompt") is not None and prediction["prompt"] != prompts[key]:
            raise ValueError(f"prediction prompt differs from archived prompt: {key}")
    return {
        "run": str(run), "metadata": metadata, "cases": _indexed(cases, "cases"),
        "predictions": indexed_predictions, "prompts": prompts, "hashes": hashes,
    }


def _prediction(run: dict, key: str) -> dict:
    prediction = run["predictions"].get(key)
    prompt = prediction.get("prompt") if prediction else None
    if prompt is None:
        prompt = run["prompts"].get(key)
    return {"prediction": prediction, "prompt": prompt}


def _summary(run: dict) -> dict:
    metadata = run["metadata"]
    return {
        "run": run["run"], "label": metadata.get("label") or Path(run["run"]).name,
        "mode": metadata.get("mode"),
        "cases_sha256": run["hashes"]["cases.jsonl"],
        "predictions_sha256": run["hashes"]["predictions.jsonl"],
    }


def build_payload(source_run: Path, analysis_json: Path, *,
                  comparison_runs: tuple[Path, ...] = (), root: Path = ROOT) -> dict:
    """Select only the supplied analyses, in their order; never infer a human vote."""
    source = _load_run(source_run, root=root)
    analyses = read_json(analysis_json)
    if not isinstance(analyses, list) or not analyses:
        raise ValueError("analysis JSON must be a nonempty array")
    seen = set()
    for row in analyses:
        if not isinstance(row, dict) or not {"case_id", "category", "analysis"} <= row.keys():
            raise ValueError("each analysis requires case_id, category and analysis")
        key = row["case_id"]
        if not isinstance(key, str) or key not in source["cases"]:
            raise ValueError(f"analysis case_id is absent from source run: {key!r}")
        if key in seen:
            raise ValueError(f"duplicate analysis case_id: {key}")
        seen.add(key)
        if row["category"] not in CATEGORIES:
            raise ValueError(f"unsupported analysis category: {row['category']!r}")
        if not isinstance(row["analysis"], str) or not row["analysis"].strip():
            raise ValueError("analysis must be nonempty text")
        if "counterargument" in row and not isinstance(row["counterargument"], str):
            raise ValueError("counterargument must be text when supplied")

    comparisons = [_load_run(path, root=root) for path in comparison_runs]
    for comparison in comparisons:
        if comparison["metadata"]["version"] != source["metadata"]["version"]:
            raise ValueError("comparison benchmark version differs from source")
        for key in seen & comparison["cases"].keys():
            if comparison["cases"][key] != source["cases"][key]:
                raise ValueError(f"comparison case identity differs from source: {key}")

    summary = _summary(source)
    comparison_summaries = [_summary(run) for run in comparisons]
    batch_sha256 = digest({"source": summary, "analyses": analyses, "comparisons": comparison_summaries})
    metadata = {
        "target": source["metadata"]["target"], "benchmark": source["metadata"]["benchmark"],
        "version": source["metadata"]["version"], "source_run": source["run"],
        "source_cases_sha256": summary["cases_sha256"],
        "source_predictions_sha256": summary["predictions_sha256"],
        "batch_id": f"score-review-{batch_sha256[:16]}", "batch_sha256": batch_sha256,
    }
    return {
        "metadata": metadata, "source": summary, "comparisons": comparison_summaries,
        "categories": list(CATEGORIES),
        "cases": [
            {
                "case_id": row["case_id"], "category": row["category"],
                "analysis": row["analysis"], "counterargument": row.get("counterargument"),
                "case": source["cases"][row["case_id"]], **_prediction(source, row["case_id"]),
                "comparisons": [_prediction(run, row["case_id"]) for run in comparisons],
            }
            for row in analyses
        ],
    }


def render_html(payload: dict) -> str:
    # JSON is data inside a script element, not executable HTML or JavaScript.
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    for character, escaped in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"),
                               ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        encoded = encoded.replace(character, escaped)
    template = (PAGE_DIR / "score-review.html").read_text(encoding="utf-8")
    script = (PAGE_DIR / "score-review.js").read_text(encoding="utf-8")
    return template.replace("/* SCORE_REVIEW_SCRIPT */", script, 1).replace("__REVIEW_DATA__", encoded, 1)


def generate(source_run: Path, analysis_json: Path, output_dir: Path, *,
             comparison_runs: tuple[Path, ...] = (), root: Path = ROOT) -> dict:
    payload = build_payload(source_run, analysis_json, comparison_runs=comparison_runs, root=root)
    output = output_dir.expanduser().resolve() / "index.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(render_html(payload))
    return {"output": str(output), "cases": len(payload["cases"]), "metadata": payload["metadata"],
            "html_sha256": file_digest(output)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成只读评分题复核页；不修改 gold 或人评标签。")
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--analysis-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--comparison-run", type=Path, action="append", default=[])
    args = parser.parse_args(argv)
    result = generate(args.source_run, args.analysis_json, args.output_dir,
                      comparison_runs=tuple(args.comparison_run))
    print(f"已生成 {result['cases']} 条评分题复核页：{result['output']}")
    print("复核票须整批回传后确认；尚未写入人评标签或修改题库。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
