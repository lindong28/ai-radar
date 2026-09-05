from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from airadar.eval.aihot_fit.build import build_evalset, normalize_url
from airadar.eval.aihot_fit.common import readonly_db_uri


@pytest.mark.parametrize(
    ("left", "right", "method"),
    [
        (
            "https://x.com/i/web/status/2096113183702568991",
            "https://twitter.com/Baidu_Inc/status/2096113183702568991",
            "x_status_id",
        ),
        ("https://www.example.com/post/1/?utm_source=x#top", "https://example.com/post/1", "url"),
        # query order must not create two keys for one article
        ("https://example.com/p?b=2&a=1", "https://example.com/p?a=1&b=2", "url"),
    ],
)
def test_normalize_url_collapses_equivalent_forms(left: str, right: str, method: str) -> None:
    assert normalize_url(left) == normalize_url(right)
    assert normalize_url(left)[1] == method


def test_normalize_url_keeps_distinct_targets_apart() -> None:
    assert normalize_url("https://x.com/a/status/1")[0] != normalize_url("https://x.com/a/status/2")[0]
    assert normalize_url("https://example.com/post/1")[0] != normalize_url("https://example.com/post/2")[0]


@pytest.mark.parametrize(
    ("left", "right"),
    [
        # WeChat, Hacker News and YouTube keep the article id in the query. Dropping the
        # query collapsed every article on such a host onto one key, so one AIHOT reference
        # got paired with an unrelated item while match_method still read "url".
        (
            "https://mp.weixin.qq.com/s?__biz=AAA==&mid=1&idx=3&sn=deadbeef",
            "https://mp.weixin.qq.com/s?__biz=BBB==&mid=2&idx=1&sn=cafe0000",
        ),
        ("https://news.ycombinator.com/item?id=48148293", "https://news.ycombinator.com/item?id=49548600"),
        ("https://youtube.com/watch?v=aaaaaaaaaaa", "https://youtube.com/watch?v=bbbbbbbbbbb"),
    ],
)
def test_normalize_url_keeps_query_identified_articles_apart(left: str, right: str) -> None:
    assert normalize_url(left)[0] != normalize_url(right)[0]


def _seed_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE sources (id TEXT PRIMARY KEY, tier TEXT NOT NULL);
        CREATE TABLE items (
          id TEXT PRIMARY KEY, source_id TEXT NOT NULL, url TEXT NOT NULL, title TEXT NOT NULL,
          author TEXT, published_at TEXT NOT NULL, fetched_at TEXT NOT NULL, content_text TEXT NOT NULL
        );
        INSERT INTO sources VALUES ('src-x', 'T1');
        INSERT INTO items VALUES (
          'item-1', 'src-x', 'https://x.com/i/web/status/42', 'Hello', NULL,
          '2026-08-19T01:00:00Z', '2026-08-19T02:00:00Z', 'body text'
        );
        """
    )
    conn.commit()
    conn.close()


def test_build_matches_one_and_counts_one_unmatched(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.db"
    _seed_db(db_path)
    batch = tmp_path / "items.jsonl"
    records = [
        {
            "id": "aihot-1",
            "aihot_url": "https://aihot.example/items/aihot-1",
            "original_url": "https://twitter.com/someone/status/42",
            "aihot_title": "你好",
            "aihot_category_slug": "tip",
            "tags": ["Agent"],
            "aihot_score_0_to_100": 71,
            "aihot_selected": False,
            "aihot_summary": "摘要",
            "aihot_recommendation_reason": None,
            "published_at": "2026-08-19T00:00:00.000Z",
        },
        {
            "id": "aihot-2",
            "original_url": "https://example.com/not-in-db",
            "aihot_category_slug": "paper",
            "tags": [],
            "aihot_score_0_to_100": 10,
            "aihot_selected": False,
        },
    ]
    batch.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")

    manifest = build_evalset(db_path=db_path, out_dir=tmp_path / "out", sources=(("test-batch", batch),))

    assert manifest["question_count"] == 1
    assert manifest["batches"]["test-batch"] == {
        "source_file": str(batch),
        "source_sha256": manifest["batches"]["test-batch"]["source_sha256"],
        "read": 2,
        "matched": 1,
        "unmatched": 1,
        "deduped": 0,
        "kept": 1,
    }
    questions = [json.loads(line) for line in (tmp_path / "out" / "questions.jsonl").read_text().splitlines()]
    assert len(questions) == 1
    question = questions[0]
    assert question["input"]["item_id"] == "item-1" and question["input"]["tier"] == "T1"
    assert question["reference"]["primary_category"] == "tutorial"
    assert question["reference"]["provider"] == "aihot"
    assert question["provenance"]["match_method"] == "x_status_id"
    # The db must be unwritable through the URI build uses. Asserting "no -wal file
    # appeared" proves nothing here: the fixture db is in `delete` journal mode, so that
    # file never appears whether or not mode=ro was applied. Assert the connection
    # actually refuses a write instead.
    with sqlite3.connect(readonly_db_uri(db_path), uri=True) as probe:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            probe.execute("UPDATE items SET title = 'mutated' WHERE id = 'item-1'")
