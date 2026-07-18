from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from airadar import db
from airadar.web.app import create_app
from airadar.web.routes import curated_archive
from airadar.web.routes.pagination import VersionedTotalCache


def _seed_curated_db(path: Path) -> None:
    db.migrate(path)
    with db.get_conn(path) as conn:
        conn.execute(
            """
            INSERT INTO sources (
              id, name, url, tier, enabled, kind, homepage_url, icon_url, meta_json, synced_at
            ) VALUES (
              'source-1', 'Synthetic source', 'https://example.invalid/source', 'T1', 1,
              'feed', 'https://example.invalid/', NULL, '{}', '2026-07-15T00:00:00Z'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO items (
              id, source_id, url, title, author, published_at, fetched_at,
              content_text, content_html, content_hash, extra_json
            ) VALUES (
              'item-1', 'source-1', 'https://example.invalid/items/1', 'Synthetic item',
              'Synthetic author', '2026-07-15T00:00:00Z', '2026-07-15T00:00:00Z',
              'Synthetic placeholder.', NULL, 'hash-1', '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO curation_runs (
              id, ruleset_version, weights_json, threshold,
              input_eval_ids, output_curated_ids, created_at
            ) VALUES ('run-1', 'synthetic.r1', '{}', 0, '[]', '["item-1"]', '2026-07-15T00:00:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
            VALUES ('run-1', 'item-1', 9.0, 1, '{}')
            """
        )
        conn.commit()


def _insert_curated_item(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    run_id: str,
    url: str,
    published_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO items (
          id, source_id, url, title, author, published_at, fetched_at,
          content_text, content_html, content_hash, extra_json
        ) VALUES (?, 'source-1', ?, ?, 'Synthetic author', ?, ?, ?, NULL, ?, '{}')
        """,
        (
            item_id,
            url,
            f"Synthetic {item_id}",
            published_at,
            published_at,
            f"Synthetic placeholder for {item_id}.",
            f"hash-{item_id}",
        ),
    )
    conn.execute(
        """
        INSERT INTO curation_runs (
          id, ruleset_version, weights_json, threshold,
          input_eval_ids, output_curated_ids, created_at
        ) VALUES (?, 'synthetic.r1', '{}', 0, '[]', ?, ?)
        """,
        (run_id, json.dumps([item_id]), published_at),
    )
    conn.execute(
        """
        INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
        VALUES (?, ?, 9.0, 1, '{}')
        """,
        (run_id, item_id),
    )


def _category_output(*tags: str) -> str:
    return json.dumps(
        {
            "title_zh": "合成标题",
            "summary_zh": "这是只用于测试分类缓存失效的合成摘要内容，不包含生产数据。",
            "why_recommend": "这是只用于测试分类缓存失效的合成推荐理由，不包含生产数据。",
            "tags": list(tags),
        },
        ensure_ascii=False,
    )


def _cache_generations(conn: sqlite3.Connection) -> tuple[int, int]:
    row = conn.execute(
        """
        SELECT archive_generation, category_generation
        FROM archive_cache_generations
        WHERE id=1
        """
    ).fetchone()
    assert row is not None
    return int(row[0]), int(row[1])


def test_unrelated_evaluation_write_does_not_recompute_exact_archive_total(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "cache-regression.db"
    _seed_curated_db(db_path)
    monkeypatch.setattr(curated_archive, "_curated_total_cache", VersionedTotalCache(maxsize=8))
    count_calls: list[tuple[str, tuple[object, ...]]] = []
    original_count = curated_archive._count_archive_items

    def counting_total(
        conn: sqlite3.Connection,
        where: str,
        params: tuple[object, ...],
    ) -> int:
        count_calls.append((where, params))
        return original_count(conn, where, params)

    monkeypatch.setattr(curated_archive, "_count_archive_items", counting_total)
    client = TestClient(create_app(db_path))

    first = client.get("/api/v1/curated?page=1&limit=40")
    second = client.get("/api/v1/curated?page=1&limit=40")
    with db.get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO item_evaluations (
              item_id, stage, ruleset_version, model_id, input_json, output_json,
              numeric_json, latency_ms, cost_usd, evaluated_at, error
            ) VALUES (
              'item-1', 'scoring', 'synthetic.r1', 'synthetic', '{}', '{}', '{}',
              1, 0, '2026-07-15T00:01:00Z', NULL
            )
            """
        )
        conn.commit()
    after_unrelated_write = client.get("/api/v1/curated?page=1&limit=40")

    for response in (first, second, after_unrelated_write):
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["total"] == 1
        assert [item["id"] for item in data["items"]] == ["item-1"]
    assert len(count_calls) == 1


def test_successful_enrichment_invalidates_only_category_total(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "category-cache.db"
    _seed_curated_db(db_path)
    monkeypatch.setattr(curated_archive, "_curated_total_cache", VersionedTotalCache(maxsize=8))
    count_params: list[tuple[object, ...]] = []
    original_count = curated_archive._count_archive_items

    def counting_total(
        conn: sqlite3.Connection,
        where: str,
        params: tuple[object, ...],
    ) -> int:
        count_params.append(params)
        return original_count(conn, where, params)

    monkeypatch.setattr(curated_archive, "_count_archive_items", counting_total)
    client = TestClient(create_app(db_path))
    assert client.get("/api/v1/curated").json()["data"]["total"] == 1
    assert client.get("/api/v1/curated?category=ai-models").json()["data"]["total"] == 0

    output = _category_output("模型发布", "论文/研究")
    with db.get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO item_evaluations (
              item_id, stage, ruleset_version, model_id, input_json, output_json,
              numeric_json, latency_ms, cost_usd, evaluated_at, error
            ) VALUES (
              'item-1', 'enrich', 'synthetic.r1', 'synthetic', '{}', ?, NULL,
              1, 0, '2026-07-15T00:02:00Z', NULL
            )
            """,
            (output,),
        )
        conn.commit()

    assert client.get("/api/v1/curated").json()["data"]["total"] == 1
    category = client.get("/api/v1/curated?category=ai-models").json()["data"]
    assert category["total"] == 1
    assert [item["id"] for item in category["items"]] == ["item-1"]
    assert sum(not params for params in count_params) == 1
    assert sum(bool(params) for params in count_params) == 2


def test_curated_membership_change_invalidates_exact_total(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "membership-cache.db"
    _seed_curated_db(db_path)
    monkeypatch.setattr(curated_archive, "_curated_total_cache", VersionedTotalCache(maxsize=8))
    count_calls = 0
    original_count = curated_archive._count_archive_items

    def counting_total(
        conn: sqlite3.Connection,
        where: str,
        params: tuple[object, ...],
    ) -> int:
        nonlocal count_calls
        count_calls += 1
        return original_count(conn, where, params)

    monkeypatch.setattr(curated_archive, "_count_archive_items", counting_total)
    client = TestClient(create_app(db_path))
    assert client.get("/api/v1/curated").json()["data"]["total"] == 1

    with db.get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO items (
              id, source_id, url, title, author, published_at, fetched_at,
              content_text, content_html, content_hash, extra_json
            ) VALUES (
              'item-2', 'source-1', 'https://example.invalid/items/2', 'Synthetic item 2',
              'Synthetic author', '2026-07-15T00:03:00Z', '2026-07-15T00:03:00Z',
              'Synthetic placeholder 2.', NULL, 'hash-2', '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO curation_runs (
              id, ruleset_version, weights_json, threshold,
              input_eval_ids, output_curated_ids, created_at
            ) VALUES ('run-2', 'synthetic.r1', '{}', 0, '[]', '["item-2"]', '2026-07-15T00:03:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
            VALUES ('run-2', 'item-2', 9.0, 1, '{}')
            """
        )
        conn.commit()

    changed = client.get("/api/v1/curated?page=999&limit=1").json()["data"]
    assert changed["total"] == 2
    assert changed["page"] == 2
    assert [item["id"] for item in changed["items"]] == ["item-1"]
    assert count_calls == 2


def test_recurating_existing_members_does_not_invalidate_exact_total(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "recurated-members-cache.db"
    _seed_curated_db(db_path)
    monkeypatch.setattr(curated_archive, "_curated_total_cache", VersionedTotalCache(maxsize=8))
    count_calls = 0
    original_count = curated_archive._count_archive_items

    def counting_total(
        conn: sqlite3.Connection,
        where: str,
        params: tuple[object, ...],
    ) -> int:
        nonlocal count_calls
        count_calls += 1
        return original_count(conn, where, params)

    monkeypatch.setattr(curated_archive, "_count_archive_items", counting_total)
    client = TestClient(create_app(db_path))
    assert client.get("/api/v1/curated").json()["data"]["total"] == 1

    with db.get_conn(db_path) as conn:
        before = _cache_generations(conn)
        conn.execute(
            """
            INSERT INTO curation_runs (
              id, ruleset_version, weights_json, threshold,
              input_eval_ids, output_curated_ids, created_at
            ) VALUES ('run-2', 'synthetic.r1', '{}', 0, '[]', '["item-1"]',
                      '2026-07-15T00:03:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
            VALUES ('run-2', 'item-1', 8.5, 1, '{}')
            """
        )
        conn.commit()
        assert _cache_generations(conn) == before

    after = client.get("/api/v1/curated").json()["data"]
    assert after["total"] == 1
    assert [item["id"] for item in after["items"]] == ["item-1"]
    assert count_calls == 1


def test_items_url_update_invalidates_deduped_exact_total(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "items-update.db"
    _seed_curated_db(db_path)
    with db.get_conn(db_path) as conn:
        _insert_curated_item(
            conn,
            item_id="item-2",
            run_id="run-2",
            url="https://example.invalid/items/2",
            published_at="2026-07-15T00:03:00Z",
        )
        conn.commit()

    monkeypatch.setattr(curated_archive, "_curated_total_cache", VersionedTotalCache(maxsize=8))
    client = TestClient(create_app(db_path))
    before = client.get("/api/v1/curated?page=1&limit=40").json()["data"]
    assert before["total"] == 2

    with db.get_conn(db_path) as conn:
        conn.execute(
            "UPDATE items SET url='https://example.invalid/items/2/' WHERE id='item-1'"
        )
        conn.commit()

    changed = client.get("/api/v1/curated?page=1&limit=40").json()["data"]
    assert changed["total"] == 1
    assert [item["id"] for item in changed["items"]] == ["item-2"]


def test_curated_item_id_update_invalidates_exact_total(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "curated-update.db"
    _seed_curated_db(db_path)
    with db.get_conn(db_path) as conn:
        _insert_curated_item(
            conn,
            item_id="item-2",
            run_id="run-2",
            url="https://example.invalid/items/2",
            published_at="2026-07-15T00:03:00Z",
        )
        conn.commit()

    monkeypatch.setattr(curated_archive, "_curated_total_cache", VersionedTotalCache(maxsize=8))
    client = TestClient(create_app(db_path))
    assert client.get("/api/v1/curated").json()["data"]["total"] == 2

    with db.get_conn(db_path) as conn:
        conn.execute("UPDATE curated_items SET item_id='item-1' WHERE run_id='run-2'")
        conn.commit()

    changed = client.get("/api/v1/curated").json()["data"]
    assert changed["total"] == 1
    assert [item["id"] for item in changed["items"]] == ["item-1"]


def test_curated_delete_invalidates_total_and_clamps_last_page(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "curated-delete-clamp.db"
    _seed_curated_db(db_path)
    with db.get_conn(db_path) as conn:
        _insert_curated_item(
            conn,
            item_id="item-2",
            run_id="run-2",
            url="https://example.invalid/items/2",
            published_at="2026-07-15T00:03:00Z",
        )
        conn.commit()

    monkeypatch.setattr(curated_archive, "_curated_total_cache", VersionedTotalCache(maxsize=8))
    client = TestClient(create_app(db_path))
    before = client.get("/api/v1/curated?page=2&limit=1").json()["data"]
    assert before["total"] == 2
    assert before["page"] == 2

    with db.get_conn(db_path) as conn:
        conn.execute("DELETE FROM curated_items WHERE run_id='run-2'")
        conn.commit()

    changed = client.get("/api/v1/curated?page=999&limit=1").json()["data"]
    assert changed["total"] == 1
    assert changed["page"] == 1
    assert [item["id"] for item in changed["items"]] == ["item-1"]


def test_source_delete_invalidates_total_and_clamps_last_page(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "source-delete-clamp.db"
    _seed_curated_db(db_path)
    monkeypatch.setattr(curated_archive, "_curated_total_cache", VersionedTotalCache(maxsize=8))
    client = TestClient(create_app(db_path))
    before = client.get("/api/v1/curated?page=1&limit=1").json()["data"]
    assert before["total"] == 1

    with db.get_conn(db_path) as conn:
        conn.execute("DELETE FROM sources WHERE id='source-1'")
        conn.commit()

    changed = client.get("/api/v1/curated?page=999&limit=1").json()["data"]
    assert changed["total"] == 0
    assert changed["page"] == 1
    assert changed["items"] == []


def test_source_id_update_invalidates_exact_total(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "source-id-update.db"
    _seed_curated_db(db_path)
    monkeypatch.setattr(curated_archive, "_curated_total_cache", VersionedTotalCache(maxsize=8))
    client = TestClient(create_app(db_path))
    assert client.get("/api/v1/curated").json()["data"]["total"] == 1

    with db.get_conn(db_path) as conn:
        conn.execute("UPDATE sources SET id='renamed-source' WHERE id='source-1'")
        conn.commit()

    changed = client.get("/api/v1/curated").json()["data"]
    assert changed["total"] == 0
    assert changed["items"] == []


def test_count_and_page_query_share_one_read_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "read-snapshot.db"
    _seed_curated_db(db_path)
    with db.get_conn(db_path) as conn:
        _insert_curated_item(
            conn,
            item_id="item-2",
            run_id="run-2",
            url="https://example.invalid/items/2",
            published_at="2026-07-15T00:03:00Z",
        )
        conn.commit()

    monkeypatch.setattr(curated_archive, "_curated_total_cache", VersionedTotalCache(maxsize=8))
    count_finished = threading.Event()
    writer_finished = threading.Event()
    original_count = curated_archive._count_archive_items

    def coordinated_count(
        conn: sqlite3.Connection,
        where: str,
        params: tuple[object, ...],
    ) -> int:
        total = original_count(conn, where, params)
        count_finished.set()
        assert writer_finished.wait(timeout=5), "writer did not commit between count and page query"
        return total

    def delete_newer_membership() -> None:
        assert count_finished.wait(timeout=5), "reader did not finish exact count"
        with db.get_conn(db_path) as writer:
            writer.execute("DELETE FROM curated_items WHERE run_id='run-2' AND item_id='item-2'")
            writer.commit()
        writer_finished.set()

    monkeypatch.setattr(curated_archive, "_count_archive_items", coordinated_count)
    writer = threading.Thread(target=delete_newer_membership)
    writer.start()
    try:
        with db.get_conn(db_path) as reader:
            items, total, page = curated_archive._compute_archive_page(
                reader,
                page=2,
                limit=1,
                normalized_category=None,
                q=None,
            )
    finally:
        writer.join(timeout=5)
    assert not writer.is_alive()
    assert total == 2
    assert page == 2
    assert [item["id"] for item in items] == ["item-1"]


def test_category_total_matches_items_when_display_tags_are_truncated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "category-display-limit.db"
    _seed_curated_db(db_path)
    with db.get_conn(db_path) as conn:
        conn.execute("UPDATE sources SET name='OpenAI News' WHERE id='source-1'")
        conn.execute(
            """
            INSERT INTO item_evaluations (
              item_id, stage, ruleset_version, model_id, input_json, output_json,
              numeric_json, latency_ms, cost_usd, evaluated_at, error
            ) VALUES (
              'item-1', 'enrich', 'synthetic.r1', 'synthetic', '{}', ?, NULL,
              1, 0, '2026-07-15T00:02:00Z', NULL
            )
            """,
            (_category_output("模型发布", "产品更新", "教程/实践", "论文/研究"),),
        )
        conn.commit()

    monkeypatch.setattr(curated_archive, "_curated_total_cache", VersionedTotalCache(maxsize=8))
    data = TestClient(create_app(db_path)).get("/api/v1/curated?category=paper").json()["data"]
    assert data["total"] == 1
    assert [item["id"] for item in data["items"]] == ["item-1"]


def test_successful_enrich_update_and_delete_invalidate_category_total(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "enrich-update-delete.db"
    _seed_curated_db(db_path)
    with db.get_conn(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO item_evaluations (
              item_id, stage, ruleset_version, model_id, input_json, output_json,
              numeric_json, latency_ms, cost_usd, evaluated_at, error
            ) VALUES (
              'item-1', 'enrich', 'synthetic.r1', 'synthetic', '{}', ?, NULL,
              1, 0, '2026-07-15T00:02:00Z', NULL
            )
            """,
            (_category_output("模型发布"),),
        )
        evaluation_id = int(cursor.lastrowid)
        conn.commit()

    monkeypatch.setattr(curated_archive, "_curated_total_cache", VersionedTotalCache(maxsize=8))
    client = TestClient(create_app(db_path))
    assert client.get("/api/v1/curated?category=ai-models").json()["data"]["total"] == 1

    with db.get_conn(db_path) as conn:
        conn.execute(
            "UPDATE item_evaluations SET output_json=? WHERE id=?",
            (_category_output("论文/研究"), evaluation_id),
        )
        conn.commit()
    assert client.get("/api/v1/curated?category=ai-models").json()["data"]["total"] == 0

    with db.get_conn(db_path) as conn:
        conn.execute(
            "UPDATE item_evaluations SET output_json=? WHERE id=?",
            (_category_output("模型发布"), evaluation_id),
        )
        conn.commit()
    assert client.get("/api/v1/curated?category=ai-models").json()["data"]["total"] == 1

    with db.get_conn(db_path) as conn:
        conn.execute("DELETE FROM item_evaluations WHERE id=?", (evaluation_id,))
        conn.commit()
    assert client.get("/api/v1/curated?category=ai-models").json()["data"]["total"] == 0


def test_generation_triggers_cover_relevant_writes_only(tmp_path: Path) -> None:
    db_path = tmp_path / "generation-events.db"
    _seed_curated_db(db_path)
    with db.get_conn(db_path) as conn:
        initial = _cache_generations(conn)

        scoring = conn.execute(
            """
            INSERT INTO item_evaluations (
              item_id, stage, ruleset_version, model_id, input_json, output_json,
              numeric_json, latency_ms, cost_usd, evaluated_at, error
            ) VALUES (
              'item-1', 'scoring', 'synthetic.r1', 'synthetic', '{}', '{}', '{}',
              1, 0, '2026-07-15T00:01:00Z', NULL
            )
            """
        )
        scoring_id = int(scoring.lastrowid)
        conn.execute("UPDATE item_evaluations SET numeric_json='{\"score\":1}' WHERE id=?", (scoring_id,))
        conn.execute("DELETE FROM item_evaluations WHERE id=?", (scoring_id,))
        assert _cache_generations(conn) == initial

        failed_enrich = conn.execute(
            """
            INSERT INTO item_evaluations (
              item_id, stage, ruleset_version, model_id, input_json, output_json,
              numeric_json, latency_ms, cost_usd, evaluated_at, error
            ) VALUES (
              'item-1', 'enrich', 'synthetic.r1', 'synthetic', '{}', ?, NULL,
              1, 0, '2026-07-15T00:02:00Z', 'failed'
            )
            """,
            (_category_output("模型发布"),),
        )
        failed_enrich_id = int(failed_enrich.lastrowid)
        assert _cache_generations(conn) == initial

        conn.execute("UPDATE item_evaluations SET error=NULL WHERE id=?", (failed_enrich_id,))
        after_success_transition = _cache_generations(conn)
        assert after_success_transition == (initial[0], initial[1] + 1)

        conn.execute(
            "UPDATE item_evaluations SET output_json=? WHERE id=?",
            (_category_output("论文/研究"), failed_enrich_id),
        )
        after_output_update = _cache_generations(conn)
        assert after_output_update == (initial[0], initial[1] + 2)

        conn.execute(
            """
            INSERT INTO items (
              id, source_id, url, title, author, published_at, fetched_at,
              content_text, content_html, content_hash, extra_json
            ) VALUES (
              'item-2', 'source-1', 'https://example.invalid/items/2', 'Synthetic item 2',
              'Synthetic author', '2026-07-15T00:03:00Z', '2026-07-15T00:03:00Z',
              'Synthetic placeholder 2.', NULL, 'hash-2', '{}'
            )
            """
        )
        archive_after_item_insert = _cache_generations(conn)[0]
        conn.execute("UPDATE item_evaluations SET item_id='item-2' WHERE id=?", (failed_enrich_id,))
        after_item_update = _cache_generations(conn)
        assert after_item_update == (archive_after_item_insert, initial[1] + 3)

        conn.execute("UPDATE item_evaluations SET stage='scoring' WHERE id=?", (failed_enrich_id,))
        after_success_exit = _cache_generations(conn)
        assert after_success_exit == (archive_after_item_insert, initial[1] + 4)

        successful_enrich = conn.execute(
            """
            INSERT INTO item_evaluations (
              item_id, stage, ruleset_version, model_id, input_json, output_json,
              numeric_json, latency_ms, cost_usd, evaluated_at, error
            ) VALUES (
              'item-1', 'enrich', 'synthetic.r1', 'synthetic', '{}', ?, NULL,
              1, 0, '2026-07-15T00:04:00Z', NULL
            )
            """,
            (_category_output("模型发布"),),
        )
        successful_enrich_id = int(successful_enrich.lastrowid)
        assert _cache_generations(conn) == (archive_after_item_insert, initial[1] + 5)
        conn.execute("DELETE FROM item_evaluations WHERE id=?", (successful_enrich_id,))
        assert _cache_generations(conn) == (archive_after_item_insert, initial[1] + 6)

        conn.execute("DELETE FROM curated_items WHERE run_id='run-1' AND item_id='item-1'")
        after_curated_delete = _cache_generations(conn)
        assert after_curated_delete == (archive_after_item_insert + 1, initial[1] + 6)


def test_archive_generation_advances_for_item_and_curated_writes(tmp_path: Path) -> None:
    db_path = tmp_path / "archive-generation-events.db"
    _seed_curated_db(db_path)
    with db.get_conn(db_path) as conn:
        initial_archive, initial_category = _cache_generations(conn)
        conn.execute(
            "UPDATE sources SET name='Routine rename', synced_at='2026-07-15T01:00:00Z' WHERE id='source-1'"
        )
        assert _cache_generations(conn) == (initial_archive, initial_category)
        conn.execute(
            """
            INSERT INTO items (
              id, source_id, url, title, author, published_at, fetched_at,
              content_text, content_html, content_hash, extra_json
            ) VALUES (
              'item-2', 'source-1', 'https://example.invalid/items/2', 'Synthetic item 2',
              'Synthetic author', '2026-07-15T00:03:00Z', '2026-07-15T00:03:00Z',
              'Synthetic placeholder 2.', NULL, 'hash-2', '{}'
            )
            """
        )
        assert _cache_generations(conn) == (initial_archive, initial_category)
        conn.execute("UPDATE items SET url=url || '/' WHERE id='item-2'")
        assert _cache_generations(conn) == (initial_archive + 1, initial_category)

        conn.execute(
            """
            INSERT INTO curation_runs (
              id, ruleset_version, weights_json, threshold,
              input_eval_ids, output_curated_ids, created_at
            ) VALUES ('run-2', 'synthetic.r1', '{}', 0, '[]', '["item-2"]',
                      '2026-07-15T00:03:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
            VALUES ('run-2', 'item-2', 9.0, 1, '{}')
            """
        )
        assert _cache_generations(conn) == (initial_archive + 2, initial_category)
        conn.execute("UPDATE curated_items SET rank=2 WHERE run_id='run-2' AND item_id='item-2'")
        assert _cache_generations(conn) == (initial_archive + 2, initial_category)
        conn.execute("UPDATE curated_items SET run_id='run-1' WHERE run_id='run-2' AND item_id='item-2'")
        assert _cache_generations(conn) == (initial_archive + 3, initial_category)
        conn.execute("UPDATE curated_items SET run_id='run-2' WHERE run_id='run-1' AND item_id='item-2'")
        assert _cache_generations(conn) == (initial_archive + 4, initial_category)
        conn.execute("UPDATE curated_items SET item_id='item-1' WHERE run_id='run-2'")
        assert _cache_generations(conn) == (initial_archive + 5, initial_category)
        conn.execute("UPDATE curated_items SET item_id='item-2' WHERE run_id='run-2'")
        assert _cache_generations(conn) == (initial_archive + 6, initial_category)
        conn.execute("DELETE FROM curated_items WHERE run_id='run-2' AND item_id='item-2'")
        assert _cache_generations(conn) == (initial_archive + 7, initial_category)
        conn.execute("DELETE FROM items WHERE id='item-2'")
        assert _cache_generations(conn) == (initial_archive + 8, initial_category)


def test_newer_uncurated_duplicate_insert_advances_archive_generation(tmp_path: Path) -> None:
    db_path = tmp_path / "newer-uncurated-duplicate-insert.db"
    _seed_curated_db(db_path)
    with db.get_conn(db_path) as conn:
        initial_archive, initial_category = _cache_generations(conn)
        conn.execute(
            """
            INSERT INTO items (
              id, source_id, url, title, author, published_at, fetched_at,
              content_text, content_html, content_hash, extra_json
            ) VALUES (
              'item-2', 'source-1', 'https://example.invalid/items/1/',
              'Newer uncurated duplicate', 'Synthetic author',
              '2026-07-16T00:00:00Z', '2026-07-16T00:00:00Z',
              'Synthetic newer duplicate.', NULL, 'hash-2', '{}'
            )
            """
        )
        assert _cache_generations(conn) == (initial_archive + 1, initial_category)


def test_insert_between_suppressed_curated_item_and_visible_winner_does_not_advance(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "suppressed-curated-duplicate-insert.db"
    _seed_curated_db(db_path)
    with db.get_conn(db_path) as conn:
        initial_archive, initial_category = _cache_generations(conn)
        conn.execute(
            """
            INSERT INTO items (
              id, source_id, url, title, author, published_at, fetched_at,
              content_text, content_html, content_hash, extra_json
            ) VALUES (
              'item-newest', 'source-1', 'https://example.invalid/items/1/',
              'Newest uncurated duplicate', 'Synthetic author',
              '2026-07-17T00:00:00Z', '2026-07-17T00:00:00Z',
              'Synthetic newest duplicate.', NULL, 'hash-newest', '{}'
            )
            """
        )
        after_visible_winner_insert = _cache_generations(conn)
        assert after_visible_winner_insert == (initial_archive + 1, initial_category)

        conn.execute(
            """
            INSERT INTO items (
              id, source_id, url, title, author, published_at, fetched_at,
              content_text, content_html, content_hash, extra_json
            ) VALUES (
              'item-middle', 'source-1', 'https://example.invalid/items/1',
              'Middle uncurated duplicate', 'Synthetic author',
              '2026-07-16T00:00:00Z', '2026-07-16T00:00:00Z',
              'Synthetic middle duplicate.', NULL, 'hash-middle', '{}'
            )
            """
        )
        assert _cache_generations(conn) == after_visible_winner_insert


def test_item_insert_restoring_orphaned_curated_relation_advances_archive_generation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "orphaned-curated-item-insert.db"
    _seed_curated_db(db_path)
    with db.get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO curation_runs (
              id, ruleset_version, weights_json, threshold,
              input_eval_ids, output_curated_ids, created_at
            ) VALUES (
              'run-2', 'synthetic.r1', '{}', 0, '[]', '["item-orphan"]',
              '2026-07-15T01:00:00Z'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
            VALUES ('run-2', 'item-orphan', 9.0, 1, '{}')
            """
        )
        after_orphan_insert = _cache_generations(conn)
        conn.execute(
            """
            INSERT INTO items (
              id, source_id, url, title, author, published_at, fetched_at,
              content_text, content_html, content_hash, extra_json
            ) VALUES (
              'item-orphan', 'source-1', 'https://example.invalid/items/orphan',
              'Restored orphan', 'Synthetic author',
              '2026-07-15T01:00:00Z', '2026-07-15T01:00:00Z',
              'Synthetic restored orphan.', NULL, 'hash-orphan', '{}'
            )
            """
        )
        assert _cache_generations(conn) == (after_orphan_insert[0] + 1, after_orphan_insert[1])


def test_curated_presentation_updates_do_not_advance_archive_generation(tmp_path: Path) -> None:
    db_path = tmp_path / "curated-presentation-update.db"
    _seed_curated_db(db_path)
    with db.get_conn(db_path) as conn:
        initial = _cache_generations(conn)
        conn.execute(
            """
            UPDATE curated_items
            SET weighted_score=8.5,
                rank=2,
                reason_json='{"reason":"updated"}',
                summary_json='{"summary":"updated"}'
            WHERE run_id='run-1' AND item_id='item-1'
            """
        )
        assert _cache_generations(conn) == initial


def test_item_content_updates_do_not_advance_archive_generation(tmp_path: Path) -> None:
    db_path = tmp_path / "item-content-update.db"
    _seed_curated_db(db_path)
    with db.get_conn(db_path) as conn:
        initial = _cache_generations(conn)
        conn.execute(
            """
            UPDATE items
            SET title='Updated synthetic title',
                author='Updated author',
                content_text='Updated synthetic content.',
                content_html='<p>Updated synthetic content.</p>',
                content_hash='updated-hash',
                extra_json='{"updated":true}'
            WHERE id='item-1'
            """
        )
        assert _cache_generations(conn) == initial


def test_fetched_at_update_without_duplicate_does_not_advance_archive_generation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "item-fetched-update.db"
    _seed_curated_db(db_path)
    with db.get_conn(db_path) as conn:
        initial = _cache_generations(conn)
        conn.execute("UPDATE items SET fetched_at='2026-07-15T01:00:00Z' WHERE id='item-1'")
        assert _cache_generations(conn) == initial


def test_item_identity_updates_advance_archive_generation(tmp_path: Path) -> None:
    db_path = tmp_path / "item-identity-update.db"
    _seed_curated_db(db_path)
    with db.get_conn(db_path) as conn:
        initial_archive, initial_category = _cache_generations(conn)
        conn.execute("UPDATE items SET url=url || '/changed' WHERE id='item-1'")
        assert _cache_generations(conn) == (initial_archive + 1, initial_category)
        conn.execute(
            """
            INSERT INTO sources (
              id, name, url, tier, enabled, kind, homepage_url, icon_url, meta_json, synced_at
            ) VALUES (
              'source-2', 'Synthetic source 2', 'https://example.invalid/source-2', 'T1', 1,
              'feed', 'https://example.invalid/source-2/', NULL, '{}', '2026-07-15T00:00:00Z'
            )
            """
        )
        after_source_insert = _cache_generations(conn)[0]
        conn.execute("UPDATE items SET source_id='source-2' WHERE id='item-1'")
        assert _cache_generations(conn) == (after_source_insert + 1, initial_category)
        conn.execute("UPDATE items SET id='renamed-item' WHERE id='item-1'")
        assert _cache_generations(conn) == (after_source_insert + 2, initial_category)


def test_duplicate_timestamp_updates_advance_archive_generation(tmp_path: Path) -> None:
    db_path = tmp_path / "duplicate-timestamp-update.db"
    _seed_curated_db(db_path)
    with db.get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO items (
              id, source_id, url, title, author, published_at, fetched_at,
              content_text, content_html, content_hash, extra_json
            ) VALUES (
              'item-2', 'source-1', 'https://example.invalid/items/1/', 'Synthetic duplicate',
              'Synthetic author', '2026-07-14T00:00:00Z', '2026-07-14T00:00:00Z',
              'Synthetic duplicate placeholder.', NULL, 'hash-2', '{}'
            )
            """
        )
        initial_archive, initial_category = _cache_generations(conn)
        conn.execute("UPDATE items SET published_at='2026-07-16T00:00:00Z' WHERE id='item-2'")
        assert _cache_generations(conn) == (initial_archive + 1, initial_category)
        conn.execute("UPDATE items SET published_at='2026-07-15T00:00:00Z' WHERE id='item-2'")
        assert _cache_generations(conn) == (initial_archive + 2, initial_category)
        conn.execute("UPDATE items SET fetched_at='2026-07-16T00:00:00Z' WHERE id='item-2'")
        assert _cache_generations(conn) == (initial_archive + 3, initial_category)


def test_archive_generation_migration_is_idempotent_for_fresh_and_existing_db(
    tmp_path: Path,
) -> None:
    fresh_db = tmp_path / "fresh.db"
    db.migrate(fresh_db)
    first_objects: tuple[list[tuple[str]], tuple[int, int]]
    with sqlite3.connect(fresh_db) as conn:
        first_objects = (
            conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'archive_cache_%' ORDER BY name"
            ).fetchall(),
            _cache_generations(conn),
        )
    db.migrate(fresh_db)
    with sqlite3.connect(fresh_db) as conn:
        assert (
            conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'archive_cache_%' ORDER BY name"
            ).fetchall(),
            _cache_generations(conn),
        ) == first_objects

    existing_db = tmp_path / "existing.db"
    _seed_curated_db(existing_db)
    with sqlite3.connect(existing_db) as conn:
        conn.execute("DROP TABLE IF EXISTS archive_cache_generations")
        for (trigger_name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'archive_cache_%'"
        ).fetchall():
            conn.execute(f'DROP TRIGGER "{trigger_name}"')
    db.migrate(existing_db)
    db.migrate(existing_db)
    with sqlite3.connect(existing_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
        assert _cache_generations(conn) == (0, 0)


def test_archive_membership_trigger_migration_is_idempotent(tmp_path: Path) -> None:
    migration = db.MIGRATIONS_DIR / "014_curated_archive_membership_trigger.sql"
    assert migration.exists()

    db_path = tmp_path / "membership-trigger-idempotent.db"
    _seed_curated_db(db_path)
    with db.get_conn(db_path) as conn:
        sql = migration.read_text(encoding="utf-8")
        db._execute_migration_idempotent(conn, sql)
        db._execute_migration_idempotent(conn, sql)
        before = _cache_generations(conn)
        conn.execute(
            """
            INSERT INTO curation_runs (
              id, ruleset_version, weights_json, threshold,
              input_eval_ids, output_curated_ids, created_at
            ) VALUES ('run-2', 'synthetic.r1', '{}', 0, '[]', '["item-1"]',
                      '2026-07-15T00:03:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
            VALUES ('run-2', 'item-1', 8.5, 1, '{}')
            """
        )
        assert _cache_generations(conn) == before
