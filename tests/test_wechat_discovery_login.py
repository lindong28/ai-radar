from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from airadar.wechat_discovery.login import (
    extract_admin_token,
    safe_browser_env,
    save_credentials,
)
from airadar.wechat_discovery.protocol import load_credentials


def test_safe_browser_env_does_not_inherit_unrelated_dotenv_secrets() -> None:
    source = {
        "PATH": "/usr/bin",
        "TMPDIR": "/tmp/task",
        "LANG": "zh_CN.UTF-8",
        "DISPLAY": ":0",
        "DEEPSEEK_API_KEY": "secret-a",
        "FEISHU_GENERAL_ALERT_WEBHOOK": "secret-b",
        "MP2RSS_FEED_URL": "secret-c",
    }

    result = safe_browser_env(source)

    assert result == {
        "PATH": "/usr/bin",
        "TMPDIR": "/tmp/task",
        "LANG": "zh_CN.UTF-8",
        "DISPLAY": ":0",
    }


def test_extract_admin_token_requires_wechat_admin_origin() -> None:
    assert extract_admin_token("https://mp.weixin.qq.com/cgi-bin/home?t=home/index&token=12345") == "12345"
    assert extract_admin_token("https://example.com/?token=12345") is None
    assert extract_admin_token("https://mp.weixin.qq.com/") is None


def test_save_credentials_is_atomic_private_and_loadable(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    save_credentials(
        path,
        token="12345",
        cookies=[
            {
                "name": "session",
                "value": "fixture-cookie",
                "domain": ".weixin.qq.com",
                "path": "/",
                "expires": -1,
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            },
            {"name": "unrelated", "value": "discard", "domain": ".example.com"},
        ],
    )

    assert path.stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.glob(".wechat-discovery-session.*")) == []
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == 1
    assert [cookie["name"] for cookie in raw["cookies"]] == ["session"]
    credentials = load_credentials(path)
    assert credentials.token == "12345"
    assert credentials.cookie_header == "session=fixture-cookie"


def test_capture_login_does_not_report_failure_when_browser_close_fails_after_save(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    from airadar.wechat_discovery.login import capture_login

    session_path = tmp_path / "session.json"

    class FakePage:
        url = "https://mp.weixin.qq.com/cgi-bin/home?t=home/index&token=12345"

        def goto(self, *_args: object, **_kwargs: object) -> None:
            return None

    class FakeContext:
        pages = [FakePage()]

        def cookies(self, _urls):  # noqa: ANN001
            return [
                {
                    "name": "session",
                    "value": "fixture-cookie",
                    "domain": ".weixin.qq.com",
                    "path": "/",
                }
            ]

        def close(self) -> None:
            raise RuntimeError("close failed")

    context = FakeContext()
    launch_kwargs: dict[str, object] = {}

    def launch_context(*_args: object, **kwargs: object):  # noqa: ANN202
        launch_kwargs.update(kwargs)
        return context

    chromium = SimpleNamespace(launch_persistent_context=launch_context)
    playwright = SimpleNamespace(chromium=chromium)

    class FakeManager:
        def __enter__(self):  # noqa: ANN204
            return playwright

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: FakeManager())

    capture_login(
        session_path=session_path,
        browser_profile=tmp_path / "browser",
        timeout_seconds=30,
    )

    assert load_credentials(session_path).token == "12345"
    assert launch_kwargs["args"] == ["--no-proxy-server"]
