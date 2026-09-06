from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from airadar.db import migrate
from airadar.fetcher.dedup import FetchedItem, upsert_item
from airadar.provider.base import ProviderItem, ScoringResult
from airadar.provider.heuristics import heuristic_score
from airadar.scorer.runner import run_scoring
from airadar.scorer.schema import ScoringNumeric
from airadar.sources.loader import SourceConfig
from airadar.sources.sync import sync_to_db


class FakeScorer:
    model_id = "fake-scorer"

    def score_5d(self, item: ProviderItem) -> ScoringResult:
        return ScoringResult(
            relevance=8.0,
            density=7.0,
            recency=6.0,
            authority=5.0,
            engineering=9.0,
            reasoning=f"Useful engineering signal for {item.source_id}.",
            raw={"provider_item_id": item.id},
        )

    def smoke_test(self) -> str:
        return "ok"


class VerboseFakeScorer:
    model_id = "verbose-fake-scorer"

    def score_5d(self, item: ProviderItem) -> ScoringResult:
        return ScoringResult(
            relevance=8.0,
            density=7.0,
            recency=6.0,
            authority=5.0,
            engineering=9.0,
            reasoning="x" * 240,
            topics=("a", "b", "c", "d", "e"),
            raw={"provider_item_id": item.id},
        )

    def smoke_test(self) -> str:
        return "ok"


def _recent_iso(minutes_ago: int) -> str:
    return (
        (datetime.now(UTC) - timedelta(minutes=minutes_ago)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


def test_strict_schema() -> None:
    valid = ScoringNumeric(
        relevance=0,
        density=10,
        recency=5.5,
        authority=7,
        engineering=8,
        reasoning="ok",
    )
    assert valid.relevance == 0

    with pytest.raises(ValueError):
        ScoringNumeric(relevance=10.1, density=5, recency=5, authority=5, engineering=5, reasoning="bad")
    with pytest.raises(ValueError):
        ScoringNumeric(relevance=5, density=5, recency=5, authority=5, engineering=5, reasoning="x" * 201)


def test_heuristic_score_is_item_specific() -> None:
    api_item = ProviderItem(
        id="a",
        title="OpenAI releases new realtime API guide",
        url="https://example.com/api",
        source_id="openai_blog",
        tier="T1",
        author=None,
        published_at="2026-05-08T01:02:03Z",
        content_text="A practical API guide for voice agents, tooling, evaluation, and deployment.",
    )
    research_item = ProviderItem(
        id="b",
        title="New arXiv paper benchmarks reasoning models",
        url="https://arxiv.org/abs/1234",
        source_id="papers",
        tier="T1.5",
        author=None,
        published_at="2026-05-08T01:02:03Z",
        content_text="Research paper with benchmark and evaluation details for LLM reasoning.",
    )

    api_score = heuristic_score(api_item)
    research_score = heuristic_score(research_item)

    assert "Heuristic development score" not in api_score.reasoning
    assert api_score.reasoning != research_score.reasoning
    assert api_score.topics == ()
    assert research_score.topics == ()
    assert api_score.engineering != research_score.engineering


def _db(tmp_path: Path) -> sqlite3.Connection:
    conn = _db_with_sources(tmp_path)
    item_id = _seed_item_with_dates(
        conn,
        "LLM benchmark",
        "A practical LLM benchmark with API details.",
        published_at=_recent_iso(30),
        fetched_at=_recent_iso(29),
    )
    _insert_prefilter_eval(conn, item_id)
    conn.commit()
    return conn


def _db_with_sources(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    sync_to_db(
        [
            SourceConfig(
                slug="example", name="Example", url="https://example.com/feed", tier="T1.5", enabled=True, meta={}
            )
        ],
        conn,
    )
    return conn


def _seed_item_with_dates(
    conn: sqlite3.Connection, title: str, content: str, *, published_at: str, fetched_at: str
) -> str:
    item = FetchedItem(
        source_id="example",
        url=f"https://example.com/{title.replace(' ', '-').lower()}",
        title=title,
        author="Ada",
        published_at=published_at,
        fetched_at=fetched_at,
        content_text=content,
    )
    upsert_item(conn, item)
    return conn.execute("SELECT id FROM items WHERE title=?", (title,)).fetchone()[0]


def _insert_prefilter_eval(conn: sqlite3.Connection, item_id: str) -> None:
    conn.execute(
        """
        INSERT INTO item_evaluations (
          item_id, stage, ruleset_version, model_id, input_json, output_json,
          numeric_json, latency_ms, cost_usd, evaluated_at, error
        )
        VALUES (?, 'prefilter', 'test.r1', 'fake', '{}', '{}', ?, 1, 0, ?, NULL)
        """,
        (item_id, json.dumps({"is_ai_related": True, "confidence": 0.9}), _recent_iso(28)),
    )


def test_run_scoring_writes_numeric_evaluations(tmp_path: Path) -> None:
    conn = _db(tmp_path)

    summary = run_scoring(conn, provider=FakeScorer(), since="24h", ruleset_version="score.r1")

    assert summary.processed == 1
    row = conn.execute(
        "SELECT stage, model_id, numeric_json, error FROM item_evaluations WHERE stage='scoring'"
    ).fetchone()
    assert row[0] == "scoring"
    assert row[1] == "fake-scorer"
    numeric = json.loads(row[2])
    assert numeric["engineering"] == 9.0
    assert row[3] is None


def test_run_scoring_includes_recently_fetched_backfill_regardless_of_old_published(tmp_path: Path) -> None:
    conn = _db_with_sources(tmp_path)
    recent_fetch = _recent_iso(5)
    old_published = (datetime.now(UTC) - timedelta(days=30)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    old_fetch = (datetime.now(UTC) - timedelta(days=30)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    backfill_id = _seed_item_with_dates(
        conn,
        "Old LLM archive fetched today",
        "A stale LLM archive item.",
        published_at=old_published,
        fetched_at=recent_fetch,
    )
    stale_fetch_id = _seed_item_with_dates(
        conn,
        "Recently published LLM archive fetched last month",
        "A recently published LLM item from an old fetch window.",
        published_at=_recent_iso(30),
        fetched_at=old_fetch,
    )
    _insert_prefilter_eval(conn, backfill_id)
    _insert_prefilter_eval(conn, stale_fetch_id)
    conn.commit()

    summary = run_scoring(conn, provider=FakeScorer(), since="1d", ruleset_version="score.r1")

    assert summary.processed == 1
    assert conn.execute("SELECT item_id FROM item_evaluations WHERE stage='scoring'").fetchall() == [(backfill_id,)]


def test_run_scoring_clamps_provider_text_to_schema_limits(tmp_path: Path) -> None:
    conn = _db(tmp_path)

    summary = run_scoring(conn, provider=VerboseFakeScorer(), since="24h", ruleset_version="score.r1")

    assert summary.processed == 1
    assert summary.errors == 0
    numeric_json, error = conn.execute(
        "SELECT numeric_json, error FROM item_evaluations WHERE stage='scoring'"
    ).fetchone()
    numeric = json.loads(numeric_json)
    assert len(numeric["reasoning"]) == 200
    assert numeric["topics"] == ["a", "b", "c", "d"]
    assert error is None


def test_run_scoring_records_out_of_range_errors(monkeypatch, tmp_path: Path) -> None:
    conn = _db(tmp_path)
    monkeypatch.setenv("AI_RADAR_FAKE_OUT_OF_RANGE", "1")

    summary = run_scoring(conn, provider=FakeScorer(), since="24h", limit=1)

    assert summary.processed == 1
    assert summary.errors == 1
    error = conn.execute("SELECT error FROM item_evaluations WHERE stage='scoring'").fetchone()[0]
    assert "schema validation failed" in error


def test_a_second_run_skips_what_it_already_scored(tmp_path: Path) -> None:
    conn = _db(tmp_path)

    first = run_scoring(conn, provider=FakeScorer(), since="24h", ruleset_version="score.r1")
    second = run_scoring(conn, provider=FakeScorer(), since="24h", ruleset_version="score.r1")

    assert (first.processed, second.processed) == (1, 0)


def test_force_rescores_what_the_version_alone_would_skip(tmp_path: Path) -> None:
    """A prompt edit changes scoring without moving ruleset_version, and the skip clause matches
    on that version -- so every already-scored item becomes unreachable and the archive can never
    pick up the change. `--force` is the way back in; the case above is what it has to overcome.
    """
    conn = _db(tmp_path)
    run_scoring(conn, provider=FakeScorer(), since="24h", ruleset_version="score.r1")

    forced = run_scoring(conn, provider=FakeScorer(), since="24h", ruleset_version="score.r1", force=True)

    assert forced.processed == 1
    rows = conn.execute(
        "SELECT COUNT(*) FROM item_evaluations WHERE stage='scoring' AND ruleset_version='score.r1'"
    ).fetchone()[0]
    # A new evaluation row, not an overwrite: selection reads MAX(id) per item, so the newer one
    # wins while the older one stays readable for comparison.
    assert rows == 2
