from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from airadar import db


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts/eval/measure_archive_composition.py"
    spec = importlib.util.spec_from_file_location("measure_archive_composition_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_visible_fields_use_latest_curated_row_before_historical_cutoff(tmp_path: Path) -> None:
    module = _load_module()
    db_path = tmp_path / "archive.db"
    db.migrate(db_path)
    with db.get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO sources (id, name, url, tier, enabled, kind, meta_json, synced_at)
            VALUES (
              'source-1', 'Synthetic source', 'https://example.invalid/feed', 'T1', 1, 'feed', '{}',
              '2026-09-14T00:00:00Z'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO items (
              id, source_id, url, title, published_at, fetched_at, content_text, content_hash, extra_json
            ) VALUES (
              'item-1', 'source-1', 'https://example.invalid/item', 'Synthetic title',
              '2026-09-14T00:00:00Z', '2026-09-14T00:00:00Z', 'Synthetic content', 'hash-1', '{}'
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO curation_runs (
              id, ruleset_version, weights_json, threshold, input_eval_ids, output_curated_ids, created_at
            ) VALUES (?, 'synthetic.r1', '{}', 0, '[]', '["item-1"]', ?)
            """,
            (
                ("20260914T120000Z", "2026-09-14T12:00:00Z"),
                ("20260914T180000Z", "2026-09-14T18:00:00Z"),
            ),
        )
        conn.executemany(
            """
            INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
            VALUES (?, 'item-1', ?, 1, '{}')
            """,
            (("20260914T120000Z", 7.1), ("20260914T180000Z", 9.2)),
        )
        conn.execute(
            """
            INSERT INTO item_evaluations (
              item_id, stage, ruleset_version, model_id, input_json, output_json, numeric_json, evaluated_at
            ) VALUES ('item-1', 'enrich', 'v1', 'synthetic', '{}', ?, '{}', '2026-09-14T12:00:00Z')
            """,
            (json.dumps({"tags": ["模型发布"]}, ensure_ascii=False),),
        )
        conn.execute(
            """
            INSERT INTO item_evaluations (
              item_id, stage, ruleset_version, model_id, input_json, output_json, numeric_json, evaluated_at
            ) VALUES ('item-1', 'enrich', 'v2', 'synthetic', '{}', ?, '{}', '2026-09-14T18:00:00Z')
            """,
            (json.dumps({"tags": ["产业动态"]}, ensure_ascii=False),),
        )
        conn.execute(
            """
            INSERT INTO items (
              id, source_id, url, title, published_at, fetched_at, content_text, content_hash, extra_json
            ) VALUES (
              'item-2', 'source-1', 'https://example.invalid/item-2', 'OpenAI synthetic title',
              '2026-09-13T23:00:00Z', '2026-09-13T23:00:00Z', 'Synthetic content', 'hash-2', '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
            VALUES ('20260914T120000Z', 'item-2', 6.8, 2, '{}')
            """
        )
        conn.execute(
            """
            INSERT INTO item_evaluations (
              item_id, stage, ruleset_version, model_id, input_json, output_json, numeric_json, evaluated_at
            ) VALUES ('item-2', 'enrich', 'v2', 'synthetic', '{}', ?, '{}', '2026-09-14T18:00:00Z')
            """,
            (json.dumps({"tags": ["模型发布"]}, ensure_ascii=False),),
        )
        conn.commit()

        rows = module.archive_page_visible_fields(conn, "2026-09-14", 40)

    by_url = {row["url"]: row for row in rows}
    assert len(rows) == 2
    assert by_url["https://example.invalid/item"]["display_score_0_100"] == 71
    assert "模型发布" in by_url["https://example.invalid/item"]["tags"]
    assert "产业动态" not in by_url["https://example.invalid/item"]["tags"]
    assert by_url["https://example.invalid/item-2"]["tags"] == []


def test_custom_history_does_not_pollute_the_canonical_ledger(tmp_path: Path) -> None:
    module = _load_module()

    assert module._record_ledger_path(tmp_path / "exploration.jsonl", None) is None
    assert module._record_ledger_path(module._comp.DEFAULT_HISTORY, None) == module.DEFAULT_LEDGER_PATH

    try:
        module._record_ledger_path(tmp_path / "exploration.jsonl", str(module.DEFAULT_LEDGER_PATH))
    except SystemExit as exc:
        assert "custom composition history" in str(exc)
    else:
        raise AssertionError("custom history was allowed to pollute the canonical ledger")
