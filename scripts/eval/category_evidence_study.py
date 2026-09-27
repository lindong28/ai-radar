"""Replay a frozen category cohort with a narrowly scoped evidence intervention."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from evals._shared import assets
from evals._shared.category_compare import collect, compare
from evals._shared.category_eval import evaluate
from evals._shared.cli import transport_factory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--arm", choices=["control", "documents", "evidence"], required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=assets.ROOT)
    parser.add_argument("--temperature", type=float, help="Override only this frozen request parameter")
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument("--thinking", choices=["disabled", "enabled"])
    parser.add_argument("--reasoning-effort", choices=["low", "medium", "high", "max"])
    args = parser.parse_args()
    cases, _, _, _ = collect([args.source_run])
    meta = assets.read_json(args.source_run / "started.json")
    behavior = meta["object_identity"]["behavior"]
    inputs = meta["object_identity"]["inputs"]
    if assets.file_digest(Path(meta["dataset"]) / "manifest.json") != inputs["dataset"]:
        raise ValueError("frozen dataset identity drift")
    if behavior["conditional_review"] or behavior["include_source_context"]:
        raise ValueError("study currently supports single-call controls without extra source-context")
    source_files = meta["object_identity"]["inputs"]["quote_sources"]
    for name, expected in source_files.items():
        if assets.file_digest(Path(name)) != expected:
            raise ValueError("frozen quote source identity drift")
    manifests = [Path(p).parent for p in source_files if Path(p).name == "manifest.json"]
    if len(manifests) > 1:
        raise ValueError("ambiguous frozen quote source")
    body = args.source_run / "body-context.jsonl"
    if inputs.get("body_context") and (not body.is_file() or assets.file_digest(body) != inputs["body_context"]):
        raise ValueError("study requires a fully frozen matching body sidecar")
    if body.exists() and not inputs.get("body_context"):
        raise ValueError("undeclared body sidecar")
    reviews = args.source_run / "human-reviews.json"
    if assets.file_digest(reviews) != meta["object_identity"]["inputs"]["human_reviews"]:
        raise ValueError("frozen human review identity drift")
    config = assets.read_json(args.source_run / "config.json")
    request = behavior["request"]
    if config["models"]["category"] != request["model"] or config["transport_identity"] != behavior["transport"]:
        raise ValueError("frozen transport or model drift")
    result = evaluate(Path(meta["dataset"]), config=config, split=meta["split"], limit=None,
        seed=meta["selection"]["seed"], label=args.label,
        chat_factory=transport_factory(config, args.env_file), rubric=behavior["rubric"],
        workers=8, root=args.output_root, quote_source=manifests[0] if manifests else None,
        body_limit=behavior["body_limit"], quote_contribution=behavior["quote_contribution"],
        quote_guidance=behavior["quote_guidance"],
        thinking=args.thinking if args.thinking is not None else behavior["thinking"],
        reasoning_effort=args.reasoning_effort if args.reasoning_effort is not None else request.get("reasoning_effort"),
        max_tokens=args.max_tokens if args.max_tokens is not None else request["max_tokens"],
        temperature=args.temperature if args.temperature is not None else request["temperature"],
        human_reviews=reviews, body_context=body if body.exists() else None,
        case_ids=set(cases), request_interval=1,
        material_layout="legacy" if args.arm == "control" else "documents",
        evidence_reason=args.arm == "evidence")
    run = Path(result["run"])
    comparison = compare([args.source_run], [run], reviews=reviews)
    assets.write_json(run / "comparison.json", comparison)
    print(json.dumps({"run": str(run), "arm": args.arm,
        "human": comparison["candidate"]["human_priority"]["metrics"],
        "paired": comparison["candidate_vs_baseline"]["all"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
