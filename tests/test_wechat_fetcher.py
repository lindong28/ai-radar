from __future__ import annotations

import os
from pathlib import Path

import pytest

from airadar.fetcher.wechat import extract_round_head_img, parse_article_html, scrape_article

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


def test_parse_article_html_extracts_round_head_img_avatar() -> None:
    html = """
    <html>
      <head><script>var round_head_img = "https:\\/\\/mmbiz.qpic.cn\\/mmbiz_png\\/avatar\\/0?wx_fmt=png&amp;tp=webp";</script></head>
      <body>
        <h1 id="activity-name">Seed Title</h1>
        <span id="js_author_name">歸藏的 AI 工具箱</span>
        <em id="publish_time">2026年06月01日</em>
        <div id="js_content">Full article body</div>
      </body>
    </html>
    """

    article = parse_article_html(html, "https://mp.weixin.qq.com/s/seed")

    assert article["author_avatar_url"] == "https://mmbiz.qpic.cn/mmbiz_png/avatar/0?wx_fmt=png&tp=webp"


def test_extract_round_head_img_supports_protocol_relative_urls() -> None:
    html = "<script>window.round_head_img='//mmbiz.qpic.cn/mmbiz_jpg/avatar/0';</script>"

    assert extract_round_head_img(html) == "https://mmbiz.qpic.cn/mmbiz_jpg/avatar/0"


def test_extract_round_head_img_upgrades_mmbiz_http_urls_to_https() -> None:
    html = "<script>var round_head_img='http://mmbiz.qpic.cn/mmbiz_png/avatar/0?wx_fmt=png';</script>"

    assert extract_round_head_img(html) == "https://mmbiz.qpic.cn/mmbiz_png/avatar/0?wx_fmt=png"


def test_extract_round_head_img_supports_object_property_colon_syntax() -> None:
    # 某些公众号文章页（如「歸藏的AI工具箱」）用 JS 对象属性冒号形式，而非等号赋值
    html = "<script>window.__mp={round_head_img: 'http://mmbiz.qpic.cn/mmbiz_png/guizang/0?wx_fmt=png'};</script>"

    assert extract_round_head_img(html) == "https://mmbiz.qpic.cn/mmbiz_png/guizang/0?wx_fmt=png"


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
