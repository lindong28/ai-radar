from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from airadar.db import migrate
from airadar.fetcher.dedup import FetchedItem, upsert_item
from airadar.prefilter.runner import run_prefilter
from airadar.provider.base import PrefilterResult, ProviderItem
from airadar.sources.loader import SourceConfig
from airadar.sources.sync import sync_to_db


class FakePrefilter:
    model_id = "fake-prefilter"

    def is_ai_related(self, item: ProviderItem) -> PrefilterResult:
        return PrefilterResult(
            is_ai_related="llm" in item.title.lower(),
            confidence=0.91,
            raw={"provider_item_id": item.id},
        )

    def smoke_test(self) -> str:
        return "ok"


def test_gateway_failure_is_recorded_without_retry_or_aborting_batch(tmp_path: Path) -> None:
    from airadar.provider.llm_gateway import GatewayRequestError

    conn = _db(tmp_path)
    ids = [_seed_item(conn, title, "LLM news") for title in ("LLM invalid", "LLM timeout", "LLM valid")]

    class MixedPrefilter(FakePrefilter):
        calls: list[str] = []

        def is_ai_related(self, item: ProviderItem) -> PrefilterResult:
            self.calls.append(item.id)
            if item.id in ids[:2]:
                raise GatewayRequestError(
                    "request-" + item.id,
                    code="ValueError" if item.id == ids[0] else "APITimeoutError",
                    body={"choices": [{"message": {"content": "[]"}}]} if item.id == ids[0] else None,
                    identity={"attempt_id": "attempt-1"} if item.id == ids[0] else None,
                )
            return super().is_ai_related(item)

    provider = MixedPrefilter()
    summary = run_prefilter(conn, provider=provider, ruleset_version="gateway-isolation")
    assert (summary.processed, summary.errors) == (3, 2)
    assert sorted(provider.calls) == sorted(ids)
    rows = {row[0]: row[1:] for row in conn.execute(
        "SELECT item_id,numeric_json,output_json,error FROM item_evaluations"
    )}
    for item_id in ids[:2]:
        numeric, output, error = rows[item_id]
        assert numeric is None
        assert "request-" + item_id in error
        raw = json.loads(output)["raw"]
        assert raw["sent_request_id"] == "request-" + item_id
        assert "is_ai_related" not in json.loads(output)
    assert json.loads(rows[ids[0]][1])["raw"]["response_body"]["choices"][0]["message"]["content"] == "[]"
    assert json.loads(rows[ids[1]][1])["raw"]["response_body"] is None
    assert rows[ids[2]][2] is None
    assert json.loads(rows[ids[2]][0])["is_ai_related"] is True
    recovered = run_prefilter(conn, provider=FakePrefilter(), ruleset_version="gateway-isolation")
    assert (recovered.processed, recovered.errors) == (2, 0)
    assert conn.execute("SELECT COUNT(*) FROM item_evaluations WHERE error IS NOT NULL").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM item_evaluations WHERE error IS NULL").fetchone()[0] == 3
    assert run_prefilter(conn, provider=FakePrefilter(), ruleset_version="gateway-isolation").processed == 0


def _recent_iso(minutes_ago: int) -> str:
    return (
        (datetime.now(UTC) - timedelta(minutes=minutes_ago)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


def _seed_item(conn: sqlite3.Connection, title: str, content: str) -> str:
    item = FetchedItem(
        source_id="example",
        url=f"https://example.com/{title.replace(' ', '-').lower()}",
        title=title,
        author="Ada",
        published_at=_recent_iso(30),
        fetched_at=_recent_iso(29),
        content_text=content,
    )
    upsert_item(conn, item)
    return conn.execute("SELECT id FROM items WHERE title=?", (title,)).fetchone()[0]


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


def _db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    sync_to_db(
        [
            SourceConfig(
                slug="example", name="Example", url="https://example.com/feed", tier="T2", enabled=True, meta={}
            )
        ],
        conn,
    )
    return conn


def test_run_prefilter_writes_numeric_evaluations(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    item_id = _seed_item(conn, "LLM benchmark", "A dense article about LLM evals.")

    summary = run_prefilter(conn, provider=FakePrefilter(), since="24h")

    assert summary.processed == 1
    row = conn.execute("SELECT item_id, stage, model_id, numeric_json, error FROM item_evaluations").fetchone()
    assert row[0] == item_id
    assert row[1] == "prefilter"
    assert row[2] == "fake-prefilter"
    assert json.loads(row[3]) == {"confidence": 0.91, "is_ai_related": True}
    assert row[4] is None


def test_invalid_llm_reason_is_archived_without_aborting_batch(tmp_path, monkeypatch):
    from airadar.provider import deepseek_v32
    from airadar.provider.deepseek_chat import ChatJsonResult

    monkeypatch.setenv("ARK_API_KEY", "fixture")
    monkeypatch.delenv("AI_RADAR_FORCE_HEURISTIC", raising=False)
    payloads = [
        {"is_ai_related": True, "confidence": .9},
        {"is_ai_related": False, "reason": "too late", "confidence": .9},
        {"reason": "concrete new model capability", "is_ai_related": True, "confidence": .9},
    ]
    responses = iter(payloads)
    monkeypatch.setattr(deepseek_v32, "chat_json", lambda **kw: ChatJsonResult(
        json=next(responses), provider="ark", model="fixture"))
    conn = _db(tmp_path)
    for i in range(3):
        _seed_item(conn, f"LLM reason {i}", f"distinct body {i}")
    result = run_prefilter(conn, provider=deepseek_v32.DeepSeekV32Prefilter(), since="24h")
    assert (result.processed, result.errors) == (3, 2)
    rows = conn.execute("SELECT output_json, numeric_json, error FROM item_evaluations ORDER BY id").fetchall()
    assert [json.loads(row[0])["raw"]["json"] for row in rows] == payloads
    assert all(row[1] is None and row[2] for row in rows[:2])
    assert json.loads(rows[2][0])["reason"] == payloads[2]["reason"]
    assert rows[2][2] is None


def test_run_prefilter_skips_existing_ruleset_evaluation(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    _seed_item(conn, "LLM benchmark", "A dense article about LLM evals.")

    run_prefilter(conn, provider=FakePrefilter(), since="24h", ruleset_version="test.r1")
    summary = run_prefilter(conn, provider=FakePrefilter(), since="24h", ruleset_version="test.r1")

    assert summary.processed == 0
    assert conn.execute("SELECT COUNT(*) FROM item_evaluations").fetchone()[0] == 1


def test_run_prefilter_includes_recently_fetched_backfill_regardless_of_old_published(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    old_fetch = (datetime.now(UTC) - timedelta(days=30)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    recent_fetch = _recent_iso(5)
    old_published = (datetime.now(UTC) - timedelta(days=30)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    backfill_id = _seed_item_with_dates(
        conn,
        "Old LLM archive fetched today",
        "A stale LLM archive item.",
        published_at=old_published,
        fetched_at=recent_fetch,
    )
    _seed_item_with_dates(
        conn,
        "Recently published LLM archive fetched last month",
        "A recently published LLM item from an old fetch window.",
        published_at=_recent_iso(30),
        fetched_at=old_fetch,
    )

    summary = run_prefilter(conn, provider=FakePrefilter(), since="1d", ruleset_version="test.r1")

    assert summary.processed == 1
    assert conn.execute("SELECT item_id FROM item_evaluations").fetchall() == [(backfill_id,)]


def test_run_prefilter_records_parse_errors(monkeypatch, tmp_path: Path) -> None:
    conn = _db(tmp_path)
    _seed_item(conn, "LLM benchmark", "A dense article about LLM evals.")
    monkeypatch.setenv("AI_RADAR_FAKE_BAD_JSON", "1")

    summary = run_prefilter(conn, provider=FakePrefilter(), since="24h", limit=1)

    assert summary.processed == 1
    assert summary.errors == 1
    error = conn.execute("SELECT error FROM item_evaluations").fetchone()[0]
    assert "json parse failed" in error


def test_candidate_rows_serve_never_judged_items_before_recomputing_old_ones(tmp_path: Path) -> None:
    """A criterion change must not starve arrivals, and `fetched_at` cannot prevent it.

    Prefilter's stamp now derives from the criterion, so editing the criterion makes
    every in-window item a candidate at once — measured 2026-09-09, 7020 items in the
    24h window. `fetched_at` cannot separate those recomputes from new arrivals: it is
    assigned per source batch and refreshed whenever a feed re-lists its archive
    (openai_blog re-fetched 1173 of its 1204 items within 24h). The same shape already
    bit enrich, where 18 brand-new items ranked 303rd to 2440th behind recomputes.

    So the fixture makes the already-judged items the *newer* ones by `fetched_at`: an
    ordering that ignores prefilter history hands every slot to a recompute.
    """
    conn = _db(tmp_path)
    fresh = _seed_item_with_dates(
        conn, "Brand new arrival", "text", published_at=_recent_iso(50), fetched_at=_recent_iso(50)
    )
    stale = [
        _seed_item_with_dates(
            conn, f"Already judged {n}", "text", published_at=_recent_iso(20 - n), fetched_at=_recent_iso(20 - n)
        )
        for n in range(5)
    ]
    for item_id in stale:
        conn.execute(
            """
            INSERT INTO item_evaluations (
              item_id, stage, ruleset_version, model_id, input_json, output_json,
              numeric_json, latency_ms, cost_usd, evaluated_at, error
            )
            VALUES (?, 'prefilter', 'retired.r1.deadbeef', 'fake', '{}', '{}', '{}', 1, 0, ?, NULL)
            """,
            (item_id, _recent_iso(10)),
        )
    conn.commit()

    from airadar.prefilter import runner

    rows = runner._candidate_rows(conn, "48h", "current.r1.cafebabe", 1, False)

    assert [row[0] for row in rows] == [fresh], "a never-judged item lost its slot to a recompute"
