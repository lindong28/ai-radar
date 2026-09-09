from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from ..ruleset import current_version
from .dedup import deduplicate_candidates
from .score import ScoredCandidate, tier_multiplier, weighted_score
from .weights import DEFAULT_WEIGHTS, Weights

DEFAULT_THRESHOLD = 6.5
DEFAULT_LIMIT = 40
DEFAULT_FRESHNESS_QUOTA = 36
DEFAULT_FRESHNESS_FLOOR = 4.0
DEFAULT_FRESHNESS_WINDOW_HOURS = 48
DISPLAY_SCORE_HIGH = 92
DISPLAY_SCORE_LOW = 62
SOURCE_QUOTA_POLICY = "source-quota-v1"
# What the per-item ``baseline_selected`` flag and the run-level ``baseline_only`` list
# are measured against: the same run, same candidates and ordering, quotas disabled.
SOURCE_QUOTA_BASELINE = "same_run_without_source_quota"
# Which score ``baseline_only[].raw_weighted_score`` carries. It has to move when the score
# behind it moves -- the tier multiplier was retired on 2026-09-06 (ADR-20260906-7c31), and
# leaving the old value would have made runs from either side of that change read alike in the
# audit trail, which is the thing this record exists to prevent.
SOURCE_QUOTA_SCORE_SEMANTICS = "unadjusted_before_rank_calibration"
# What validation accepts. ADR-20260903-bc36 freezes the run *shape*, not a single value of this
# field: a run recorded before the multiplier was retired legitimately carries the old string,
# and rollback has to keep working on it. Comparing every stored run against the current constant
# broke rollback for the entire archive the moment the constant moved. New values are appended
# here, never substituted.
KNOWN_SOURCE_QUOTA_SCORE_SEMANTICS = frozenset(
    {"tier_adjusted_before_rank_calibration", SOURCE_QUOTA_SCORE_SEMANTICS}
)


@dataclass(frozen=True)
class SourceQuota:
    kind_caps: dict[str, float]
    per_source: float | None


DEFAULT_SOURCE_QUOTA = SourceQuota(kind_caps={"x": 0.20}, per_source=0.075)


@dataclass(frozen=True)
class CurationRun:
    id: str
    ruleset_version: str
    weights: Weights
    threshold: float
    input_eval_ids: list[int]
    output_curated_ids: list[str]


def _json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _run_id() -> str:
    return f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(2)}"


def _parse_utc(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _shanghai_date(value: str) -> str | None:
    parsed = _parse_utc(value)
    if parsed is None:
        return None
    return (parsed + timedelta(hours=8)).date().isoformat()


def parse_source_quota(text: str) -> SourceQuota | None:
    """Parse ``x=0.20,source=0.075``; ``off`` disables quotas.

    An empty or whitespace-only value is rejected: callers that read the
    environment treat it as "unset" (defaults apply) before calling this.
    """
    stripped = text.strip()
    if not stripped:
        raise ValueError("source quota is empty; expected e.g. x=0.20,source=0.075 or off")
    if stripped.lower() == "off":
        return None
    kind_caps: dict[str, float] = {}
    per_source: float | None = None
    for assignment in stripped.split(","):
        name, separator, raw_share = assignment.partition("=")
        name = name.strip()
        if not separator or not name or not raw_share.strip():
            raise ValueError(f"invalid source quota assignment: {assignment!r}")
        try:
            share = float(raw_share)
        except ValueError:
            raise ValueError(f"share for {name!r} is not a number: {raw_share.strip()!r}") from None
        if not 0 < share <= 1:
            raise ValueError(f"source quota share must be within (0, 1]: {assignment!r}")
        if name == "source":
            per_source = share
        else:
            kind_caps[name] = share
    return SourceQuota(kind_caps=kind_caps, per_source=per_source)


def _quota_cap(limit: int, share: float | None) -> int:
    return limit if share is None else max(1, round(limit * share))


def _fill(
    candidates_fresh: list[ScoredCandidate],
    candidates_filtered: list[ScoredCandidate],
    limit: int,
    freshness_quota: int,
    quota: SourceQuota | None,
) -> list[ScoredCandidate]:
    selected: list[ScoredCandidate] = []
    seen: set[str] = set()
    kind_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}

    def admit(candidate: ScoredCandidate) -> bool:
        if candidate.item_id in seen:
            return False
        if quota is not None:
            kind_cap = _quota_cap(limit, quota.kind_caps.get(candidate.kind))
            source_cap = _quota_cap(limit, quota.per_source)
            if kind_counts.get(candidate.kind, 0) >= kind_cap:
                return False
            if source_counts.get(candidate.source_id, 0) >= source_cap:
                return False
        selected.append(candidate)
        seen.add(candidate.item_id)
        kind_counts[candidate.kind] = kind_counts.get(candidate.kind, 0) + 1
        source_counts[candidate.source_id] = source_counts.get(candidate.source_id, 0) + 1
        return True

    # Same bound as the pre-quota code path (``fresh[:freshness_quota]``): the
    # fresh segment is not clamped to ``limit`` so ``source_quota=None`` keeps
    # reproducing the previous selection byte for byte.
    fresh_limit = max(0, freshness_quota)
    for candidate in candidates_fresh:
        if len(selected) >= fresh_limit:
            break
        admit(candidate)
    for candidate in candidates_filtered:
        if len(selected) >= limit:
            break
        admit(candidate)
    return selected


def _calibrate_selected_scores(selected: list[ScoredCandidate]) -> list[ScoredCandidate]:
    if len(selected) <= 1:
        return selected
    span = DISPLAY_SCORE_HIGH - DISPLAY_SCORE_LOW
    calibrated: list[ScoredCandidate] = []
    for index, candidate in enumerate(selected):
        display_score = round(DISPLAY_SCORE_HIGH - (span * index / (len(selected) - 1)))
        reason = dict(candidate.reason)
        reason["raw_weighted_score"] = candidate.weighted_score
        reason["score_calibration"] = {
            "method": "rank_linear_v1",
            "display_score": display_score,
            "range": [DISPLAY_SCORE_LOW, DISPLAY_SCORE_HIGH],
        }
        calibrated.append(replace(candidate, weighted_score=display_score / 10, reason=reason))
    return calibrated


# Per-category ranking multipliers, chosen 2026-09-09 by the repository owner.
#
# WHY THESE TWO AND NOT A QUOTA. AIHOT's own selection was characterised first, from the
# evalset's `reference.selected` + `category_slug` (its raw slug, deterministically mapped;
# NOT our enrich output): its flow -> its picks lifts tutorial 0.90x, product 0.88x,
# paper 0.92x, industry 0.69x and model 2.19x. So it is near category-neutral and simply
# has more tutorial in its flow (35.7%) than we do (23.8%). Ours lifted paper 2.63x and
# that is the one clear defect -- opposite in sign to the reference.
#
# WHAT THESE NUMBERS ARE, STATED PLAINLY: they were fitted to minimise the composition
# distance on one 48h pool, so they are a fit to a reading, not a transcription of AIHOT's
# decision function. Copying its lift coefficients was measured and REJECTED: it lands at
# TV 0.215, worse than the 0.203 baseline, because the same coefficient on a different flow
# yields a different composition (35.7%x0.90=32.1% vs 23.8%x0.90=21.4%). The owner chose the
# result over the method with that tradeoff in front of them.
#
# CONSEQUENCES FOR WHOEVER TOUCHES THIS NEXT. A cross-window check was attempted and did
# NOT come off, which is worth knowing before you trust one: TV is computed over categories,
# and each day's categories come from whichever enrich prompt was live then -- measured
# 2026-09-09, the last six day-windows are dominated by five different prompt generations
# (09-04/05 by a retired f422dfa6 at 96%/92%, 09-09 by e04c9a50 at 100%). So comparing them
# compares labelling regimes, not days. Across those six the multipliers never made TV worse
# (5 improved, 1 unchanged, worst delta +0.000), but only 09-09 is a clean reading (-0.041)
# and 09-08 a half-clean one (-0.015). A real cross-window check needs enrich re-run at one
# prompt over several windows. The pool also drifts within minutes, so these need re-fitting.
# This is
# deliberately a score multiplier and not a quota: a quota would force the target share
# whatever the pool holds, while this only reorders and lets composition float with the pool.
# Measured on the real selection path (_load_candidates + dedup + threshold + fresh pool +
# _fill), not a simplification -- an earlier simplified model put the baseline at 0.494
# against a real 0.214 and every coefficient derived from it was wrong.
CATEGORY_MULTIPLIERS: dict[str, float] = {"paper": 0.90, "tutorial": 1.12}


def category_multiplier(category: str) -> float:
    """1.0 for anything unlisted, including items with no enrich row yet."""

    return CATEGORY_MULTIPLIERS.get(category, 1.0)


def _primary_category(output_json: str | None) -> str:
    """Empty string when the item has no usable enrich row -- it then scores unmultiplied.

    Not an error: scoring and enrich are separate stages and an item can be scored before it
    is enriched, so this is the ordinary state for the newest candidates.
    """

    if not output_json:
        return ""
    try:
        payload = json.loads(output_json)
    except (TypeError, ValueError):
        return ""
    if not isinstance(payload, dict):
        return ""
    category = payload.get("primary_category")
    return category if isinstance(category, str) else ""


def _load_candidates(conn: sqlite3.Connection, weights: Weights) -> list[ScoredCandidate]:
    rows = conn.execute(
        """
        SELECT
          e.id, i.id, i.content_hash, i.url, i.published_at, s.tier,
          s.id, COALESCE(s.kind, 'feed'), e.numeric_json,
          (SELECT en.output_json FROM item_evaluations en
            WHERE en.item_id=e.item_id AND en.stage='enrich' AND en.error IS NULL
            ORDER BY en.id DESC LIMIT 1)
        FROM item_evaluations e
        JOIN items i ON i.id=e.item_id
        JOIN sources s ON s.id=i.source_id
        WHERE e.stage='scoring'
          AND s.enabled=1
          AND COALESCE(s.kind, 'feed') != 'wechat'
          AND e.error IS NULL
          AND e.id = (
            SELECT MAX(latest.id) FROM item_evaluations latest
            WHERE latest.item_id=e.item_id
              AND latest.stage='scoring'
              AND latest.error IS NULL
          )
        """
    ).fetchall()
    candidates: list[ScoredCandidate] = []
    for row in rows:
        numeric: dict[str, Any] = json.loads(row[8])
        category = _primary_category(row[9])
        score = weighted_score(numeric, weights, row[5]) * category_multiplier(category)
        reason = {
            "scores": numeric,
            "tier": row[5],
            # What was actually applied, not what the tier would map to. Recording the mapping
            # while the score no longer carries it puts a 1.25 next to a number that was never
            # multiplied, and every consumer of reason_json reads them as a pair.
            "tier_multiplier": tier_multiplier(row[5]) if weights.uses_tier_multiplier else 1.0,
            "category": category,
            # Recorded for the same reason as tier_multiplier above: reason_json's consumers
            # read the factor and the score as a pair, so a score that was multiplied must
            # say by how much.
            "category_multiplier": category_multiplier(category),
            "weighted_score": score,
        }
        candidates.append(
            ScoredCandidate(
                eval_id=row[0],
                item_id=row[1],
                content_hash=row[2],
                url=row[3],
                published_at=row[4],
                weighted_score=score,
                reason=reason,
                source_id=row[6],
                kind=row[7],
                primary_category=category,
            )
        )
    return candidates


def curate(
    conn: sqlite3.Connection,
    *,
    ruleset_version: str | None = None,
    weights: Weights | None = None,
    threshold: float | None = None,
    limit: int = DEFAULT_LIMIT,
    freshness_quota: int = DEFAULT_FRESHNESS_QUOTA,
    freshness_floor: float = DEFAULT_FRESHNESS_FLOOR,
    freshness_window_hours: int = DEFAULT_FRESHNESS_WINDOW_HOURS,
    source_quota: SourceQuota | None = DEFAULT_SOURCE_QUOTA,
) -> CurationRun:
    selected_weights = weights or DEFAULT_WEIGHTS
    selected_weights.validate()
    selected_threshold = DEFAULT_THRESHOLD if threshold is None else threshold
    selected_ruleset = ruleset_version or current_version()

    candidates = deduplicate_candidates(_load_candidates(conn, selected_weights))
    filtered = [candidate for candidate in candidates if candidate.weighted_score >= selected_threshold]
    filtered.sort(key=lambda c: (-c.weighted_score, c.published_at, c.item_id))
    cutoff = datetime.now(UTC) - timedelta(hours=freshness_window_hours)
    fresh_pool = [
        candidate
        for candidate in candidates
        if candidate.weighted_score >= freshness_floor
        and (published_at := _parse_utc(candidate.published_at))
        and published_at >= cutoff
        and _shanghai_date(candidate.published_at)
    ]
    latest_fresh_date = max((_shanghai_date(candidate.published_at) for candidate in fresh_pool), default=None)
    fresh = [
        candidate
        for candidate in fresh_pool
        if latest_fresh_date and _shanghai_date(candidate.published_at) == latest_fresh_date
    ]
    fresh.sort(key=lambda c: (-c.weighted_score, c.published_at, c.item_id))
    selected = _fill(fresh, filtered, limit, freshness_quota, source_quota)
    shadow_json: str | None = None
    if source_quota is not None:
        baseline = _fill(fresh, filtered, limit, freshness_quota, None)
        baseline_ids = {candidate.item_id for candidate in baseline}
        selected_ids = {candidate.item_id for candidate in selected}
        selected = [
            replace(
                candidate,
                reason={
                    **candidate.reason,
                    "source_quota": {
                        "policy": SOURCE_QUOTA_POLICY,
                        "kind": candidate.kind,
                        # null = no quota configured for this kind / no per-source cap
                        "kind_cap": (
                            _quota_cap(limit, source_quota.kind_caps[candidate.kind])
                            if candidate.kind in source_quota.kind_caps
                            else None
                        ),
                        "source_cap": (
                            _quota_cap(limit, source_quota.per_source) if source_quota.per_source is not None else None
                        ),
                        "baseline": SOURCE_QUOTA_BASELINE,
                        "baseline_selected": candidate.item_id in baseline_ids,
                    },
                },
            )
            for candidate in selected
        ]
        shadow_json = _json(
            {
                "policy": SOURCE_QUOTA_POLICY,
                "baseline": SOURCE_QUOTA_BASELINE,
                "score_semantics": SOURCE_QUOTA_SCORE_SEMANTICS,
                "baseline_only": [
                    {"item_id": candidate.item_id, "raw_weighted_score": candidate.weighted_score}
                    for candidate in baseline
                    if candidate.item_id not in selected_ids
                ],
                "quota_only_count": sum(candidate.item_id not in baseline_ids for candidate in selected),
            }
        )
    run = CurationRun(
        id=_run_id(),
        ruleset_version=selected_ruleset,
        weights=selected_weights,
        threshold=selected_threshold,
        input_eval_ids=[candidate.eval_id for candidate in candidates],
        output_curated_ids=[candidate.item_id for candidate in selected],
    )
    selected = _calibrate_selected_scores(selected)
    conn.execute(
        """
        INSERT INTO curation_runs (
          id, ruleset_version, weights_json, threshold, input_eval_ids,
          output_curated_ids, created_at, shadow_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run.id,
            run.ruleset_version,
            _json(run.weights.as_record()),
            run.threshold,
            _json(run.input_eval_ids),
            _json(run.output_curated_ids),
            _utc_now(),
            shadow_json,
        ),
    )
    for rank, candidate in enumerate(selected, start=1):
        conn.execute(
            """
            INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (run.id, candidate.item_id, candidate.weighted_score, rank, _json(candidate.reason)),
        )
    conn.commit()
    return run
