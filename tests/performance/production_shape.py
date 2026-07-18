from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from airadar import db
from airadar.web.routes.categories import deduped_item_clause


@dataclass(frozen=True, slots=True)
class ShapeConfig:
    items: int = 30_000
    evaluations: int = 70_000
    curation_runs: int = 5_000
    curated_rows: int = 200_000
    interpretations: int = 1_800
    displayed_interpretations: int = 1_300
    curated_page_size: int = 40
    wechat_page_size: int = 50


@dataclass(frozen=True, slots=True)
class ProductionShape:
    db_path: Path
    manifest_path: Path
    manifest: dict[str, Any]

    def clone(self, target: Path) -> ProductionShape:
        if target.exists():
            raise FileExistsError(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(self.db_path)
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        target_manifest = target.with_suffix(".manifest.json")
        target_manifest.write_bytes(self.manifest_path.read_bytes())
        return ProductionShape(target, target_manifest, self.manifest)

    def validate(self) -> None:
        validate_production_shape(self.db_path, self.manifest)


def _item_id(index: int) -> str:
    return f"item-{index:05d}"


def _slug(index: int) -> str:
    return f"synthetic-wechat-{index:04d}"


def _relation_sha256(rows: Iterable[Iterable[object]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        encoded = json.dumps(tuple(row), ensure_ascii=False, separators=(",", ":")).encode()
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _require_equal(label: str, actual: object, expected: object) -> None:
    if actual != expected:
        raise AssertionError(f"{label} mismatch")


def _expected_manifest(config: ShapeConfig) -> dict[str, Any]:
    curated_ids = [_item_id(index) for index in range(config.items - 1, -1, -1)]
    displayed_indices = list(range(config.displayed_interpretations - 1, -1, -1))
    wechat_item_ids = [_item_id(index) for index in displayed_indices]
    wechat_slugs = [_slug(index) for index in displayed_indices]
    return {
        "schema_version": 1,
        "generator": "ai-radar-synthetic-production-shape-v1",
        "table_counts": {
            "items": config.items,
            "item_evaluations": config.evaluations,
            "curation_runs": config.curation_runs,
            "curated_items": config.curated_rows,
            "wechat_interpretations": config.interpretations,
            "displayed_wechat_interpretations": config.displayed_interpretations,
        },
        "relation_sha256": {
            "curated_items": _relation_sha256(
                (
                    f"run-{run_index:04d}",
                    _item_id((run_index * (config.curated_rows // config.curation_runs) + offset) % config.items),
                    offset + 1,
                )
                for run_index in range(config.curation_runs)
                for offset in range(config.curated_rows // config.curation_runs)
            ),
            "wechat_interpretations": _relation_sha256(
                (_item_id(index), _slug(index), 1 if index < config.displayed_interpretations else 0)
                for index in range(config.interpretations)
            ),
        },
        "visible": {
            "curated_eligible_ids": curated_ids,
            "curated_total": config.items,
            "curated_page_1_ids": curated_ids[: config.curated_page_size],
            "curated_page_2_ids": curated_ids[config.curated_page_size : config.curated_page_size * 2],
            "joinable_wechat_item_ids": wechat_item_ids,
            "wechat_total": config.displayed_interpretations,
            "wechat_page_1_slugs": wechat_slugs[: config.wechat_page_size],
            "wechat_page_2_slugs": wechat_slugs[config.wechat_page_size : config.wechat_page_size * 2],
            "detail_slug": wechat_slugs[0],
            "detail_item_id": wechat_item_ids[0],
            "detail_title": f"Synthetic item {displayed_indices[0]:05d}",
        },
    }


def _timestamp(index: int) -> str:
    value = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=index)
    return value.isoformat().replace("+00:00", "Z")


def _batches(total: int, size: int) -> range:
    return range(0, total, size)


def build_production_shape(db_path: Path, manifest_path: Path) -> ProductionShape:
    if db_path.exists() or manifest_path.exists():
        raise FileExistsError(db_path if db_path.exists() else manifest_path)
    config = ShapeConfig()
    manifest = _expected_manifest(config)
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode()
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_bytes(manifest_bytes)

    db.migrate(db_path)
    conn = db.get_conn(db_path)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        # Bulk enrich rows are fixture data, not the behavior under test. Updating
        # the FTS5 table one row at a time makes setup quadratic because item_id is
        # UNINDEXED. create_app() rebuilds the same FTS snapshot before requests;
        # restore the trigger here so cloned fixtures retain production schema.
        conn.execute("DROP TRIGGER IF EXISTS enrich_ai_fts")
        conn.executemany(
            """
            INSERT INTO sources (
              id, name, url, tier, enabled, kind, homepage_url, icon_url, meta_json, synced_at
            ) VALUES (?, ?, ?, 'T1', 1, ?, ?, NULL, '{}', '2026-07-15T00:00:00Z')
            """,
            [
                (
                    f"source-{index:02d}",
                    f"Synthetic source {index:02d}",
                    f"https://example.invalid/source/{index:02d}/rss",
                    "wechat" if index == 0 else "feed",
                    f"https://example.invalid/source/{index:02d}/",
                )
                for index in range(10)
            ],
        )
        for start in _batches(config.items, 2_000):
            stop = min(config.items, start + 2_000)
            conn.executemany(
                """
                INSERT INTO items (
                  id, source_id, url, title, author, published_at, fetched_at,
                  content_text, content_html, content_hash, extra_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, '{}')
                """,
                [
                    (
                        _item_id(index),
                        f"source-{index % 10:02d}",
                        f"https://example.invalid/item/{index:05d}",
                        f"Synthetic item {index:05d}",
                        f"Synthetic author {index % 100:02d}",
                        _timestamp(index),
                        _timestamp(index),
                        f"Synthetic placeholder {index:05d}.",
                        f"synthetic-hash-{index:05d}",
                    )
                    for index in range(start, stop)
                ],
            )

        enrich_output = json.dumps(
            {
                "title_zh": "合成标题",
                "summary_zh": "这是只用于确定性性能测试的简短合成摘要，不包含任何生产内容。",
                "why_recommend": "这是只用于确定性性能测试的合成推荐理由，不包含生产内容。",
                "tags": ["模型发布", "论文/研究"],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        for start in _batches(config.items, 2_000):
            stop = min(config.items, start + 2_000)
            conn.executemany(
                """
                INSERT INTO item_evaluations (
                  item_id, stage, ruleset_version, model_id, input_json, output_json,
                  numeric_json, latency_ms, cost_usd, evaluated_at, error
                ) VALUES (?, 'enrich', 'synthetic.r1', 'synthetic', '{}', ?, NULL, 1, 0, ?, NULL)
                """,
                [(_item_id(index), enrich_output, _timestamp(index)) for index in range(start, stop)],
            )
        conn.execute(
            """
            CREATE TRIGGER enrich_ai_fts AFTER INSERT ON item_evaluations
            WHEN new.stage = 'enrich' AND new.error IS NULL AND new.output_json IS NOT NULL BEGIN
              UPDATE items_fts
              SET title_zh = COALESCE(json_extract(new.output_json, '$.title_zh'), '')
              WHERE item_id = new.item_id;
            END
            """
        )
        remaining_evaluations = config.evaluations - config.items
        for start in _batches(remaining_evaluations, 2_000):
            stop = min(remaining_evaluations, start + 2_000)
            conn.executemany(
                """
                INSERT INTO item_evaluations (
                  item_id, stage, ruleset_version, model_id, input_json, output_json,
                  numeric_json, latency_ms, cost_usd, evaluated_at, error
                ) VALUES (?, ?, 'synthetic.r1', 'synthetic', '{}', '{}', '{}', 1, 0, ?, NULL)
                """,
                [
                    (
                        _item_id(index % config.items),
                        "prefilter" if index % 2 == 0 else "scoring",
                        _timestamp(config.items + index),
                    )
                    for index in range(start, stop)
                ],
            )

        for start in _batches(config.curation_runs, 1_000):
            stop = min(config.curation_runs, start + 1_000)
            conn.executemany(
                """
                INSERT INTO curation_runs (
                  id, ruleset_version, weights_json, threshold,
                  input_eval_ids, output_curated_ids, created_at
                ) VALUES (?, 'synthetic.r1', '{}', 0, '[]', '[]', ?)
                """,
                [(f"run-{index:04d}", _timestamp(index)) for index in range(start, stop)],
            )
        rows_per_run = config.curated_rows // config.curation_runs
        assert rows_per_run * config.curation_runs == config.curated_rows
        for start in _batches(config.curation_runs, 250):
            stop = min(config.curation_runs, start + 250)
            conn.executemany(
                """
                INSERT INTO curated_items (
                  run_id, item_id, weighted_score, rank, reason_json, summary_json
                ) VALUES (?, ?, 9.0, ?, '{}', NULL)
                """,
                [
                    (
                        f"run-{run_index:04d}",
                        _item_id((run_index * rows_per_run + offset) % config.items),
                        offset + 1,
                    )
                    for run_index in range(start, stop)
                    for offset in range(rows_per_run)
                ],
            )

        for start in _batches(config.interpretations, 500):
            stop = min(config.interpretations, start + 500)
            conn.executemany(
                """
                INSERT INTO wechat_interpretations (
                  item_id, slug, recommendation, save_decision, save_reason, abstract,
                  tags_json, summary_md, model, kb_synced, processed_at, error
                ) VALUES (?, ?, '值得一看', ?, '合成理由', '合成摘要', '["合成"]',
                          '### 合成解读\n\n这是只用于测试的合成正文。', 'synthetic', 0, ?, NULL)
                """,
                [
                    (
                        _item_id(index),
                        _slug(index),
                        1 if index < config.displayed_interpretations else 0,
                        _timestamp(index),
                    )
                    for index in range(start, stop)
                ],
            )
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()

    shape = ProductionShape(db_path, manifest_path, manifest)
    shape.validate()
    return shape


def validate_production_shape(db_path: Path, manifest: dict[str, Any]) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        expected_counts = manifest["table_counts"]
        actual_counts = {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "items",
                "item_evaluations",
                "curation_runs",
                "curated_items",
                "wechat_interpretations",
            )
        }
        actual_counts["displayed_wechat_interpretations"] = int(
            conn.execute("SELECT COUNT(*) FROM wechat_interpretations WHERE save_decision=1").fetchone()[0]
        )
        _require_equal("table_counts", actual_counts, expected_counts)

        expected_relations = manifest["relation_sha256"]
        actual_relations = {
            "curated_items": _relation_sha256(
                conn.execute(
                    """
                    SELECT run_id, item_id, rank
                    FROM curated_items
                    ORDER BY run_id, rank, item_id
                    """
                )
            ),
            "wechat_interpretations": _relation_sha256(
                conn.execute(
                    """
                    SELECT item_id, slug, save_decision
                    FROM wechat_interpretations
                    ORDER BY item_id
                    """
                )
            ),
        }
        _require_equal("relation_sha256", actual_relations, expected_relations)

        latest_curated_join = """
        JOIN curated_items c
          ON c.item_id=i.id
         AND c.run_id=(SELECT MAX(latest.run_id) FROM curated_items latest WHERE latest.item_id=i.id)
        """
        curated_ids = [
            str(row[0])
            for row in conn.execute(
                f"""
                SELECT i.id
                FROM items i
                {latest_curated_join}
                WHERE {deduped_item_clause('i')}
                ORDER BY i.published_at DESC, i.fetched_at DESC, i.id DESC
                """
            ).fetchall()
        ]
        visible = manifest["visible"]
        _require_equal("curated_eligible_ids", curated_ids, visible["curated_eligible_ids"])
        _require_equal("curated_total", len(curated_ids), visible["curated_total"])
        _require_equal("curated_page_1_ids", curated_ids[:40], visible["curated_page_1_ids"])
        _require_equal("curated_page_2_ids", curated_ids[40:80], visible["curated_page_2_ids"])

        wechat_rows = conn.execute(
            """
            SELECT wi.item_id, wi.slug
            FROM wechat_interpretations wi
            JOIN items i ON i.id=wi.item_id
            WHERE wi.save_decision=1
            ORDER BY i.published_at DESC, i.fetched_at DESC, i.id DESC
            """
        ).fetchall()
        _require_equal(
            "joinable_wechat_item_ids",
            [str(row["item_id"]) for row in wechat_rows],
            visible["joinable_wechat_item_ids"],
        )
        wechat_slugs = [str(row["slug"]) for row in wechat_rows]
        _require_equal("wechat_total", len(wechat_slugs), visible["wechat_total"])
        _require_equal("wechat_page_1_slugs", wechat_slugs[:50], visible["wechat_page_1_slugs"])
        _require_equal(
            "wechat_page_2_slugs",
            wechat_slugs[50:100],
            visible["wechat_page_2_slugs"],
        )
        _require_equal("detail_slug", wechat_slugs[0], visible["detail_slug"])

        invalid_urls = conn.execute(
            """
            SELECT COUNT(*)
            FROM items
            WHERE url NOT LIKE 'https://example.invalid/%'
               OR content_text NOT LIKE 'Synthetic placeholder %'
            """
        ).fetchone()[0]
        _require_equal("synthetic_item_content", invalid_urls, 0)
    finally:
        conn.close()


def manifest_sha256(shape: ProductionShape) -> str:
    return hashlib.sha256(shape.manifest_path.read_bytes()).hexdigest()
