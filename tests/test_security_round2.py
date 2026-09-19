"""Regression tests for the second security-review round (2026-09-19).

Findings are quoted by the reviewer's numbering in
`.local/security-review-20260919/05..08` (not tracked); the assertion text
carries enough of the finding that the mapping survives without them.
"""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from airadar import runtime_env
from airadar.egress import open_external_url
from airadar.fetcher.wechat import normalize_wechat_avatar_url
from airadar.fetcher.x_api import _media_still_url
from airadar.interpret import runner
from airadar.presentation.media import PROXY_IMAGE_HOST_SUFFIXES, proxy_image_url, public_url
from airadar.sources.loader import _expand_url_placeholder
from airadar.web.routes.media import _scrub
from airadar.web.routes.wechat import _detail_url

# --- runtime_env: only declared keys leave the shared dotenv -----------------


def test_runtime_env_loads_only_declared_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    shared = tmp_path / "claude.env"
    shared.write_text(
        "GITHUB_TOKEN=other-project\nDEEPSEEK_API_KEY=ours\nAI_RADAR_ANYTHING=prefixed\nSLACK_BOT_TOKEN=no\n",
        encoding="utf-8",
    )
    project = tmp_path / "project.env"
    project.write_text("CLOUDFLARE_API_TOKEN=nope\nX_BEARER_TOKEN=yes\n", encoding="utf-8")
    for key in ("GITHUB_TOKEN", "DEEPSEEK_API_KEY", "AI_RADAR_ANYTHING", "SLACK_BOT_TOKEN", "CLOUDFLARE_API_TOKEN", "X_BEARER_TOKEN"):
        monkeypatch.delenv(key, raising=False)

    runtime_env.load_runtime_env(project_env=project, shared_env=shared)

    assert os.environ["DEEPSEEK_API_KEY"] == "ours"
    assert os.environ["AI_RADAR_ANYTHING"] == "prefixed"
    assert os.environ["X_BEARER_TOKEN"] == "yes"
    for leaked in ("GITHUB_TOKEN", "SLACK_BOT_TOKEN", "CLOUDFLARE_API_TOKEN"):
        assert leaked not in os.environ, leaked


def test_runtime_env_allowlist_covers_every_key_declared_in_env_example() -> None:
    example = Path(__file__).resolve().parents[1] / ".env.example"
    declared = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", example.read_text(encoding="utf-8"), re.M))
    assert declared, "no keys parsed from .env.example"
    missing = {key for key in declared if not runtime_env.is_runtime_env_key(key)}
    assert not missing, f"declared in .env.example but not loadable: {sorted(missing)}"


def test_read_value_still_resolves_undeclared_keys_explicitly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shared = tmp_path / "claude.env"
    shared.write_text("SOME_OTHER_KEY=value\n", encoding="utf-8")
    monkeypatch.delenv("SOME_OTHER_KEY", raising=False)
    assert runtime_env.read_value("SOME_OTHER_KEY", project_env=tmp_path / "none", shared_env=shared) == "value"
    assert "SOME_OTHER_KEY" not in os.environ


# --- interpret: subprocess env allowlist, path confinement, error scrub -----


def test_interpret_subprocess_env_drops_unrelated_secrets() -> None:
    source = {
        "PATH": "/usr/bin",
        "HOME": "/home/x",
        "DEEPSEEK_API_KEY": "k",
        "AI_ASSISTANT_ROOT": "/opt/ai",
        "LC_ALL": "C",
        "X_BEARER_TOKEN": "leak",
        "AI_RADAR_ADMIN_TOKEN": "leak",
        "AI_RADAR_IMG_PROXY_URL": "http://u:p@proxy",
        "EDGEONE_SECRET_KEY": "leak",
        "FEISHU_GENERAL_ALERT_WEBHOOK": "leak",
        "GITHUB_TOKEN": "leak",
    }
    env = runner._subprocess_env_source(source)
    assert set(env) == {"PATH", "HOME", "DEEPSEEK_API_KEY", "AI_ASSISTANT_ROOT", "LC_ALL"}


@pytest.mark.parametrize("value", ["../../etc/hosts", "/etc/hosts", "sub/../../x"])
def test_summary_file_path_must_stay_under_assistant_root(tmp_path: Path, value: str) -> None:
    root = tmp_path / "assistant"
    root.mkdir()
    with pytest.raises(ValueError, match="summary_file_path escapes"):
        runner._path_from_ai_assistant(root, value)


def test_summary_file_path_inside_root_is_accepted(tmp_path: Path) -> None:
    root = tmp_path / "assistant"
    (root / "data").mkdir(parents=True)
    assert runner._path_from_ai_assistant(root, "data/x_summary.md") == (root / "data" / "x_summary.md").resolve()
    assert runner._path_from_ai_assistant(root, str(root / "data" / "y.md")) == (root / "data" / "y.md").resolve()


def test_confined_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside)
    with pytest.raises(ValueError, match="batch_dir escapes"):
        runner._confined(root, root / "link", "batch_dir")


@pytest.mark.parametrize("slug", ["../x", "a/b", "", "x" * 201, "a b", "a\nb"])
def test_batch_slug_regex_rejects_path_shaped_values(slug: str) -> None:
    assert not runner._BATCH_SLUG_RE.fullmatch(slug)


@pytest.mark.parametrize("slug", ["ok-slug_1", "中文标题", "a" * 200])
def test_batch_slug_regex_accepts_plain_slugs(slug: str) -> None:
    assert runner._BATCH_SLUG_RE.fullmatch(slug)


def test_run_json_has_a_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(runner, "_subprocess_env", lambda **_: {"PATH": "/usr/bin"})
    runner._run_json(["x"], cwd=tmp_path, callsite_id="test")
    assert seen["timeout"] == runner.SUBPROCESS_TIMEOUT_S > 0


def test_record_error_scrubs_secrets_from_stderr(tmp_path: Path) -> None:
    text = (
        "Traceback\nAuthorization: Bearer sk-abcdef123456\nDEEPSEEK_API_KEY=sk-zzz "
        "https://user:pa55@proxy.internal/x token=t0k3n done"
    )
    scrubbed = runner._scrub_error(text)
    for secret in ("sk-abcdef123456", "sk-zzz", "pa55", "t0k3n"):
        assert secret not in scrubbed, secret
    assert "<redacted>" in scrubbed


# --- egress: http(s) only ---------------------------------------------------


@pytest.mark.parametrize("url", ["file://localhost/etc/passwd", "file:///etc/passwd", "ftp://localhost/x"])
def test_open_external_url_refuses_non_http_schemes(url: str) -> None:
    with pytest.raises(ValueError, match="only accepts http"):
        open_external_url(url, callsite_id="test", timeout=1)


# --- presentation: read-time URL scheme gate --------------------------------


@pytest.mark.parametrize("value", ["javascript:alert(1)", "data:text/html,x", "about:blank", "", None, "http:///no-host"])
def test_public_url_drops_non_http_values(value: object) -> None:
    assert public_url(value) is None


def test_public_url_keeps_http_and_https() -> None:
    assert public_url("https://example.test/a?b=c") == "https://example.test/a?b=c"
    assert public_url("  http://example.test/ ") == "http://example.test/"


def test_item_summary_applies_public_url_to_link_fields(tmp_path: Path) -> None:
    from airadar.db import migrate
    from airadar.web.app import create_app

    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO sources (id,name,url,tier,enabled,meta_json,synced_at,homepage_url) "
        "VALUES ('s','S','https://example.test/feed','T1',1,'{}','2026-06-01T00:00:00Z','javascript:alert(2)')"
    )
    conn.execute(
        """
        INSERT INTO items (id, source_id, url, title, author, published_at, fetched_at,
                           content_text, content_html, content_hash, extra_json)
        VALUES ('deadbeefdeadbeef', 's', 'javascript:alert(1)', 'T', NULL,
                '2026-09-19T00:00:00Z', '2026-09-19T00:00:00Z', 'body', NULL, 'h', '{}')
        """
    )
    conn.commit()
    conn.close()
    client = TestClient(create_app(db_path))

    response = client.get("/api/v1/items/deadbeefdeadbeef")

    assert response.status_code == 200
    item = response.json()["data"]["item"]
    assert item["url"] is None
    assert item["source_homepage_url"] is None
    assert "javascript:" not in response.text


# --- avatars / X media / config expansion / log scrub -----------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://mmbiz.qpic.cn/x/0", "https://mmbiz.qpic.cn/x/0"),
        ("http://mmbiz.qpic.cn/x/0", "https://mmbiz.qpic.cn/x/0"),
        ("//wx.qlogo.cn/mmhead/abc/0", "https://wx.qlogo.cn/mmhead/abc/0"),
        ("https://evil.test/avatar.png", None),
        ("https://qpic.cn.evil.test/x", None),
        ("https://evil.test/#qpic.cn", None),
        ("javascript:alert(1)", None),
    ],
)
def test_wechat_avatar_url_requires_tencent_image_host(value: str, expected: str | None) -> None:
    assert normalize_wechat_avatar_url(value) == expected


def test_qlogo_avatars_are_proxied_like_qpic() -> None:
    assert "qlogo.cn" in PROXY_IMAGE_HOST_SUFFIXES
    assert proxy_image_url("https://wx.qlogo.cn/mmhead/abc/0").startswith("/img?url=")


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ({"type": "photo", "url": "https://pbs.twimg.com/media/a.jpg"}, "https://pbs.twimg.com/media/a.jpg"),
        ({"type": "video", "preview_image_url": "https://pbs.twimg.com/x.jpg"}, "https://pbs.twimg.com/x.jpg"),
        ({"type": "photo", "url": "https://evil.test/a.jpg"}, None),
        ({"type": "photo", "url": "https://pbs.twimg.com.evil.test/a.jpg"}, None),
        ({"type": "photo", "url": "http://pbs.twimg.com/a.jpg"}, None),
    ],
)
def test_x_media_url_must_be_on_x_cdn(entry: dict[str, Any], expected: str | None) -> None:
    assert _media_still_url(entry) == expected


def test_source_url_expands_only_a_whole_url_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-secret")
    monkeypatch.setenv("MY_FEED_URL", "https://feed.test/rss")
    assert _expand_url_placeholder("${MY_FEED_URL}") == "https://feed.test/rss"
    # Embedded references are left verbatim (the loader then refuses them).
    assert _expand_url_placeholder("https://a.test/?k=${DEEPSEEK_API_KEY}") == "https://a.test/?k=${DEEPSEEK_API_KEY}"
    assert _expand_url_placeholder("${DEEPSEEK_API_KEY}x") == "${DEEPSEEK_API_KEY}x"


def test_source_loader_refuses_undeclared_env_reference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from airadar.sources.loader import load_sources

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-secret")
    toml = tmp_path / "sources.toml"
    toml.write_text(
        '[[source]]\nslug = "leaky"\nname = "Leaky"\nurl = "https://a.test/?k=${DEEPSEEK_API_KEY}"\ntier = "T1"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="embeds an env reference"):
        load_sources(toml)


def test_credentials_scrub_is_linear_on_colon_runs() -> None:
    import time

    started = time.perf_counter()
    _scrub("//" + ":" * 65536)
    assert time.perf_counter() - started < 0.5


def test_wechat_detail_url_percent_encodes_slug() -> None:
    assert _detail_url("a/b?c#d") == "/wechat/a%2Fb%3Fc%23d"
