#!/usr/bin/env python3
"""Carve the `--limit N --seed S` subset out of a larger aihot-fit run.

Thresholds have to be derived at the sample size they will be enforced at: the gate asks
whether a run's CI lower bound clears the floor, and that bound moves with sqrt(n), so a
floor taken from a 2741-question run rejects a 300-question run that has not regressed at
all (0.4294 against a floor of 0.4673 for a metric sitting at 0.486). Re-running 300
questions would cost another 900 model calls for outputs the full run already contains, so
carve them out instead -- the sampler is deterministic, so the carved subset is byte-identical
to what `run --limit N --seed S` would have produced.

    scripts/derive_eval_fit_subset.py <full-run-dir> <out-dir> --limit 300 --seed 7
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from airadar.eval.aihot_fit.common import (  # noqa: E402
    DEFAULT_EVALSET_DIR,
    load_questions,
    read_json,
    read_jsonl,
    sha256_text,
    utc_now,
    write_json,
    write_jsonl,
)
from airadar.eval.aihot_fit.run import sample_questions  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--questions", type=Path, default=DEFAULT_EVALSET_DIR / "questions.jsonl")
    parser.add_argument("--limit", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args()

    run_meta = read_json(args.run_dir / "run.json")
    sampled = sample_questions(load_questions(args.questions), args.limit, args.seed)
    wanted_qids = {str(q["question_id"]) for q in sampled}
    item_ids = sorted(str(q["input"]["item_id"]) for q in sampled)

    covered = set(run_meta.get("item_ids") or [])
    missing = [i for i in item_ids if i not in covered]
    if missing:
        raise SystemExit(f"source run does not cover {len(missing)} of the sampled items; cannot carve")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    outputs = [r for r in read_jsonl(args.run_dir / "outputs.jsonl") if str(r["question_id"]) in wanted_qids]
    write_jsonl(args.out_dir / "outputs.jsonl", outputs)
    judgments_src = args.run_dir / "judgments.jsonl"
    judgments = 0
    if judgments_src.exists():
        rows = [r for r in read_jsonl(judgments_src) if str(r["question_id"]) in wanted_qids]
        write_jsonl(args.out_dir / "judgments.jsonl", rows)
        judgments = len(rows)
    for name in ("judge.json", "judge-calibration.json"):
        if (args.run_dir / name).exists():
            write_json(args.out_dir / name, read_json(args.run_dir / name))

    derived = dict(run_meta)
    derived.update(
        {
            "run_id": args.out_dir.name,
            "n": len(sampled),
            "limit": args.limit,
            "seed": args.seed,
            "item_ids": item_ids,
            "derived_from": run_meta.get("run_id"),
            "derived_at": utc_now(),
        }
    )
    write_json(args.out_dir / "run.json", derived)
    print(f"carved {len(outputs)} outputs and {judgments} judgments into {args.out_dir}")
    print(f"subset_sha256 = {sha256_text(chr(10).join(item_ids))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
