from __future__ import annotations

import math
import sqlite3
import statistics
from dataclasses import dataclass


@dataclass(frozen=True)
class ScoreDistribution:
    run_id: str | None
    count: int
    display_scores: list[int]
    minimum: int | None
    maximum: int | None
    span: int
    stdev: float
    top10_scores: list[int]
    top10_unique_count: int

    @property
    def passes_v5(self) -> bool:
        return self.span >= 20 and self.stdev >= 8 and self.top10_unique_count == min(10, self.count)


def display_score(weighted_score: float) -> int:
    return math.floor(weighted_score * 10 + 0.5)


def latest_run_id(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT id FROM curation_runs ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()
    return row[0] if row else None


def score_distribution(conn: sqlite3.Connection, run_id: str | None = None) -> ScoreDistribution:
    selected_run_id = run_id or latest_run_id(conn)
    if selected_run_id is None:
        return ScoreDistribution(None, 0, [], None, None, 0, 0.0, [], 0)
    rows = conn.execute(
        "SELECT weighted_score FROM curated_items WHERE run_id=? ORDER BY rank",
        (selected_run_id,),
    ).fetchall()
    scores = [display_score(float(row[0])) for row in rows]
    if not scores:
        return ScoreDistribution(selected_run_id, 0, [], None, None, 0, 0.0, [], 0)
    top10 = scores[:10]
    return ScoreDistribution(
        run_id=selected_run_id,
        count=len(scores),
        display_scores=scores,
        minimum=min(scores),
        maximum=max(scores),
        span=max(scores) - min(scores),
        stdev=statistics.pstdev(scores),
        top10_scores=top10,
        top10_unique_count=len(set(top10)),
    )
