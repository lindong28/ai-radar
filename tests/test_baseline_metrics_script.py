from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from airadar.db import migrate


def test_dump_baseline_metrics_outputs_required_json_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.db"
    output_path = tmp_path / "baseline.json"
    backup_path = tmp_path / "radar.db.bak-test"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO sources (id, name, url, tier, enabled, kind, homepage_url, icon_url, meta_json, synced_at)
        VALUES ('disabled_source', 'Disabled', 'https://example.com/feed', 'T2', 0, 'feed', NULL, NULL, '{}', '2026-05-13T00:00:00Z')
        """
    )
    conn.commit()
    conn.close()

    subprocess.run(
        [
            sys.executable,
            "scripts/dump_baseline_metrics.py",
            "--db",
            str(db_path),
            "--backup-path",
            str(backup_path),
            "--output",
            str(output_path),
        ],
        check=True,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert set(payload) >= {
        "backup_path",
        "items_total",
        "items_nitter",
        "curated_total",
        "curated_by_source",
        "disabled_curated_ratio",
        "top_sources_by_curated",
    }
    assert payload["backup_path"] == str(backup_path)
    assert payload["disabled_curated_ratio"] is None
