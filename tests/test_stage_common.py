from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime

import pytest

from airadar.enrich.prompts import render_enrich_prompt
from airadar.enrich.runner import _insert_evaluation as insert_enrich_evaluation
from airadar.enrich.runner import run_enrich
from airadar.enrich.schema import EnrichOutput
from airadar.prefilter.prompts import render_prefilter_prompt
from airadar.prefilter.runner import PrefilterNumeric, run_prefilter
from airadar.prefilter.runner import _insert_evaluation as insert_prefilter_evaluation
from airadar.provider.base import EnrichResult, PrefilterResult, ProviderItem, ScoringResult
from airadar.scorer.prompts import render_scoring_prompt
from airadar.scorer.runner import _insert_evaluation as insert_scoring_evaluation
from airadar.scorer.runner import run_scoring
from airadar.scorer.schema import ScoringNumeric
from airadar.stage_common import (
    insert_evaluation,
    json_dumps,
    parse_since,
    provider_item_from_row,
    utc_now,
)


class _Provider:
    model_id = "model-stage-common"


class _CountingConnection(sqlite3.Connection):
    commit_calls = 0

    def commit(self) -> None:
        self.commit_calls += 1
        super().commit()


class _PrefilterProvider:
    model_id = "prefilter-commit-spy"

    def is_ai_related(self, item: ProviderItem) -> PrefilterResult:
        return PrefilterResult(is_ai_related=True, confidence=0.9 if item.id == "item-1" else 2.0)


class _ScoringProvider:
    model_id = "scoring-commit-spy"

    def score_5d(self, item: ProviderItem) -> ScoringResult:
        return ScoringResult(
            relevance=8 if item.id == "item-1" else 99,
            density=7,
            recency=6,
            authority=5,
            engineering=9,
            reasoning="useful",
        )


class _EnrichProvider:
    model_id = "enrich-commit-spy"

    def enrich(self, item: ProviderItem) -> EnrichResult:
        if item.id != "item-1":
            raise RuntimeError("forced enrich failure")
        return EnrichResult(
            title_zh="一个足够长的中文标题",
            summary_zh="这是一段足够长的中文摘要，说明核心事实、背景原因和实际意义。",
            why_recommend="这个具体事实会影响 AI 工程实践，值得读者直接核对判断与趋势。",
            tags=("模型发布", "教程/实践"),
        )


def _item() -> ProviderItem:
    return ProviderItem(
        id="item-1",
        title="LLM 测试",
        url="https://example.com/item-1",
        source_id="example",
        tier="T1.5",
        author="Ada",
        published_at="2026-07-14T01:02:03Z",
        content_text="A practical test item.",
    )


def _evaluation_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE item_evaluations (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          item_id TEXT NOT NULL,
          stage TEXT NOT NULL,
          ruleset_version TEXT NOT NULL,
          model_id TEXT NOT NULL,
          input_json TEXT NOT NULL,
          output_json TEXT NOT NULL,
          numeric_json TEXT,
          latency_ms INTEGER NOT NULL,
          cost_usd REAL NOT NULL,
          evaluated_at TEXT NOT NULL,
          error TEXT
        )
        """
    )
    return conn


def _runner_conn() -> _CountingConnection:
    conn = sqlite3.connect(":memory:", factory=_CountingConnection)
    conn.executescript(
        """
        CREATE TABLE sources (id TEXT PRIMARY KEY, tier TEXT NOT NULL);
        CREATE TABLE items (
          id TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          url TEXT NOT NULL,
          source_id TEXT NOT NULL,
          author TEXT,
          published_at TEXT NOT NULL,
          fetched_at TEXT NOT NULL,
          content_text TEXT NOT NULL
        );
        CREATE TABLE item_evaluations (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          item_id TEXT NOT NULL,
          stage TEXT NOT NULL,
          ruleset_version TEXT NOT NULL,
          model_id TEXT NOT NULL,
          input_json TEXT NOT NULL,
          output_json TEXT NOT NULL,
          numeric_json TEXT,
          latency_ms INTEGER NOT NULL,
          cost_usd REAL NOT NULL,
          evaluated_at TEXT NOT NULL,
          error TEXT
        );
        INSERT INTO sources VALUES ('example', 'T1.5');
        INSERT INTO items VALUES
          ('item-1', 'First LLM item', 'https://example.com/1', 'example', 'Ada',
           '2026-07-14T01:02:03Z', '2026-07-14T01:03:03Z', 'First content'),
          ('item-2', 'Second LLM item', 'https://example.com/2', 'example', NULL,
           '2026-07-14T01:02:03Z', '2026-07-14T01:04:03Z', 'Second content');
        INSERT INTO item_evaluations (
          item_id, stage, ruleset_version, model_id, input_json, output_json,
          numeric_json, latency_ms, cost_usd, evaluated_at, error
        ) VALUES
          ('item-1', 'prefilter', 'seed.r1', 'seed', '{}', '{}',
           '{"is_ai_related":true}', 1, 0, '2026-07-14T01:05:03Z', NULL),
          ('item-2', 'prefilter', 'seed.r1', 'seed', '{}', '{}',
           '{"is_ai_related":true}', 1, 0, '2026-07-14T01:05:03Z', NULL);
        """
    )
    conn.commit_calls = 0
    return conn


def _row(conn: sqlite3.Connection) -> tuple[object, ...]:
    return conn.execute(
        """
        SELECT item_id, stage, ruleset_version, model_id, input_json, output_json,
               numeric_json, latency_ms, cost_usd, evaluated_at, error
        FROM item_evaluations
        """
    ).fetchone()


def test_shared_json_time_since_and_provider_item_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    assert json_dumps({"z": "汉", "a": [2, 1]}) == '{"a":[2,1],"z":"汉"}'

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001, ANN206
            return cls(2026, 7, 14, 1, 2, 3, 456789, tzinfo=UTC)

    monkeypatch.setattr("airadar.stage_common.datetime", _FrozenDateTime)
    assert utc_now() == "2026-07-14T01:02:03Z"
    assert parse_since("2h") == datetime(2026, 7, 13, 23, 2, 3, 456789, tzinfo=UTC)
    assert parse_since("1D") == datetime(2026, 7, 13, 1, 2, 3, 456789, tzinfo=UTC)
    assert parse_since("2026-07-14T03:02:03+02:00") == datetime(2026, 7, 14, 1, 2, 3, tzinfo=UTC)

    row = (
        "item-1",
        "LLM 测试",
        "https://example.com/item-1",
        "example",
        "T1.5",
        "Ada",
        "2026-07-14T01:02:03Z",
        "A practical test item.",
        "ignored ninth value",
    )
    assert provider_item_from_row(row) == _item()


@pytest.mark.parametrize("error", [None, "provider failed"])
def test_shared_evaluation_insert_preserves_complete_row_shape(
    monkeypatch: pytest.MonkeyPatch, error: str | None
) -> None:
    conn = _evaluation_conn()
    monkeypatch.setattr("airadar.stage_common.utc_now", lambda: "2026-07-14T01:02:03Z")

    insert_evaluation(
        conn,
        item_id="item-1",
        stage="stage-under-test",
        ruleset_version="rules.r1",
        model_id="model-1",
        input_data={"z": 2, "a": 1},
        output_data={"ok": error is None},
        numeric_data={"score": 7} if error is None else None,
        latency_ms=123,
        error=error,
    )

    assert _row(conn) == (
        "item-1",
        "stage-under-test",
        "rules.r1",
        "model-1",
        '{"a":1,"z":2}',
        '{"ok":true}' if error is None else '{"ok":false}',
        '{"score":7}' if error is None else None,
        123,
        0.0,
        "2026-07-14T01:02:03Z",
        error,
    )


@pytest.mark.parametrize("error", [None, "prefilter failed"])
def test_prefilter_evaluation_row_shape_is_unchanged(error: str | None) -> None:
    conn = _evaluation_conn()
    item = _item()
    numeric = PrefilterNumeric(is_ai_related=True, confidence=0.91) if error is None else None
    output = {"is_ai_related": True, "confidence": 0.91, "raw": {"b": 2, "a": 1}}

    insert_prefilter_evaluation(conn, item, _Provider(), "prefilter.r1", numeric, output, error, 17)

    row = _row(conn)
    assert row[:9] == (
        item.id,
        "prefilter",
        "prefilter.r1",
        _Provider.model_id,
        json_dumps(render_prefilter_prompt(item)),
        json_dumps(output),
        json_dumps(numeric.model_dump()) if numeric else None,
        17,
        0.0,
    )
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", str(row[9]))
    assert row[10] == error


@pytest.mark.parametrize("error", [None, "scoring failed"])
def test_scoring_evaluation_row_shape_is_unchanged(error: str | None) -> None:
    conn = _evaluation_conn()
    item = _item()
    numeric = (
        ScoringNumeric(
            relevance=8,
            density=7,
            recency=6,
            authority=5,
            engineering=9,
            reasoning="useful",
            topics=["agent"],
        )
        if error is None
        else None
    )
    output = {"relevance": 8, "reasoning": "useful", "raw": {"b": 2, "a": 1}}

    insert_scoring_evaluation(conn, item, _Provider(), "scoring.r1", numeric, output, error, 23)

    row = _row(conn)
    assert row[:9] == (
        item.id,
        "scoring",
        "scoring.r1",
        _Provider.model_id,
        json_dumps(render_scoring_prompt(item)),
        json_dumps(output),
        json_dumps(numeric.model_dump()) if numeric else None,
        23,
        0.0,
    )
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", str(row[9]))
    assert row[10] == error


@pytest.mark.parametrize("error", [None, "enrich failed"])
def test_enrich_evaluation_row_shape_is_unchanged(error: str | None) -> None:
    conn = _evaluation_conn()
    item = _item()
    enriched = (
        EnrichOutput(
            title_zh="一个足够长的中文标题",
            summary_zh="这是一段足够长的中文摘要，说明核心事实、背景原因和实际意义。",
            why_recommend="这个具体事实会影响 AI 工程实践，值得读者直接核对判断与趋势。",
            tags=["模型发布", "教程/实践"],
        )
        if error is None
        else None
    )
    output = {"attempts": 2, "raw": {"b": 2, "a": 1}}

    insert_enrich_evaluation(conn, item, _Provider(), "enrich.r1", enriched, output, error, 31)

    row = _row(conn)
    assert row[:9] == (
        item.id,
        "enrich",
        "enrich.r1",
        _Provider.model_id,
        json_dumps(render_enrich_prompt(item)),
        json_dumps(enriched.model_dump() if enriched else output),
        None,
        31,
        0.0,
    )
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", str(row[9]))
    assert row[10] == error


@pytest.mark.parametrize(
    ("stage", "run", "provider", "expected_commits"),
    [
        ("prefilter", run_prefilter, _PrefilterProvider(), 1),
        ("scoring", run_scoring, _ScoringProvider(), 1),
        ("enrich", run_enrich, _EnrichProvider(), 2),
    ],
)
def test_stage_processed_errors_and_commit_cadence_are_unchanged(
    stage: str, run, provider: object, expected_commits: int  # noqa: ANN001
) -> None:
    conn = _runner_conn()

    summary = run(
        conn,
        provider=provider,
        since="2026-07-01T00:00:00Z",
        ruleset_version=f"{stage}.r1",
    )

    assert summary.processed == 2
    assert summary.errors == 1
    assert conn.commit_calls == expected_commits
    assert conn.execute(
        "SELECT COUNT(*), SUM(error IS NOT NULL) FROM item_evaluations WHERE stage=? AND ruleset_version=?",
        (stage, f"{stage}.r1"),
    ).fetchone() == (2, 1)
