from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from airadar.sources.loader import load_sources

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "tests/fixtures/aihot_sources.json"
CONFIG_PATH = ROOT / "data/sources.toml"


def _contract() -> list[dict[str, object]]:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))["sources"]


def _project(source: object) -> dict[str, object]:
    return {
        "slug": source.slug,
        "name": source.name,
        "kind": source.kind,
        "tier": source.tier,
        "enabled": source.enabled,
        "paused": source.paused,
        "fetch_url": source.url,
        "homepage_url": source.homepage_url,
        "icon_url": source.icon_url,
        "meta": source.meta,
    }


OPTIONAL_WECHAT_SLUGS = ["wx_mp2rss", "wx_wechat2rss"]


def _expected(row: dict[str, object], resolved: dict[str, str] | None = None) -> dict[str, object]:
    fetch_url = row["fetch_url"]
    if resolved:
        fetch_url = resolved.get(str(row["slug"]), fetch_url)
    return {key: row[key] for key in ("slug", "name", "kind", "tier", "enabled", "paused", "homepage_url", "icon_url", "meta")} | {"fetch_url": fetch_url}


def test_contract_has_exact_complete_identity_classes() -> None:
    rows = _contract()
    main = [row for row in rows if row["ai_radar_main_timeline_member"]]
    assert len(rows) == 163
    assert len(main) == 161
    assert Counter(row["kind"] for row in main) == {"x": 109, "feed": 34, "web": 18}
    assert [row["slug"] for row in rows if not row["ai_radar_main_timeline_member"]] == OPTIONAL_WECHAT_SLUGS
    assert len({row["slug"] for row in rows}) == len(rows)
    assert len({row["derived_aihot_identity"] for row in rows}) == len(rows)
    assert len({str(row["meta"]["username"]).casefold() for row in rows if row["kind"] == "x"}) == 109
    for row in rows:
        assert urlparse(str(row["homepage_url"])).scheme in {"http", "https"}
        assert isinstance(row["aihot_aliases"], list)
        assert row["name"].casefold() not in {alias.casefold() for alias in row["aihot_aliases"]}


def test_config_matches_contract_without_optional_wechat_envs(monkeypatch) -> None:
    monkeypatch.delenv("MP2RSS_FEED_URL", raising=False)
    monkeypatch.delenv("WECHAT2RSS_FEED_URL", raising=False)
    actual = load_sources(CONFIG_PATH)
    expected = [
        row
        for row in _contract()
        if row["ai_radar_main_timeline_member"] or row["slug"] == "wx_mp2rss"
    ]
    assert {_project(row)["slug"]: _project(row) for row in actual} == {str(row["slug"]): _expected(row) for row in expected}


def test_config_matches_contract_with_optional_mp2rss(monkeypatch) -> None:
    resolved = {
        "wx_mp2rss": "https://paid.example.test/private-mp2rss.xml",
        "wx_wechat2rss": "http://127.0.0.1:8080/feed/all.xml?k=example-token",
    }
    monkeypatch.setenv("MP2RSS_FEED_URL", resolved["wx_mp2rss"])
    monkeypatch.setenv("WECHAT2RSS_FEED_URL", resolved["wx_wechat2rss"])
    actual = load_sources(CONFIG_PATH)
    expected = _contract()
    assert {_project(row)["slug"]: _project(row) for row in actual} == {str(row["slug"]): _expected(row, resolved) for row in expected}


def test_main_fetch_urls_are_original_and_not_paid_or_comparison_hosts() -> None:
    forbidden = {"aihot.virxact.com", "mp2rss.com", "mp2rss.cn"}
    for row in _contract():
        if not row["ai_radar_main_timeline_member"]:
            continue
        host = urlparse(str(row["fetch_url"])).hostname
        assert host not in forbidden
        assert "${MP2RSS" not in str(row["fetch_url"])


def test_removed_extras_absent_and_post_baseline_x_present() -> None:
    slugs = {str(row["slug"]) for row in _contract()}
    assert not slugs & {"lilianweng", "sebastianraschka", "latent_space", "importai", "hn_ai", "lobsters_ai", "the_batch", "last_week_ai", "simonw_mastodon"}
    usernames = {str(row["meta"].get("username", "")).casefold() for row in _contract()}
    assert {
        "openclaw",
        "spacexai",
        "workbuddy_ai",
        "petermccrory",
        "deepseek_ai",
        "zhang_benita",
        "siliconflowai",
    } <= usernames
    assert "deepseek_api_updates" in slugs
