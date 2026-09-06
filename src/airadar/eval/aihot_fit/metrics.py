"""Deterministic metrics (with bootstrap CIs) for an aihot-fit run."""

from __future__ import annotations

import random
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ...enrich.normalizers.production_enrich_provider_output_v2 import (
    AIHOT_TO_RADAR_TAG_MAP,
    is_in_v2_vocabulary,
)
from .common import (
    METRICS_SCHEMA_VERSION,
    PRIMARY_CATEGORIES,
    load_questions,
    read_json,
    read_jsonl,
    sha256_text,
    utc_now,
    write_json,
)

BOOTSTRAP_ROUNDS = 1000
BOOTSTRAP_SEED = 0
# Fraction of bootstrap rounds that must produce a value before a CI is reported.
BOOTSTRAP_MIN_KEPT_RATIO = 0.95
SHUFFLE_ROUNDS = 100
HIGHER = "higher_is_better"

# Reference (AIHOT) tag -> our controlled vocabulary. Spec-listed aliases layered on the
# production map; anything still outside the v2 vocabulary is dropped and counted.
REFERENCE_TAG_MAP: dict[str, str] = {
    **AIHOT_TO_RADAR_TAG_MAP,
    "MCP": "MCP/工具",
    "工具调用": "MCP/工具",
}


@dataclass
class Joined:
    question_id: str
    reference: dict[str, Any]
    prefilter: dict[str, Any] | None = None
    score: dict[str, Any] | None = None
    weighted_score: float | None = None
    enrich: dict[str, Any] | None = None
    judgments: dict[str, int] = field(default_factory=dict)


@dataclass
class Metric:
    name: str
    n: int
    value: float | None
    ci95: tuple[float, float] | None
    direction: str = HIGHER
    baseline: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "value": self.value,
            "ci95": list(self.ci95) if self.ci95 else None,
            "direction": self.direction,
            "baseline": self.baseline,
            **self.extra,
        }


def _stage_output(row: dict[str, Any], stage: str) -> dict[str, Any] | None:
    record = row.get(stage)
    if not isinstance(record, dict) or record.get("error") or not isinstance(record.get("output"), dict):
        return None
    return record["output"]


def join_rows(
    questions: Sequence[dict[str, Any]],
    outputs: Sequence[dict[str, Any]],
    judgments: Sequence[dict[str, Any]] = (),
) -> list[Joined]:
    by_id = {str(question["question_id"]): question for question in questions}
    scores: dict[str, dict[str, int]] = defaultdict(dict)
    for judgment in judgments:
        if judgment.get("closeness") is not None and not judgment.get("control"):
            scores[str(judgment["question_id"])][str(judgment["dimension"])] = int(judgment["closeness"])
    joined: list[Joined] = []
    for row in outputs:
        question = by_id.get(str(row["question_id"]))
        if question is None or row.get("skipped"):
            continue
        score_record = row.get("score") if isinstance(row.get("score"), dict) else None
        joined.append(
            Joined(
                question_id=str(row["question_id"]),
                reference=question["reference"],
                prefilter=_stage_output(row, "prefilter"),
                score=_stage_output(row, "score"),
                weighted_score=score_record.get("weighted_score") if score_record else None,
                enrich=_stage_output(row, "enrich"),
                judgments=dict(scores.get(str(row["question_id"]), {})),
            )
        )
    return joined


def bootstrap_ci(
    items: Sequence[Any],
    statistic: Callable[[Sequence[Any]], float | None],
    *,
    rounds: int = BOOTSTRAP_ROUNDS,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float] | None:
    if len(items) < 2:
        return None
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(rounds):
        resample = [items[rng.randrange(len(items))] for _ in items]
        value = statistic(resample)
        if value is not None:
            samples.append(value)
    # A resample can be degenerate (AUC with no positives, Spearman with zero variance).
    # Taking quantiles over only the surviving rounds silently reports an interval
    # conditioned on "the resample was well-formed" while the payload still says
    # rounds=1000 — measured at 338/1000 dropped for n=20 with a single positive.
    # Below the floor there is no trustworthy interval, so report none rather than a
    # narrower one that reads identical to a real result.
    if len(samples) < rounds * BOOTSTRAP_MIN_KEPT_RATIO:
        return None
    samples.sort()
    lower = samples[int(0.025 * (len(samples) - 1))]
    upper = samples[int(0.975 * (len(samples) - 1))]
    return (round(lower, 4), round(upper, 4))


def _mean(values: Sequence[float]) -> float | None:
    return round(statistics.fmean(values), 4) if values else None


def _mean_metric(name: str, values: Sequence[float], **extra: Any) -> Metric:
    return Metric(name=name, n=len(values), value=_mean(values), ci95=bootstrap_ci(values, _mean), extra=extra)


# --- prefilter ---------------------------------------------------------------


def ai_recall(rows: Sequence[Joined]) -> Metric:
    values = [1.0 if row.prefilter.get("is_ai_related") else 0.0 for row in rows if row.prefilter is not None]
    metric = _mean_metric("ai_recall", values)
    metric.baseline = {
        "kind": "none",
        "note": "all questions come from AIHOT and are treated as positives; no negatives",
    }
    return metric


# --- classification ----------------------------------------------------------


def category_pairs(rows: Sequence[Joined]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for row in rows:
        expected = row.reference.get("primary_category")
        predicted = row.enrich.get("primary_category") if row.enrich else None
        if expected in PRIMARY_CATEGORIES and predicted in PRIMARY_CATEGORIES:
            pairs.append((str(expected), str(predicted)))
    return pairs


def category_agreement(rows: Sequence[Joined]) -> Metric:
    pairs = category_pairs(rows)
    values = [1.0 if expected == predicted else 0.0 for expected, predicted in pairs]
    metric = _mean_metric("category_agreement", values)
    majority = Counter(expected for expected, _ in pairs).most_common(1)
    metric.baseline = {
        "kind": "majority_class",
        "value": round(majority[0][1] / len(pairs), 4) if pairs else None,
        "label": majority[0][0] if majority else None,
    }
    matrix = {expected: {predicted: 0 for predicted in PRIMARY_CATEGORIES} for expected in PRIMARY_CATEGORIES}
    for expected, predicted in pairs:
        matrix[expected][predicted] += 1
    metric.extra["confusion_matrix"] = {"rows": "reference", "columns": "predicted", "counts": matrix}
    return metric


# --- tags --------------------------------------------------------------------


def map_reference_tags(tags: Sequence[str] | None) -> tuple[set[str], int]:
    mapped: set[str] = set()
    dropped = 0
    for tag in tags or ():
        candidate = REFERENCE_TAG_MAP.get(str(tag), str(tag))
        if is_in_v2_vocabulary(candidate):
            mapped.add(candidate)
        else:
            dropped += 1
    return mapped, dropped


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def tag_pairs(rows: Sequence[Joined]) -> tuple[list[tuple[set[str], set[str]]], int]:
    pairs: list[tuple[set[str], set[str]]] = []
    dropped_total = 0
    for row in rows:
        if not row.reference.get("tags") or row.enrich is None:
            continue
        reference, dropped = map_reference_tags(row.reference["tags"])
        dropped_total += dropped
        if not reference:
            continue
        ours = {str(tag) for tag in row.enrich.get("tags") or []}
        pairs.append((ours, reference))
    return pairs, dropped_total


def tag_jaccard_mean(rows: Sequence[Joined]) -> Metric:
    pairs, dropped = tag_pairs(rows)
    values = [_jaccard(ours, reference) for ours, reference in pairs]
    metric = _mean_metric("tag_jaccard_mean", values, reference_tags_dropped_out_of_vocabulary=dropped)
    if len(pairs) >= 2:
        rng = random.Random(BOOTSTRAP_SEED)
        references = [reference for _, reference in pairs]
        shuffled_means: list[float] = []
        for _ in range(SHUFFLE_ROUNDS):
            rng.shuffle(references)
            shuffled_means.append(statistics.fmean(_jaccard(ours, ref) for (ours, _), ref in zip(pairs, references)))
        metric.baseline = {
            "kind": "shuffled_reference",
            "value": round(statistics.fmean(shuffled_means), 4),
            "rounds": SHUFFLE_ROUNDS,
        }
    else:
        metric.baseline = {"kind": "shuffled_reference", "value": None, "rounds": 0}
    return metric


# --- score -------------------------------------------------------------------


def _ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(order):
        end = position
        while end + 1 < len(order) and values[order[end + 1]] == values[order[position]]:
            end += 1
        average = (position + end) / 2 + 1
        for index in order[position : end + 1]:
            ranks[index] = average
        position = end + 1
    return ranks


def spearman(pairs: Sequence[tuple[float, float]]) -> float | None:
    if len(pairs) < 3:
        return None
    left = _ranks([pair[0] for pair in pairs])
    right = _ranks([pair[1] for pair in pairs])
    mean_left = statistics.fmean(left)
    mean_right = statistics.fmean(right)
    cov = sum((a - mean_left) * (b - mean_right) for a, b in zip(left, right))
    var_left = sum((a - mean_left) ** 2 for a in left)
    var_right = sum((b - mean_right) ** 2 for b in right)
    if var_left == 0 or var_right == 0:
        return None
    return round(cov / (var_left * var_right) ** 0.5, 4)


def score_pairs(rows: Sequence[Joined]) -> list[tuple[float, float]]:
    return [
        (float(row.weighted_score), float(row.reference["score_0_100"]))
        for row in rows
        if row.weighted_score is not None and row.reference.get("score_0_100") is not None
    ]


def score_spearman(rows: Sequence[Joined]) -> Metric:
    pairs = score_pairs(rows)
    return Metric(
        name="score_spearman",
        n=len(pairs),
        value=spearman(pairs),
        ci95=bootstrap_ci(pairs, spearman),
        baseline={"kind": "independence", "value": 0.0},
    )


# --- selected ----------------------------------------------------------------


def auc(pairs: Sequence[tuple[float, bool]]) -> float | None:
    positives = [score for score, selected in pairs if selected]
    negatives = [score for score, selected in pairs if not selected]
    if not positives or not negatives:
        return None
    wins = 0.0
    for positive in positives:
        for negative in negatives:
            if positive > negative:
                wins += 1.0
            elif positive == negative:
                wins += 0.5
    return round(wins / (len(positives) * len(negatives)), 4)


def selected_pairs(rows: Sequence[Joined]) -> list[tuple[float, bool]]:
    return [
        (float(row.weighted_score), bool(row.reference.get("selected")))
        for row in rows
        if row.weighted_score is not None
    ]


def selected_auc(rows: Sequence[Joined]) -> Metric:
    pairs = selected_pairs(rows)
    return Metric(
        name="selected_auc",
        n=len(pairs),
        value=auc(pairs),
        ci95=bootstrap_ci(pairs, auc),
        baseline={"kind": "random_ranking", "value": 0.5},
        extra={"positives": sum(1 for _, selected in pairs if selected)},
    )


def _utc_day(value: Any) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%Y-%m-%d")


@dataclass(frozen=True)
class DayBucket:
    day: str
    k: int
    n: int
    hits: int

    @property
    def random_expected_hits(self) -> float:
        return self.k * self.k / self.n


def day_buckets(rows: Sequence[Joined]) -> list[DayBucket]:
    grouped: dict[str, list[Joined]] = defaultdict(list)
    for row in rows:
        day = _utc_day(row.reference.get("published_at"))
        if day is not None and row.weighted_score is not None:
            grouped[day].append(row)
    buckets: list[DayBucket] = []
    for day in sorted(grouped):
        members = grouped[day]
        k = sum(1 for row in members if row.reference.get("selected"))
        if k == 0:
            continue
        ranked = sorted(members, key=lambda row: (-float(row.weighted_score or 0.0), row.question_id))
        hits = sum(1 for row in ranked[:k] if row.reference.get("selected"))
        buckets.append(DayBucket(day=day, k=k, n=len(members), hits=hits))
    return buckets


def pooled_precision(buckets: Sequence[DayBucket]) -> float | None:
    total_k = sum(bucket.k for bucket in buckets)
    return round(sum(bucket.hits for bucket in buckets) / total_k, 4) if total_k else None


def selected_p_at_k(rows: Sequence[Joined]) -> Metric:
    buckets = day_buckets(rows)
    total_k = sum(bucket.k for bucket in buckets)
    return Metric(
        name="selected_p_at_k",
        n=len(buckets),
        value=pooled_precision(buckets),
        ci95=bootstrap_ci(buckets, pooled_precision),
        baseline={
            "kind": "daily_selected_rate",
            "value": round(sum(bucket.random_expected_hits for bucket in buckets) / total_k, 4) if total_k else None,
        },
        extra={
            "unit": "days (k = reference selected count per UTC day; precision pooled over days)",
            "recall_at_k": pooled_precision(buckets),
            "recall_note": "k equals the day's selected count, so recall@k == precision@k by construction",
            "days": [bucket.__dict__ for bucket in buckets],
        },
    )


# --- judge + bigram -----------------------------------------------------------


def closeness_mean(rows: Sequence[Joined], dimension: str) -> Metric:
    values = [row.judgments[dimension] / 100.0 for row in rows if dimension in row.judgments]
    metric = _mean_metric(f"{dimension}_closeness_mean", values)
    metric.baseline = {
        "kind": "judge_calibration",
        "note": "see judge-calibration.json positive / negative control means",
    }
    return metric


def char_bigrams(text: str) -> set[str]:
    compact = "".join(text.split())
    if len(compact) < 2:
        return {compact} if compact else set()
    return {compact[index : index + 2] for index in range(len(compact) - 1)}


_TEXT_FIELDS = {"summary": ("summary", "summary_zh"), "reason": ("reason", "why_recommend")}


def text_pairs(rows: Sequence[Joined], dimension: str) -> list[tuple[str, str]]:
    reference_field, candidate_field = _TEXT_FIELDS[dimension]
    pairs: list[tuple[str, str]] = []
    for row in rows:
        reference = row.reference.get(reference_field)
        candidate = row.enrich.get(candidate_field) if row.enrich else None
        if reference and candidate:
            pairs.append((str(candidate), str(reference)))
    return pairs


def bigram_jaccard(rows: Sequence[Joined], dimension: str) -> Metric:
    pairs = text_pairs(rows, dimension)
    values = [_jaccard(char_bigrams(candidate), char_bigrams(reference)) for candidate, reference in pairs]
    metric = _mean_metric(f"{dimension}_bigram_jaccard", values)
    if len(pairs) >= 2:
        rng = random.Random(BOOTSTRAP_SEED)
        references = [reference for _, reference in pairs]
        shuffled: list[float] = []
        for _ in range(SHUFFLE_ROUNDS):
            rng.shuffle(references)
            shuffled.append(
                statistics.fmean(
                    _jaccard(char_bigrams(candidate), char_bigrams(ref))
                    for (candidate, _), ref in zip(pairs, references)
                )
            )
        metric.baseline = {
            "kind": "shuffled_reference",
            "value": round(statistics.fmean(shuffled), 4),
            "rounds": SHUFFLE_ROUNDS,
        }
    else:
        metric.baseline = {"kind": "shuffled_reference", "value": None, "rounds": 0}
    return metric


# --- assembly ----------------------------------------------------------------


def compute_all(rows: Sequence[Joined]) -> dict[str, Metric]:
    metrics = [
        ai_recall(rows),
        category_agreement(rows),
        tag_jaccard_mean(rows),
        score_spearman(rows),
        selected_auc(rows),
        selected_p_at_k(rows),
        closeness_mean(rows, "summary"),
        bigram_jaccard(rows, "summary"),
        closeness_mean(rows, "reason"),
        bigram_jaccard(rows, "reason"),
    ]
    return {metric.name: metric for metric in metrics}


def _judge_identity_of(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return None
    return {"model": payload.get("model"), "prompt_sha256": payload.get("prompt_sha256")}


def compare_to_baseline(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    if current.get("questions_sha256") != baseline.get("questions_sha256"):
        return {
            "comparable": False,
            "reason": "questions_sha256 differs",
            "current": current.get("questions_sha256"),
            "baseline": baseline.get("questions_sha256"),
        }
    # questions_sha256 hashes the whole evalset file, so it is identical for two runs that
    # measured disjoint subsets of it (different --limit / --seed / --require-reference).
    # The subset actually measured is what the deltas are about, so gate on that too.
    if current.get("subset_sha256") != baseline.get("subset_sha256"):
        return {
            "comparable": False,
            "reason": "measured question subset differs",
            "current": current.get("sampling"),
            "baseline": baseline.get("sampling"),
        }
    if current.get("stopped_early") or baseline.get("stopped_early"):
        return {
            "comparable": False,
            "reason": "a run stopped early and covers only part of its subset",
            "current": current.get("stopped_early"),
            "baseline": baseline.get("stopped_early"),
        }
    stage_diff = {
        stage: {"current": current_identity, "baseline": (baseline.get("stages") or {}).get(stage)}
        for stage, current_identity in (current.get("stages") or {}).items()
        if (baseline.get("stages") or {}).get(stage) != current_identity
    }
    current_judge = _judge_identity_of(current.get("judge"))
    baseline_judge = _judge_identity_of(baseline.get("judge"))
    if current_judge and baseline_judge and current_judge != baseline_judge:
        return {
            "comparable": False,
            "reason": "judge identity differs",
            "current": current_judge,
            "baseline": baseline_judge,
        }
    deltas: dict[str, Any] = {}
    for name, metric in current["metrics"].items():
        other = baseline["metrics"].get(name)
        if not other or metric.get("value") is None or other.get("value") is None:
            deltas[name] = {"delta": None, "improved": None}
            continue
        # Non-overlapping 95% CIs. Comparing this run's CI lower bound against the
        # baseline's point estimate ignores the baseline's own uncertainty; measured
        # on two runs drawn from the same distribution it fired 9% of the time for a
        # nominal 2.5% test, i.e. roughly 1 in 11 no-op changes read as "improved".
        current_ci = metric.get("ci95")
        other_ci = other.get("ci95")
        # None, not False: without both intervals there is no verdict, and False would read
        # the same for a +0.38 gain, a -0.37 regression and a genuine no-change. A zero-width
        # interval is not evidence either -- 20/20 bootstraps to [1.0, 1.0] while its Wilson
        # interval is [0.84, 1.0], so treating it as a bound manufactures "improved".
        usable = (
            current_ci is not None
            and other_ci is not None
            and current_ci[0] < current_ci[1]
            and other_ci[0] < other_ci[1]
        )
        # improved and regressed are separate three-state answers, not one boolean's two
        # sides. improved=False alone reads identically for "no change" and for a drop of
        # 0.27 with fully disjoint intervals -- measured on a mutated run against its own
        # baseline -- and a reader acts differently on those.
        deltas[name] = {
            "delta": round(metric["value"] - other["value"], 4),
            "improved": (current_ci[0] > other_ci[1]) if usable else None,
            "regressed": (other_ci[0] > current_ci[1]) if usable else None,
            "verdict_rule": (
                "improved: current ci95 lower > baseline ci95 upper; "
                "regressed: baseline ci95 lower > current ci95 upper; "
                "both None when either interval is missing or zero-width"
            ),
            "baseline_value": other["value"],
            "baseline_ci95": other_ci,
            "baseline_n": other.get("n"),
            "n": metric.get("n"),
        }
    return {
        "comparable": True,
        "baseline_run_id": baseline.get("run_id"),
        "stage_identity_diff": stage_diff,
        "metrics": deltas,
    }


def evaluate_thresholds(
    metrics: dict[str, Any],
    thresholds: dict[str, Any] | None,
    subset_sha256: str | None = None,
) -> dict[str, Any] | None:
    """Score each metric against its configured floor.

    Two verdicts per metric because they answer different questions. ``value_meets`` asks
    whether this run landed above the floor; ``confident`` asks whether the interval says so,
    which is what a gate should block on -- a point estimate above the floor with an interval
    straddling it is noise, and blocking on the point estimate alone turns a threshold into a
    coin flip at these sample sizes. A metric with no usable interval gets ``confident: None``,
    not False: "cannot tell" and "below the floor" are different states and a reader acting on
    them does different things.
    """
    if not thresholds:
        return None
    verdicts: dict[str, Any] = {}
    for name, floor in thresholds.items():
        if name.startswith("_"):
            continue
        # A floor is only meaningful for the run configuration it was derived at, because the
        # gate reads a CI lower bound and that bound moves with sqrt(n). Scoring a 300-question
        # run against a floor derived from the 78-question reason population -- or the reverse --
        # fires on runs that have not regressed at all. subset_sha256 pins the pairing exactly.
        applies_to = floor.get("subset_sha256") if isinstance(floor, dict) else None
        if applies_to is not None and applies_to != subset_sha256:
            verdicts[name] = {
                "min": floor.get("min"),
                "value_meets": None,
                "confident": None,
                "reason": f"floor is for subset {applies_to[:12]}; this run is {(subset_sha256 or '')[:12]}",
            }
            continue
        minimum = floor.get("min") if isinstance(floor, dict) else floor
        metric = metrics.get(name)
        if metric is None or minimum is None or metric.get("value") is None:
            verdicts[name] = {"min": minimum, "value_meets": None, "confident": None, "reason": "no value"}
            continue
        ci = metric.get("ci95")
        usable = ci is not None and ci[0] < ci[1]
        verdicts[name] = {
            "min": minimum,
            "value_meets": metric["value"] >= minimum,
            "confident": (ci[0] >= minimum) if usable else None,
            "n": metric.get("n"),
            "basis": floor.get("basis") if isinstance(floor, dict) else None,
        }
    return verdicts


def compute_metrics(
    *,
    run_dir: Path,
    questions_path: Path,
    baseline_path: Path | None = None,
    thresholds_path: Path | None = None,
) -> dict[str, Any]:
    run_meta = read_json(run_dir / "run.json")
    resolved_thresholds = thresholds_path or (questions_path.parent / "thresholds.json")
    thresholds = read_json(resolved_thresholds) if resolved_thresholds.exists() else None
    questions = load_questions(questions_path)
    outputs = list(read_jsonl(run_dir / "outputs.jsonl"))
    judgments_path = run_dir / "judgments.jsonl"
    judgments = list(read_jsonl(judgments_path)) if judgments_path.exists() else []
    judge_meta = read_json(run_dir / "judge.json") if (run_dir / "judge.json").exists() else None
    calibration_path = run_dir / "judge-calibration.json"
    calibration = read_json(calibration_path) if calibration_path.exists() else None

    rows = join_rows(questions, outputs, judgments)
    metrics = compute_all(rows)
    failures = {
        stage: {
            "errors": sum(1 for row in outputs if isinstance(row.get(stage), dict) and row[stage].get("error")),
            "skipped": sum(1 for row in outputs if isinstance(row.get(stage), dict) and row[stage].get("skipped")),
        }
        for stage in run_meta.get("stages", [])
    }
    failures["judge"] = {"errors": sum(1 for row in judgments if row.get("error"))}
    payload: dict[str, Any] = {
        "schema_version": METRICS_SCHEMA_VERSION,
        "computed_at": utc_now(),
        "run_id": run_meta.get("run_id"),
        "questions_sha256": run_meta.get("questions_sha256"),
        # Identifies the subset actually measured, which questions_sha256 does not.
        "subset_sha256": sha256_text("\n".join(sorted(str(i) for i in (run_meta.get("item_ids") or [])))),
        "sampling": {
            key: run_meta.get(key) for key in ("limit", "seed", "require_reference", "pool_total", "pool_eligible")
        },
        "stages": (run_meta.get("identity") or {}).get("stages"),
        "n_questions_run": run_meta.get("n"),
        "n_joined": len(rows),
        "bootstrap": {"rounds": BOOTSTRAP_ROUNDS, "seed": BOOTSTRAP_SEED, "level": 0.95},
        "identity": run_meta.get("identity"),
        "judge": None
        if judge_meta is None
        else {
            key: judge_meta.get(key)
            for key in ("model", "prompt_sha256", "temperature", "max_tokens", "schema_version", "ark_host")
        },
        "judge_calibration": None
        if calibration is None
        else {
            "means": calibration.get("means"),
            "scale_ok": calibration.get("scale_ok"),
            "verdicts": calibration.get("verdicts"),
        },
        "metrics": {name: metric.as_dict() for name, metric in metrics.items()},
        "failures": failures,
        "stopped_early": bool(run_meta.get("stopped_early")) or bool(judge_meta and judge_meta.get("stopped_early")),
        "thresholds": thresholds,
        "threshold_verdicts": evaluate_thresholds(
            {name: metric.as_dict() for name, metric in metrics.items()},
            thresholds,
            sha256_text("\n".join(sorted(str(i) for i in (run_meta.get("item_ids") or [])))),
        ),
        "comparison": None,
    }
    if baseline_path is not None:
        payload["comparison"] = compare_to_baseline(payload, read_json(baseline_path))
    write_json(run_dir / "metrics.json", payload)
    (run_dir / "report.md").write_text(render_report(payload, run_meta, judge_meta, questions), encoding="utf-8")
    return payload


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def render_report(
    payload: dict[str, Any],
    run_meta: dict[str, Any],
    judge_meta: dict[str, Any] | None,
    questions: Sequence[dict[str, Any]],
) -> str:
    identity = run_meta.get("identity") or {}
    git = identity.get("git") or {}
    lines = [f"# aihot-fit report — run `{payload['run_id']}`", ""]
    lines += ["## 身份", ""]
    lines += [f"- git HEAD: `{git.get('head')}` dirty={git.get('dirty')}"]
    lines += [
        f"- 题集 sha256: `{payload['questions_sha256']}`；run n={payload['n_questions_run']}（joined {payload['n_joined']}）"
    ]
    lines += [
        f"- 起止: {run_meta.get('started_at')} → {run_meta.get('finished_at')}; stopped_early={payload['stopped_early']}"
    ]
    for stage, info in (identity.get("stages") or {}).items():
        lines.append(
            f"- {stage}: ruleset `{info.get('ruleset_version')}` model `{info.get('model_id')}` prompt sha256 `{str(info.get('prompt_sha256'))[:16]}…`"
        )
    env = identity.get("model_selection_env") or {}
    lines.append("- 模型选择 env: " + (", ".join(f"`{key}={value}`" for key, value in env.items()) or "(none)"))
    if judge_meta:
        shas = judge_meta.get("prompt_sha256") or {}
        lines.append(
            f"- 判官: `{judge_meta.get('model')}` temp={judge_meta.get('temperature')} max_tokens={judge_meta.get('max_tokens')} "
            f"host `{judge_meta.get('ark_host')}` prompt sha256 summary `{str(shas.get('summary'))[:16]}…` reason `{str(shas.get('reason'))[:16]}…`"
        )
    else:
        lines.append("- 判官: 未运行")
    lines += ["", "## 题集摘要", ""]
    run_ids = set(run_meta.get("item_ids") or [])
    subset = [question for question in questions if question["input"]["item_id"] in run_ids] or list(questions)
    categories = Counter(str(question["reference"].get("primary_category")) for question in subset)
    lines.append(f"- 本次 run 覆盖题数: {len(subset)}（题集总数 {len(questions)}）")
    lines.append("- 参考 primary_category: " + ", ".join(f"{key}={value}" for key, value in sorted(categories.items())))
    lines.append(f"- 参考 selected: {sum(1 for question in subset if question['reference'].get('selected'))}")
    lines.append(f"- 参考 tags 非空: {sum(1 for question in subset if question['reference'].get('tags'))}")
    lines.append(f"- 参考 reason 非空: {sum(1 for question in subset if question['reference'].get('reason'))}")
    lines += ["", "## 指标", "", "| 指标 | n | 点估计 | 95% CI | 对照 / 下界 |", "|---|---|---|---|---|"]
    for name, metric in payload["metrics"].items():
        baseline = metric.get("baseline") or {}
        baseline_text = f"{baseline.get('kind', '')} {_fmt(baseline.get('value'))}".strip()
        ci = metric.get("ci95")
        # AUC and P@k rest on the positives, not on n: n=20 with a single AIHOT-selected
        # item reads as a well-supported number in an "n" column but is a one-positive
        # statistic. Show the count the metric actually depends on.
        positives = metric.get("positives")
        n_text = f"{metric['n']}（正例 {positives}）" if positives is not None else str(metric["n"])
        lines.append(
            f"| {name} | {n_text} | {_fmt(metric['value'])} | {f'[{ci[0]:.4f}, {ci[1]:.4f}]' if ci else 'n/a'} | {baseline_text} |"
        )
    matrix = payload["metrics"]["category_agreement"].get("confusion_matrix", {}).get("counts", {})
    if matrix:
        lines += [
            "",
            "## 分类混淆矩阵（行=参考，列=我站）",
            "",
            "| ref \\ pred | " + " | ".join(PRIMARY_CATEGORIES) + " |",
            "|---|" + "---|" * len(PRIMARY_CATEGORIES),
        ]
        for expected in PRIMARY_CATEGORIES:
            lines.append(
                f"| {expected} | "
                + " | ".join(str(matrix[expected][predicted]) for predicted in PRIMARY_CATEGORIES)
                + " |"
            )
    days = payload["metrics"]["selected_p_at_k"].get("days") or []
    if days:
        lines += ["", "## 精选 P@k 按日", "", "| 日 | n | k | hits |", "|---|---|---|---|"]
        lines += [f"| {day['day']} | {day['n']} | {day['k']} | {day['hits']} |" for day in days]
    lines += ["", "## 判官对照", ""]
    calibration = payload.get("judge_calibration")
    if calibration and calibration.get("means"):
        for key, value in calibration["means"].items():
            lines.append(f"- {key}: n={value.get('n')} mean={_fmt(value.get('mean'))}")
        verdict = calibration.get("scale_ok")
        lines.append(
            "- 判官刻度: "
            + ("合格" if verdict else "不合格（阳性均值 <80 或阴性均值 >40）" if verdict is False else "未判定")
        )
    else:
        lines.append("- 未运行对照")
    lines += ["", "## 失败计数", ""]
    for stage, counts in payload["failures"].items():
        lines.append(f"- {stage}: " + ", ".join(f"{key}={value}" for key, value in counts.items()))
    verdicts = payload.get("threshold_verdicts")
    if not verdicts:
        lines += ["", "## 达标线", "", "- 未配置（题集目录下无 `thresholds.json`）"]
    else:
        lines += [
            "",
            "## 达标线",
            "",
            "| 指标 | 下限 | 本次值 | 点估计达标 | 区间确认 | 依据 |",
            "|---|---|---|---|---|---|",
        ]
        for name, verdict in verdicts.items():
            metric = payload["metrics"].get(name, {})
            confident = verdict.get("confident")
            lines.append(
                f"| {name} | {_fmt(verdict.get('min'))} | {_fmt(metric.get('value'))} | "
                f"{'是' if verdict.get('value_meets') else '否' if verdict.get('value_meets') is not None else 'n/a'} | "
                f"{'是' if confident else '不确定' if confident is None else '否'} | "
                f"{verdict.get('basis') or ''} |"
            )
        blocked = [n for n, v in verdicts.items() if v.get("confident") is False]
        unknown = [n for n, v in verdicts.items() if v.get("confident") is None]
        lines += [
            "",
            f"- **区间确认低于下限**（应阻断）: {', '.join(blocked) if blocked else '无'}",
            f"- **无法判定**（区间缺失或零宽，不等于达标）: {', '.join(unknown) if unknown else '无'}",
        ]
    comparison = payload.get("comparison")
    if comparison is not None:
        lines += ["", "## 与基线比较", ""]
        if not comparison.get("comparable"):
            lines.append(f"- 不可比: {comparison.get('reason')}")
        else:
            lines += [
                f"- 基线 run: `{comparison.get('baseline_run_id')}`",
                "",
                "| 指标 | delta | 改善 | 回归 |",
                "|---|---|---|---|",
            ]

            def _verdict(value: Any) -> str:
                return "是" if value is True else "否" if value is False else "判不出"

            for name, delta in comparison["metrics"].items():
                lines.append(
                    f"| {name} | {_fmt(delta.get('delta'))} | {_verdict(delta.get('improved'))} "
                    f"| {_verdict(delta.get('regressed'))} |"
                )
            regressed = [n for n, d in comparison["metrics"].items() if d.get("regressed") is True]
            lines += ["", f"- **判定为回归的指标**: {', '.join(regressed) if regressed else '无'}"]
    return "\n".join(lines) + "\n"
