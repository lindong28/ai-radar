from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

from airadar import db


def _load_script() -> ModuleType:
    path = db.PROJECT_ROOT / "scripts" / "verify_admin_metrics.py"
    spec = importlib.util.spec_from_file_location("verify_admin_metrics", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_verifier_does_not_recreate_removed_evaluation_cost_metric(tmp_path: Path) -> None:
    module = _load_script()
    db_path = tmp_path / "radar.db"
    db.migrate(db_path)
    with db.get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO sources (id,name,url,tier,enabled,meta_json,synced_at)
            VALUES ('s','Source','https://example.com/feed','T1',1,'{}','2026-08-11T00:00:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO items (
              id,source_id,url,title,published_at,fetched_at,
              content_text,content_hash,extra_json
            ) VALUES (
              'i','s','https://example.com/i','Item','2026-08-11T00:00:00Z',
              '2026-08-11T00:00:00Z','body','hash','{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO item_evaluations (
              item_id,stage,ruleset_version,model_id,input_json,output_json,
              numeric_json,latency_ms,cost_usd,evaluated_at,error
            ) VALUES (
              'i','scoring','r1','model','{}','{}','{}',10,NULL,
              '2026-08-11T01:00:00Z',NULL
            )
            """
        )
        conn.commit()

    metrics = module.evaluation_stage_metrics(
        db_path,
        datetime(2026, 8, 11, tzinfo=UTC),
        datetime(2026, 8, 12, tzinfo=UTC),
    )

    assert metrics["scoring"] == {
        "processed": 1,
        "errors": 0,
        "error_rate": 0.0,
        "p50_latency_ms": 10,
        "p95_latency_ms": 10,
    }
