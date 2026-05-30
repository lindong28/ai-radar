from __future__ import annotations

import os
from pathlib import Path

import pytest

from airadar.fetcher.wechat import parse_article_html, scrape_article

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "wechat"
SEED_CASES = [
    (
        "guizang_seed.html",
        "https://mp.weixin.qq.com/s/KWtnToEa7K-13k002K-nRw",
        ("guizang-social-card-skill", "28 个版式骨架"),
        "歸藏的 AI 工具箱",
    ),
    (
        "crossing_seed.html",
        "https://mp.weixin.qq.com/s/kORnjtyhEntmcQH4j8H4nw",
        ("TCC", "dump_ui"),
        "十字路口 Crossing",
    ),
]


@pytest.mark.parametrize(("fixture_name", "url", "features", "author"), SEED_CASES)
def test_parse_article_html_extracts_wechat_seed_fixtures(
    fixture_name: str, url: str, features: tuple[str, str], author: str
) -> None:
    html = (FIXTURE_DIR / fixture_name).read_text(encoding="utf-8")

    article = parse_article_html(html, url)

    assert article["success"] is True
    assert article["url"] == url
    assert article["author"] == author
    assert article["title"]
    assert article["publish_time"]
    assert "<script" not in article["content_html"]
    assert "<style" not in article["content_html"]
    assert all(feature in article["content_text"] for feature in features)


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("AIRADAR_RUN_LIVE_WECHAT") != "1",
    reason="set AIRADAR_RUN_LIVE_WECHAT=1 to hit live mp.weixin.qq.com URLs",
)
@pytest.mark.parametrize(
    ("url", "features"),
    [
        ("https://mp.weixin.qq.com/s/KWtnToEa7K-13k002K-nRw", ("guizang-social-card-skill", "28 个版式骨架")),
        ("https://mp.weixin.qq.com/s/kORnjtyhEntmcQH4j8H4nw", ("TCC", "dump_ui")),
    ],
)
def test_scrape_article_live_seed_urls(url: str, features: tuple[str, str]) -> None:
    article = scrape_article(url)

    assert article["success"] is True, article.get("error")
    assert all(feature in article["content_text"] for feature in features)
