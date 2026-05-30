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
    item = FetchedItem(
        source_id="example",
        url="https://example.com/llm",
        title="LLM benchmark",
        author="Ada",
        published_at=_recent_iso(30),
        fetched_at=_recent_iso(29),
        content_text="A practical LLM benchmark with API details.",
    )
    upsert_item(conn, item)
    item_id = conn.execute("SELECT id FROM items").fetchone()[0]
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
    conn.commit()
    return conn


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
