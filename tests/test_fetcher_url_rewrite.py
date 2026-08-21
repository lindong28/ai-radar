from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from airadar.db import migrate
from airadar.fetcher.urls import canonicalize_item_url


def test_canonicalize_item_url_rewrites_nitter_status_and_drops_fragment() -> None:
    assert (
        canonicalize_item_url("https://nitter.net/OpenAI/status/2053939702110269822#m")[0]
        == "https://x.com/openai/status/2053939702110269822"
    )
    assert (
        canonicalize_item_url("https://twitter.com/BerryXia/status/2053978304181567961?utm_source=rss")[0]
        == "https://x.com/berryxia/status/2053978304181567961"
    )


def test_canonicalize_item_url_preserves_original_url_in_extra_json() -> None:
    url, extra = canonicalize_item_url(
        "https://nitter.net/OpenAI/status/2053939702110269822#m",
        {"guid": "entry-1"},
    )

    assert url == "https://x.com/openai/status/2053939702110269822"
    assert extra == {
        "guid": "entry-1",
        "original_url": "https://nitter.net/OpenAI/status/2053939702110269822#m",
    }


def test_rewrite_nitter_urls_script_backfills_items_and_original_url(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO sources (id, name, url, tier, enabled, kind, homepage_url, icon_url, meta_json, synced_at)
        VALUES ('x_source', 'X Source', 'https://example.com/feed', 'T1', 1, 'x', 'https://x.com/openai', NULL, '{}', '2026-05-13T00:00:00Z')
        """
    )
    conn.execute(
        """
        INSERT INTO items (
          id, source_id, url, title, author, published_at, fetched_at,
          content_text, content_html, content_hash, extra_json
        )
        VALUES (
          'item-nitter', 'x_source', 'https://nitter.net/OpenAI/status/2053939702110269822#m',
          'OpenAI status', 'OpenAI', '2026-05-13T00:00:00Z', '2026-05-13T00:01:00Z',
          'AI model update', NULL, 'hash-nitter', '{"guid":"entry-1"}'
        )
        """
    )
    conn.commit()
    conn.close()

    subprocess.run(
        [
            sys.executable,
            "scripts/rewrite_nitter_urls.py",
            "--db",
            str(db_path),
            "--no-probe",
        ],
        check=True,
    )

    row = sqlite3.connect(db_path).execute("SELECT url, extra_json FROM items WHERE id='item-nitter'").fetchone()
    assert row[0] == "https://x.com/openai/status/2053939702110269822"
    assert json.loads(row[1])["original_url"] == "https://nitter.net/OpenAI/status/2053939702110269822#m"


def test_canonicalize_item_url_leaves_a_wechat_article_query_exactly_as_published() -> None:
    # The base64 `__biz` value ends in `==`. Rebuilding the query turns that
    # into `%3D%3D`, and this URL is what a reader clicks through to.
    published = "https://mp.weixin.qq.com/s?__biz=MTQzMjE1NjQwMQ==&mid=2656194904&idx=4&sn=904d426d"
    assert canonicalize_item_url(published)[0] == published


def test_canonicalize_item_url_still_strips_tracking_params_from_wechat_urls() -> None:
    canonical = canonicalize_item_url("https://mp.weixin.qq.com/s?__biz=AA==&utm_source=x&mid=1")[0]
    assert "utm_source" not in canonical
    assert "mid=1" in canonical
