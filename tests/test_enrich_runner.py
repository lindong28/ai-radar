from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from airadar.db import migrate
from airadar.enrich.prompts import SYSTEM_PROMPT
from airadar.enrich.runner import run_enrich
from airadar.fetcher.dedup import FetchedItem, upsert_item
from airadar.provider.base import EnrichResult, ProviderItem
from airadar.sources.loader import SourceConfig
from airadar.sources.sync import sync_to_db


class FakeEnricher:
    model_id = "fake-enricher"

    def __init__(self, *, invalid: bool = False) -> None:
        self.invalid = invalid
        self.calls = 0

    def enrich(self, item: ProviderItem) -> EnrichResult:
        self.calls += 1
        tags = ("模型发布", "不在词表") if self.invalid else ("模型发布", "教程/实践")
        return EnrichResult(
            title_zh=f"{item.title} 中文标题",
            summary_zh="这是一段足够长的中文摘要，说明核心事实、背景原因和对读者的实际意义。",
            why_recommend="做 AI 产品和工程落地的你应该读这篇，因为它提供了可直接判断趋势的信号。",
            tags=tags,
            raw={"provider_item_id": item.id},
        )

    def smoke_test(self) -> str:
        return "ok"


class SlowEnricher(FakeEnricher):
    def __init__(self, *, delay: float = 0.02) -> None:
        super().__init__()
        self.delay = delay
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def enrich(self, item: ProviderItem) -> EnrichResult:
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(self.delay)
            return super().enrich(item)
        finally:
            with self.lock:
                self.active -= 1


def test_enrich_prompt_discourages_template_recommendations() -> None:
    assert "不要以" in SYSTEM_PROMPT
    for phrase in ["适合", "必读", "必看", "推荐给"]:
        assert phrase in SYSTEM_PROMPT


def _recent_iso(minutes_ago: int) -> str:
    return (
        (datetime.now(UTC) - timedelta(minutes=minutes_ago)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


def _db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    sync_to_db(
        [
            SourceConfig(
                slug="example", name="Example", url="https://example.com/feed", tier="T1.5", enabled=True, meta={}
            )
        ],
        conn,
    )
    _add_prefiltered_item(conn, "LLM benchmark", 30)
    conn.commit()
    return conn


def _add_prefiltered_item(conn: sqlite3.Connection, title: str, minutes_ago: int) -> str:
    item = FetchedItem(
        source_id="example",
        url=f"https://example.com/{title.replace(' ', '-').lower()}",
        title=title,
        author="Ada",
        published_at=_recent_iso(minutes_ago),
        fetched_at=_recent_iso(minutes_ago - 1),
        content_text=f"A practical LLM benchmark with API details for {title}.",
    )
    upsert_item(conn, item)
    item_id = conn.execute("SELECT id FROM items WHERE url=?", (item.url,)).fetchone()[0]
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
    return item_id


def test_run_enrich_writes_output_json(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    provider = FakeEnricher()

    summary = run_enrich(conn, provider=provider, since="24h", ruleset_version="enrich.r1")

    assert summary.processed == 1
    assert summary.errors == 0
    row = conn.execute(
        "SELECT stage, model_id, output_json, numeric_json, error FROM item_evaluations WHERE stage='enrich'"
    ).fetchone()
    assert row["stage"] == "enrich"
    assert row["model_id"] == "fake-enricher"
    output = json.loads(row["output_json"])
    assert output["title_zh"] == "LLM benchmark 中文标题"
    assert output["tags"] == ["模型发布", "教程/实践"]
    assert row["numeric_json"] is None
    assert row["error"] is None


def test_run_enrich_filters_unknown_tags_before_validation(tmp_path: Path) -> None:
    conn = _db(tmp_path)

    summary = run_enrich(conn, provider=FakeEnricher(invalid=True), since="24h", ruleset_version="enrich.r1")

    assert summary.processed == 1
    assert summary.errors == 0
    row = conn.execute("SELECT output_json, error FROM item_evaluations WHERE stage='enrich'").fetchone()
    output = json.loads(row["output_json"])
    assert output["tags"] == ["模型发布", "行业动态"]
    assert "不在词表" not in row["output_json"]
    assert row["error"] is None


def test_run_enrich_skips_existing_success_for_same_ruleset(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    provider = FakeEnricher()

    first = run_enrich(conn, provider=provider, since="24h", ruleset_version="enrich.r1")
    second = run_enrich(conn, provider=provider, since="24h", ruleset_version="enrich.r1")

    assert first.processed == 1
    assert second.processed == 0
    assert provider.calls == 1
    assert conn.execute("SELECT COUNT(*) FROM item_evaluations WHERE stage='enrich'").fetchone()[0] == 1


def test_run_enrich_can_target_explicit_item_ids(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    item_id = conn.execute("SELECT id FROM items").fetchone()[0]
    provider = FakeEnricher()

    summary = run_enrich(conn, provider=provider, since="24h", ruleset_version="enrich.r1", item_ids=[item_id])

    assert summary.processed == 1
    assert summary.errors == 0
    assert provider.calls == 1


def test_run_enrich_parallel_workers_commit_and_report_progress(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    for index in range(2, 5):
        _add_prefiltered_item(conn, f"LLM benchmark {index}", 30 + index)
    conn.commit()
    provider = SlowEnricher()
    committed_counts: list[int] = []

    summary = run_enrich(
        conn,
        provider=provider,
        since="24h",
        ruleset_version="enrich.r1",
        workers=4,
        progress_callback=lambda progress: committed_counts.append(
            conn.execute("SELECT COUNT(*) FROM item_evaluations WHERE stage='enrich'").fetchone()[0]
        ),
    )

    assert summary.processed == 4
    assert summary.errors == 0
    assert provider.max_active > 1
    assert committed_counts == [1, 2, 3, 4]


def _add_failed_enrich_row(
    conn: sqlite3.Connection,
    item_id: str,
    ruleset_version: str,
    minutes_ago: int,
    error: str = "output rejected after retry: tags outside controlled vocabulary: ['NVIDIA']",
) -> None:
    conn.execute(
        """
        INSERT INTO item_evaluations (
          item_id, stage, ruleset_version, model_id, input_json, output_json,
          numeric_json, latency_ms, cost_usd, evaluated_at, error
        )
        VALUES (?, 'enrich', ?, 'fake', '{}', '{}', NULL, 1, 0, ?, ?)
        """,
        (item_id, ruleset_version, _recent_iso(minutes_ago), error),
    )
    conn.commit()


def test_candidate_rows_back_off_recently_failed_items_v1_and_v2(tmp_path: Path) -> None:
    # fetched_at is bumped on every fetch that re-lists an item, so a failed item never
    # leaves the --since window on its own; without a backoff it would be retried every
    # round and, under --limit, crowd out never-attempted items.
    from airadar.enrich import runner as runner_v1
    from airadar.enrich import runner_v2

    conn = _db(tmp_path)
    item_id = conn.execute("SELECT id FROM items").fetchone()[0]

    assert [row[0] for row in runner_v1._candidate_rows(conn, "24h", "enrich.r1", None)] == [item_id]
    assert [row[0] for row in runner_v2._candidate_rows(conn, "24h", "enrich.r2", None)] == [item_id]

    _add_failed_enrich_row(conn, item_id, "enrich.r1", minutes_ago=10)
    _add_failed_enrich_row(conn, item_id, "enrich.r2", minutes_ago=10)
    assert runner_v1._candidate_rows(conn, "24h", "enrich.r1", None) == []
    assert runner_v2._candidate_rows(conn, "24h", "enrich.r2", None) == []

    # a failure for another ruleset does not shadow this one
    assert [row[0] for row in runner_v2._candidate_rows(conn, "24h", "enrich.r3", None)] == [item_id]

    # once the backoff elapses the item becomes a candidate again
    conn.execute(
        "UPDATE item_evaluations SET evaluated_at=? WHERE stage='enrich' AND error IS NOT NULL",
        (_recent_iso(25 * 60),),
    )
    conn.commit()
    assert [row[0] for row in runner_v1._candidate_rows(conn, "24h", "enrich.r1", None)] == [item_id]
    assert [row[0] for row in runner_v2._candidate_rows(conn, "24h", "enrich.r2", None)] == [item_id]


def test_candidate_rows_limit_prefers_never_attempted_items(tmp_path: Path) -> None:
    from airadar.enrich import runner_v2

    conn = _db(tmp_path)
    failed_id = conn.execute("SELECT id FROM items").fetchone()[0]
    fresh_id = _add_prefiltered_item(conn, "Fresh benchmark", 5)
    conn.commit()
    _add_failed_enrich_row(conn, failed_id, "enrich.r2", minutes_ago=10)
    # the failed item was re-listed by its feed after the fresh one arrived (fetched_at bump)
    conn.execute("UPDATE items SET fetched_at=? WHERE id=?", (_recent_iso(1), failed_id))
    conn.commit()

    rows = runner_v2._candidate_rows(conn, "24h", "enrich.r2", 1)

    assert [row[0] for row in rows] == [fresh_id]


def test_candidate_rows_do_not_back_off_transient_provider_failures(tmp_path: Path) -> None:
    from airadar.enrich import runner_v2

    conn = _db(tmp_path)
    item_id = conn.execute("SELECT id FROM items").fetchone()[0]
    _add_failed_enrich_row(
        conn,
        item_id,
        "enrich.r2",
        minutes_ago=10,
        error="enrich failed after retry: all DeepSeek provider endpoints failed: Connection error.",
    )

    assert [row[0] for row in runner_v2._candidate_rows(conn, "24h", "enrich.r2", None)] == [item_id]


def test_candidate_rows_explicit_item_ids_ignore_backoff(tmp_path: Path) -> None:
    from airadar.enrich import runner_v2

    conn = _db(tmp_path)
    item_id = conn.execute("SELECT id FROM items").fetchone()[0]
    _add_failed_enrich_row(conn, item_id, "enrich.r2", minutes_ago=10)

    assert runner_v2._candidate_rows(conn, "24h", "enrich.r2", None) == []
    assert [row[0] for row in runner_v2._candidate_rows(conn, "24h", "enrich.r2", None, [item_id])] == [item_id]


def test_evaluate_item_classifies_normalizer_rejection_as_output_rejected() -> None:
    from airadar.enrich import runner_v2

    class RejectingProvider:
        model_id = "fake-v2"

        def enrich(self, item: ProviderItem) -> object:
            raise ValueError("tags must contain 2-4 provider-selected values")

    item = ProviderItem(
        id="i", title="t", url="https://example.com/t", source_id="example", tier="T1.5",
        author=None, published_at=_recent_iso(1), content_text="body",
    )
    enriched, output, error, _latency = runner_v2._evaluate_item(RejectingProvider(), item)  # type: ignore[arg-type]

    assert enriched is None
    assert error is not None and error.startswith("output rejected after retry: tags must contain")
    assert output["attempts"] == 2
