from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path
from typing import Protocol

from airadar.db import migrate
from airadar.provider.codex_gpt_mini import CodexGptMiniScorer
from airadar.provider.deepseek_v4_pro import DeepSeekV4ProScorer
from airadar.provider.deepseek_v32 import DeepSeekV32Prefilter
from airadar.provider.glm import GLMPrefilter

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "src" / "airadar" / "migrations"


def _enrich_trigger_block(migration_name: str) -> str:
    text = (MIGRATIONS / migration_name).read_text(encoding="utf-8")
    marker = "-- Keep this enrich_ai_fts block byte-identical in 003 and 004."
    start = text.index(marker)
    end = text.index("END;", start) + len("END;")
    return text[start:end]


class SmokeProvider(Protocol):
    model_id: str

    def smoke_test(self) -> str: ...


def test_migrate_creates_expected_tables_and_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.db"

    migrate(db_path)
    migrate(db_path)

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()

    table_names = {row[0] for row in rows}
    assert {
        "curated_items",
        "curation_runs",
        "feedback",
        "item_evaluations",
        "items_fts",
        "llm_usage",
        "items",
        "sources",
    }.issubset(table_names)

    with sqlite3.connect(db_path) as conn:
        curated_indexes = {
            row[1] for row in conn.execute("PRAGMA index_list('curated_items')").fetchall()
        }
    assert "idx_curated_items_item_run" in curated_indexes


def test_migrate_upgrades_cached_wechat_avatar_urls_to_https(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO wechat_account_avatars (account, avatar_url, checked_at, updated_at)
            VALUES (
              '数字生命卡兹克',
              'http://mmbiz.qpic.cn/mmbiz_png/avatar/0?wx_fmt=png',
              '2026-06-01T00:00:00Z',
              '2026-06-01T00:00:00Z'
            )
            """
        )

    migrate(db_path)

    with sqlite3.connect(db_path) as conn:
        avatar_url = conn.execute(
            "SELECT avatar_url FROM wechat_account_avatars WHERE account='数字生命卡兹克'"
        ).fetchone()[0]
    assert avatar_url == "https://mmbiz.qpic.cn/mmbiz_png/avatar/0?wx_fmt=png"


def test_migrate_upgrades_applied_004_database_fts_schema_and_trigger(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO sources (
              id, name, url, tier, enabled, kind, homepage_url, icon_url, meta_json, synced_at
            )
            VALUES (
              'legacy_source', 'Legacy Source', 'https://example.com/legacy.xml', 'T1', 1,
              'feed', 'https://example.com/legacy', 'https://example.com/legacy.ico', '{}',
              '2026-05-08T00:00:00Z'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO items (
              id, source_id, url, title, author, published_at, fetched_at,
              content_text, content_html, content_hash, extra_json
            )
            VALUES (
              'legacy-item', 'legacy_source', 'https://example.com/legacy-item',
              'Legacy title', 'Legacy Author', '2026-05-08T10:00:00Z',
              '2026-05-08T10:01:00Z', 'Legacy body', NULL, 'legacy-hash', '{}'
            )
            """
        )
        conn.executescript(
            """
            DROP TRIGGER IF EXISTS enrich_ai_fts;
            DROP TRIGGER IF EXISTS sources_au_fts;
            DROP TRIGGER IF EXISTS evals_ai_fts;
            DROP TABLE IF EXISTS items_fts;
            CREATE VIRTUAL TABLE items_fts USING fts5(
              item_id UNINDEXED, title, content_text, reasoning, tokenize='trigram'
            );
            INSERT INTO items_fts(item_id, title, content_text, reasoning)
            SELECT id, title, content_text, '' FROM items;
            INSERT OR IGNORE INTO airadar_migrations(id, applied_at)
            VALUES ('004_enrich_stage', '2026-05-12T00:00:00Z');
            """
        )

    migrate(db_path)
    migrate(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        columns = [row[1] for row in conn.execute("PRAGMA table_info('items_fts')").fetchall()]
        trigger_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='enrich_ai_fts'"
        ).fetchone()
        conn.execute(
            """
            INSERT INTO item_evaluations (
              item_id, stage, ruleset_version, model_id, input_json, output_json,
              numeric_json, latency_ms, cost_usd, evaluated_at, error
            )
            VALUES (
              'legacy-item', 'enrich', 'test.r1', 'fake', '{}',
              '{"title_zh":"生产态中文标题"}', '{}', 1, 0, '2026-05-08T10:02:00Z', NULL
            )
            """
        )
        title_zh = conn.execute(
            "SELECT title_zh FROM items_fts WHERE item_id='legacy-item'"
        ).fetchone()["title_zh"]

    assert columns == ["item_id", "title", "content_text", "source_name", "author", "title_zh"]
    assert trigger_exists is not None
    assert title_zh == "生产态中文标题"


def test_migration_files_keep_enrich_fts_trigger_definitions_identical() -> None:
    assert _enrich_trigger_block("003_add_fts5_search.sql") == _enrich_trigger_block("004_enrich_stage.sql")


def test_provider_smoke_contract_returns_status_strings() -> None:
    providers: list[SmokeProvider] = [
        GLMPrefilter(),
        CodexGptMiniScorer(),
        DeepSeekV32Prefilter(),
        DeepSeekV4ProScorer(),
    ]

    for provider in providers:
        assert provider.model_id
        assert isinstance(provider.smoke_test(), str)


def test_cli_help_exposes_planned_subcommands() -> None:
    result = subprocess.run(
        ["./run.sh", "--help"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    for command in ["fetch", "prefilter", "score", "curate", "serve", "admin"]:
        assert command in result.stdout


def test_cli_unimplemented_rerun_eval_exits_zero() -> None:
    result = subprocess.run(
        ["./run.sh", "admin", "rerun-eval"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    assert "not implemented" in result.stdout.lower()
