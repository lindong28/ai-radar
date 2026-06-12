"""Phase 1 schema tests for new sources.toml fields: kind / homepage_url / icon_url."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from airadar.sources.loader import SourceConfig, load_sources


def _write_toml(path: Path, lines: list[str]) -> Path:
    out = path / "sources.toml"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def test_kind_defaults_to_feed(tmp_path: Path) -> None:
    path = _write_toml(
        tmp_path,
        [
            "[[source]]",
            'slug = "minimal"',
            'name = "Minimal Feed"',
            'url = "https://example.com/feed.xml"',
            'tier = "T2"',
        ],
    )
    sources = load_sources(path)
    assert len(sources) == 1
    assert sources[0].kind == "feed"
    assert sources[0].homepage_url is None
    assert sources[0].icon_url is None


def test_kind_x_is_accepted(tmp_path: Path) -> None:
    path = _write_toml(
        tmp_path,
        [
            "[[source]]",
            'slug = "twitter_user"',
            'name = "X User"',
            'url = "https://rsshub.app/twitter/user/example"',
            'tier = "T1.5"',
            'kind = "x"',
            'homepage_url = "https://x.com/example"',
            'icon_url = "https://abs.twimg.com/favicons/twitter.ico"',
        ],
    )
    source = load_sources(path)[0]
    assert source.kind == "x"
    assert source.homepage_url == "https://x.com/example"
    assert source.icon_url == "https://abs.twimg.com/favicons/twitter.ico"


def test_kind_wechat_is_accepted(tmp_path: Path) -> None:
    path = _write_toml(
        tmp_path,
        [
            "[[source]]",
            'slug = "wx_guizang"',
            'name = "歸藏的 AI 工具箱"',
            'url = "http://localhost:4000/feeds/guizang.rss"',
            'tier = "T2"',
            'kind = "wechat"',
            'homepage_url = "https://mp.weixin.qq.com/"',
        ],
    )
    source = load_sources(path)[0]
    assert source.kind == "wechat"
    assert source.homepage_url == "https://mp.weixin.qq.com/"


def test_invalid_kind_rejected(tmp_path: Path) -> None:
    path = _write_toml(
        tmp_path,
        [
            "[[source]]",
            'slug = "bad_kind"',
            'name = "Bad"',
            'url = "https://example.com/feed.xml"',
            'tier = "T2"',
            'kind = "podcast"',
        ],
    )
    with pytest.raises(ValueError, match="invalid kind"):
        load_sources(path)


@pytest.mark.parametrize("field", ["homepage_url", "icon_url"])
def test_optional_url_fields_validated_when_present(tmp_path: Path, field: str) -> None:
    path = _write_toml(
        tmp_path,
        [
            "[[source]]",
            'slug = "bad_url"',
            'name = "Bad URL"',
            'url = "https://example.com/feed.xml"',
            'tier = "T2"',
            f'{field} = "ftp://nope.example/icon"',
        ],
    )
    with pytest.raises(ValueError, match=field):
        load_sources(path)


def test_dataclass_supports_new_fields_directly() -> None:
    source = SourceConfig(
        slug="x_user",
        name="X User",
        url="https://rsshub.app/twitter/user/example",
        tier="T1.5",
        kind="x",
        homepage_url="https://x.com/example",
        icon_url="https://abs.twimg.com/favicons/twitter.ico",
    )
    assert source.kind == "x"
    assert source.homepage_url == "https://x.com/example"
    assert source.icon_url == "https://abs.twimg.com/favicons/twitter.ico"
    assert source.enabled is True


def test_url_expands_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_MP2RSS_URL", "https://mp2rss.example/feed/secret.xml")
    path = _write_toml(
        tmp_path,
        [
            "[[source]]",
            'slug = "wx_mp2rss"',
            'name = "WeChat collection"',
            'url = "${TEST_MP2RSS_URL}"',
            'tier = "T2"',
            'kind = "wechat"',
        ],
    )
    source = load_sources(path)[0]
    assert source.url == "https://mp2rss.example/feed/secret.xml"


@pytest.mark.parametrize("env_value", [None, ""])
def test_mp2rss_source_skips_when_feed_env_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    env_value: str | None,
) -> None:
    if env_value is None:
        monkeypatch.delenv("MP2RSS_FEED_URL", raising=False)
    else:
        monkeypatch.setenv("MP2RSS_FEED_URL", env_value)
    path = _write_toml(
        tmp_path,
        [
            "[[source]]",
            'slug = "openai_blog"',
            'name = "OpenAI Blog"',
            'url = "https://openai.com/news/rss.xml"',
            'tier = "T1"',
            "",
            "[[source]]",
            'slug = "wx_mp2rss"',
            'name = "WeChat collection"',
            'url = "${MP2RSS_FEED_URL}"',
            'tier = "T2"',
            'kind = "wechat"',
        ],
    )

    with caplog.at_level(logging.WARNING, logger="airadar.sources.loader"):
        sources = load_sources(path)

    assert [source.slug for source in sources] == ["openai_blog"]
    assert "wx_mp2rss" in caplog.text
    assert "MP2RSS_FEED_URL" in caplog.text
    assert "skipped" in caplog.text


def test_mp2rss_source_loads_when_feed_env_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MP2RSS_FEED_URL", "https://mp2rss.example/feed/secret.xml")
    path = _write_toml(
        tmp_path,
        [
            "[[source]]",
            'slug = "openai_blog"',
            'name = "OpenAI Blog"',
            'url = "https://openai.com/news/rss.xml"',
            'tier = "T1"',
            "",
            "[[source]]",
            'slug = "wx_mp2rss"',
            'name = "WeChat collection"',
            'url = "${MP2RSS_FEED_URL}"',
            'tier = "T2"',
            'kind = "wechat"',
        ],
    )

    sources = load_sources(path)

    assert [source.slug for source in sources] == ["openai_blog", "wx_mp2rss"]
    assert sources[1].url == "https://mp2rss.example/feed/secret.xml"


def test_url_unset_env_var_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_MP2RSS_URL", raising=False)
    path = _write_toml(
        tmp_path,
        [
            "[[source]]",
            'slug = "wx_mp2rss"',
            'name = "WeChat collection"',
            'url = "${TEST_MP2RSS_URL}"',
            'tier = "T2"',
            'kind = "wechat"',
        ],
    )
    with pytest.raises(ValueError, match="unset env var"):
        load_sources(path)
