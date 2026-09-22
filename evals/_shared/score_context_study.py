"""Bounded source-context ablation through the existing pointwise runner."""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
from pathlib import Path

from jinja2 import StrictUndefined, Template

from . import assets
from .cli import transport_factory
from .prefilter_eval import select_cases
from .score_eval import evaluate, object_identity, prompt_context
from .relocations import resolve_asset_path


def arm_deadline(seconds: int = 1800):
    """Terminate this offline process, including its queued worker threads.

    Durable attempts already exist before network spend. On deadline those still
    in flight remain unresolved, not successful or free; recovery is explicit.
    """
    signal.signal(signal.SIGALRM, lambda *_: os._exit(124))
    signal.alarm(seconds)


def check_identity(dataset: Path, config: dict, prompt: dict, support: Path, label: str,
                   mode: str = "five", authority: str = "user-approved offline source ablation; ADR b82e") -> dict:
    """Freeze identity before spend, then compare a separate disk read via eval-identity."""
    frozen = object_identity(config, prompt, mode)
    snapshot = support / f"{label}-frozen.json"
    if snapshot.exists():
        if assets.read_json(snapshot) != frozen:
            raise ValueError("frozen object changed; use a new label")
    else:
        assets.write_json(snapshot, frozen)
    actual = object_identity(config, prompt, mode)
    dep = assets.digest(frozen["source_sha256"])
    fields = ["request", "prompt", "mode", "mapping", "transport", "thinking", "retry_count", "fallback"]
    manifest = assets.read_json(dataset / "manifest.json")
    spec = {"run": label, "at": "before paid calls", "baseline": {
        "id": label, "kind": "local_source", "authority": authority},
        "sources": [
            {"id": "dep", "role": "deploy_version", "name": "runner", "count": 1, "origin": "frozen source hashes"},
            {"id": "cfg", "role": "effective_config", "name": "behavior", "count": 8, "origin": "frozen candidate"},
            {"id": "ast", "role": "input_assets", "name": "cases", "count": 1, "origin": "benchmark manifest"}],
        "anchors": [
            {"field": "deploy_version", "source": "dep", "covers": 1,
             "base": {"value": dep, "provenance": "pre-spend frozen contract", "locator": str(snapshot)},
             "run": {"value": assets.digest(actual["source_sha256"]), "provenance": "runtime source re-read"}},
            {"field": "effective_config", "source": "cfg", "covers": 8,
             "base": {"value": assets.digest({k: frozen[k] for k in fields}), "deploy_ref": dep, "provenance": "frozen behavior"},
             "run": {"value": assets.digest({k: actual[k] for k in fields}), "provenance": "current effective behavior"}},
            {"field": "cases_sha256", "source": "ast", "covers": 1,
             "base": {"value": manifest["files"]["cases.jsonl"], "deploy_ref": dep, "provenance": "published manifest"},
             "run": {"value": assets.file_digest(dataset / "cases.jsonl"), "provenance": "current input bytes"}}],
        "exclusions": [], "cells": dict.fromkeys([
            "isolation_confirmed", "isolation_unconfirmed", "nonessential_confirmed", "nonessential_unconfirmed"], 0),
        "confirmations": []}
    spec_path = support / f"{label}-identity.json"
    assets.write_json(spec_path, spec)
    checked = subprocess.run([str(Path.home() / ".claude/bin/eval-identity"), str(spec_path)],
                             text=True, capture_output=True)
    (support / f"{label}-identity.txt").write_text(checked.stdout + checked.stderr)
    checked.check_returncode()
    print(checked.stdout, flush=True)
    return frozen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--baseline-run", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=assets.ROOT)
    parser.add_argument("--support", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--mode", choices=("five", "five-editorial"), default="five")
    parser.add_argument("--authority", default="user-approved offline source ablation; ADR b82e")
    args = parser.parse_args()
    arm_deadline()
    config = assets.read_json(assets.ROOT / "evals/_shared/configs/baseline-gateway.json")
    prompt = assets.read_json(args.prompt)
    baseline = assets.read_json(args.baseline_run / "started.json")
    original = assets.read_jsonl(args.baseline_run / "cases.jsonl")
    _, pool = assets.load_dataset(args.dataset, "visible-score")
    seed = baseline["selection"]["seed"]
    exclusions = tuple(resolve_asset_path(Path(row["run"]), root=args.source_root)
                       for row in baseline["selection"].get("exclusions", []))
    excluded = frozenset(c["case_id"] for path in exclusions for c in assets.read_jsonl(path / "cases.jsonl"))
    selected = select_cases(pool, baseline["split"], len(original), seed, excluded)
    if selected != original or baseline["split"] not in ("dev", "regression"):
        raise ValueError("requires exact baseline cases")
    # Prove expanding the allowlist did not change the original control prompts.
    old_prompt = assets.read_json(args.baseline_run / "prompt.json")
    template = Template(old_prompt["user_template"], undefined=StrictUndefined)
    old_rows = {p["case_id"]: p for p in assets.read_jsonl(args.baseline_run / "prompts.jsonl")}
    for case in original:
        if template.render(**prompt_context(case["input"])) != old_rows[case["case_id"]]["prompt"]["user"]:
            raise ValueError("baseline rendered prompt changed")
    factory = transport_factory(config, args.env_file)
    frozen = check_identity(args.dataset, config, prompt, args.support, args.label, args.mode, args.authority)
    for count, smoke in ((3, True), (len(original), False)):
        output = args.support / f"{args.label}-{count}.json"
        if output.exists():
            raise FileExistsError("existing result; do not silently repeat paid work")
        if object_identity(config, assets.read_json(args.prompt), args.mode) != frozen:
            raise ValueError("candidate changed after identity check")
        # Persist before any paid attempt; an interrupted batch needs explicit recovery.
        with (args.support / f"{args.label}-{count}.started").open("x") as marker:
            marker.write("Inspect durable attempts before recovery; do not rerun this label.\n")
        result = evaluate(args.dataset, config=config, prompt=prompt, split=baseline["split"], limit=count,
                          seed=seed, label=args.label + ("-smoke" if smoke else ""), mode=args.mode,
                          workers=args.workers, smoke=smoke, chat_factory=factory, exclude_runs=exclusions)
        assets.write_json(output, result)
        if not result["complete"]:
            raise RuntimeError("incomplete; inspect and explicitly resume failures")
    signal.alarm(0)


if __name__ == "__main__":
    main()
