#!/usr/bin/env python3
"""Re-derive the aihot-fit gate floors from a full run, and write thresholds.json.

The floors go stale silently. A gate whose floor was set at a point estimate of 0.51 stops
firing once the metric sits at 0.71 -- it still reports "pass" on every run, including a run
that regressed by 0.10. So they are re-derived whenever a fitting round moves the readings, and
that has to be reproducible rather than a one-off transcription: the numbers below are read from
the metrics files, never retyped.

    scripts/derive_eval_fit_thresholds.py <full-run-dir> <gate-subset-dir> <out.json>

The gate subset is carved from the same full run by derive_eval_fit_subset.py, so its interval
is measured at the sample size the gate will actually be enforced at, on the current quality
level. Both are required: the floor is the full run's point estimate minus twice the subset's
half-width, and mixing runs would pair a point estimate with a width from a different code
version.

The reason metrics are the exception and are handled without a carved subset. Their gate
configuration (`--require-reference reason --limit 78`) runs over a pool of exactly 78, so it is
not a sample at all -- it is the whole reason population, which the full run already measures.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

GATE_AT_SUBSET = (
    "ai_recall",
    "category_agreement",
    "tag_jaccard_mean",
    "score_spearman",
    "summary_closeness_mean",
    "summary_bigram_jaccard",
)
GATE_AT_FULL_POPULATION = ("reason_closeness_mean", "reason_bigram_jaccard")
NOT_GATED = ("selected_auc", "selected_p_at_k")


def _metric(payload: dict, name: str) -> dict:
    try:
        return payload["metrics"][name]
    except KeyError:
        raise SystemExit(f"{name} is missing from {payload.get('run_id')}") from None


def _floor(point: float, half_width: float) -> dict:
    return {"min": round(point - 2 * half_width, 4), "detectable_regression": round(2 * half_width, 4)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("full_run", type=Path)
    parser.add_argument("gate_subset", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument(
        "--reason-subset-sha256",
        required=True,
        help="subset_sha256 of the reason gate configuration; it cannot be carved, so it is named",
    )
    args = parser.parse_args()

    full = json.loads((args.full_run / "metrics.json").read_text(encoding="utf-8"))
    gate = json.loads((args.gate_subset / "metrics.json").read_text(encoding="utf-8"))
    if full["questions_sha256"] != gate["questions_sha256"]:
        raise SystemExit("the two runs answer different question sets; floors would be meaningless")

    out: dict[str, object] = {}
    for name in GATE_AT_SUBSET:
        point, subset = _metric(full, name), _metric(gate, name)
        low, high = subset["ci95"]
        half = (high - low) / 2
        out[name] = {
            **_floor(point["value"], half),
            "run_config": gate["run_id"],
            "subset_sha256": gate["subset_sha256"],
            "basis": (
                f"{full['run_id']} point estimate {point['value']:.4f} minus twice the "
                f"{gate['run_id']} half-width {half:.4f} (n={subset['n']})"
            ),
        }
    for name in GATE_AT_FULL_POPULATION:
        point = _metric(full, name)
        low, high = point["ci95"]
        half = (high - low) / 2
        out[name] = {
            **_floor(point["value"], half),
            "run_config": "require-reference reason",
            "subset_sha256": args.reason_subset_sha256,
            "basis": (
                f"{full['run_id']} point estimate {point['value']:.4f} minus twice its own "
                f"half-width {half:.4f} (n={point['n']}); that population is the whole reason set, "
                f"which is what the gate configuration runs"
            ),
        }

    out["_meta"] = {
        "derived_at": full["computed_at"][:10],
        "evalset_questions_sha256": full["questions_sha256"],
        "full_run_readings": f"{args.full_run}/metrics.json",
        "gate_subset_readings": f"{args.gate_subset}/metrics.json",
        "rule": (
            "floor = full-run point estimate minus twice the half-width the gate configuration "
            "produces. The gate reads a CI lower bound and that bound moves with sqrt(n), so a "
            "floor taken from the full run would reject an unregressed subset run."
        ),
        "not_gated": {
            name: (
                f"full run reads {_metric(full, name)['value']:.4f} {_metric(full, name)['ci95']}; "
                f"the gate subset reads {_metric(gate, name)['value']:.4f} "
                f"{_metric(gate, name)['ci95']} -- too wide to gate on. Full run only."
            )
            for name in NOT_GATED
            if name in full["metrics"] and name in gate["metrics"]
        },
    }

    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    for name in sorted(k for k in out if not k.startswith("_")):
        entry = out[name]
        assert isinstance(entry, dict)
        print(f"  {name:<24} min={entry['min']:<8} detectable={entry['detectable_regression']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
