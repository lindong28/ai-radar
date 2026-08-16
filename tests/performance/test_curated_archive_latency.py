from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from production_shape import ProductionShape

from airadar.web.app import create_app


def _preload(html: str) -> dict[str, Any]:
    match = re.search(
        r'<script id="__PRELOAD__" type="application/json">\s*(.*?)\s*</script>',
        html,
        re.S,
    )
    assert match is not None
    return json.loads(match.group(1))


@pytest.mark.performance
def test_production_shape_fixture_matches_frozen_request_identities(
    isolated_production_shape: ProductionShape,
) -> None:
    shape = isolated_production_shape
    shape.validate()
    visible = shape.manifest["visible"]

    with TestClient(create_app(shape.db_path)) as client:
        curated_page_1 = client.get("/api/v1/curated?page=1&limit=40")
        curated_page_2 = client.get("/api/v1/curated?page=2&limit=40")
        homepage = client.get("/?page=1&limit=40")
        category = client.get("/api/v1/curated?category=ai-models&page=1&limit=40")
        search = client.get("/api/v1/curated?q=29999&page=1&limit=40")
        wechat_page_1 = client.get("/api/v1/wechat?page=1&limit=50")
        wechat_page_2 = client.get("/api/v1/wechat?page=2&limit=50")
        wechat_ssr = client.get("/wechat")
        detail = client.get(f"/wechat/{visible['detail_slug']}")

    for response in (
        curated_page_1,
        curated_page_2,
        homepage,
        category,
        search,
        wechat_page_1,
        wechat_page_2,
        wechat_ssr,
        detail,
    ):
        assert response.status_code == 200

    curated_1 = curated_page_1.json()["data"]
    curated_2 = curated_page_2.json()["data"]
    assert curated_1["total"] == visible["curated_total"]
    assert curated_2["total"] == visible["curated_total"]
    assert [item["id"] for item in curated_1["items"]] == visible["curated_page_1_ids"]
    assert [item["id"] for item in curated_2["items"]] == visible["curated_page_2_ids"]
    homepage_preload = _preload(homepage.text)
    assert homepage_preload["total"] == visible["curated_total"]
    assert [item["id"] for item in homepage_preload["items"]] == visible["curated_page_1_ids"]
    assert category.json()["data"]["total"] == visible["curated_total"]
    assert [item["id"] for item in search.json()["data"]["items"]] == ["item-29999"]

    wechat_1 = wechat_page_1.json()["data"]
    wechat_2 = wechat_page_2.json()["data"]
    assert wechat_1["total"] == visible["wechat_total"]
    assert wechat_2["total"] == visible["wechat_total"]
    assert [item["slug"] for item in wechat_1["items"]] == visible["wechat_page_1_slugs"]
    assert [item["slug"] for item in wechat_2["items"]] == visible["wechat_page_2_slugs"]
    wechat_preload = _preload(wechat_ssr.text)
    assert wechat_preload["total"] == visible["wechat_total"]
    assert [item["slug"] for item in wechat_preload["items"]] == visible["wechat_page_1_slugs"]
    assert visible["detail_title"] in detail.text

@pytest.mark.performance
def test_equal_count_curated_relation_mislink_fails_before_timing(
    production_shape: ProductionShape,
    tmp_path: Path,
) -> None:
    broken = production_shape.clone(tmp_path / "broken-curated.db")
    conn = sqlite3.connect(broken.db_path)
    try:
        conn.execute(
            """
            UPDATE curated_items
            SET item_id='item-29999'
            WHERE run_id='run-0000' AND rank=1
            """
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(AssertionError):
        broken.validate()


@pytest.mark.performance
def test_equal_count_interpretation_relation_mislink_fails_before_timing(
    production_shape: ProductionShape,
    tmp_path: Path,
) -> None:
    broken = production_shape.clone(tmp_path / "broken-interpretation.db")
    conn = sqlite3.connect(broken.db_path)
    try:
        conn.execute(
            """
            UPDATE wechat_interpretations
            SET item_id='item-01799'
            WHERE item_id='item-17990'
            """
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(AssertionError):
        broken.validate()


@pytest.mark.performance
def test_relation_mislink_fails_with_python_optimization_enabled(
    production_shape: ProductionShape,
    tmp_path: Path,
) -> None:
    broken = production_shape.clone(tmp_path / "broken-optimized.db")
    conn = sqlite3.connect(broken.db_path)
    try:
        conn.execute(
            """
            UPDATE curated_items
            SET item_id='item-29999'
            WHERE run_id='run-0000' AND rank=1
            """
        )
        conn.commit()
    finally:
        conn.close()

    code = """
import json
from pathlib import Path
from production_shape import validate_production_shape

db_path = Path(__import__('sys').argv[1])
manifest_path = Path(__import__('sys').argv[2])
validate_production_shape(db_path, json.loads(manifest_path.read_text()))
"""
    environment = dict(os.environ)
    pythonpath = [str(Path(__file__).parent), str(Path(__file__).parents[2] / "src")]
    if environment.get("PYTHONPATH"):
        pythonpath.append(environment["PYTHONPATH"])
    environment["PYTHONPATH"] = os.pathsep.join(pythonpath)
    result = subprocess.run(
        [sys.executable, "-O", "-c", code, str(broken.db_path), str(broken.manifest_path)],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "relation_sha256" in result.stderr
