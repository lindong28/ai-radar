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


def _load_candidates(conn: sqlite3.Connection, weights: Weights) -> list[ScoredCandidate]:
    rows = conn.execute(
        """
        SELECT e.id, i.id, i.content_hash, i.url, i.published_at, s.tier, e.numeric_json
        FROM item_evaluations e
        JOIN items i ON i.id=e.item_id
        JOIN sources s ON s.id=i.source_id
        WHERE e.stage='scoring'
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
        numeric: dict[str, Any] = json.loads(row[6])
        score = weighted_score(numeric, weights, row[5])
        reason = {
            "scores": numeric,
            "tier": row[5],
            "tier_multiplier": tier_multiplier(row[5]),
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
    selected: list[ScoredCandidate] = []
    seen: set[str] = set()
    for candidate in fresh[: max(0, freshness_quota)]:
        selected.append(candidate)
        seen.add(candidate.item_id)
    for candidate in filtered:
        if len(selected) >= limit:
            break
        if candidate.item_id in seen:
            continue
        selected.append(candidate)
        seen.add(candidate.item_id)
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
          id, ruleset_version, weights_json, threshold, input_eval_ids, output_curated_ids, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run.id,
            run.ruleset_version,
            _json(run.weights.as_dict()),
            run.threshold,
            _json(run.input_eval_ids),
            _json(run.output_curated_ids),
            _utc_now(),
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
