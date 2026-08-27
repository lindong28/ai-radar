from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ..egress import playwright_launch_proxy
from .protocol import DEFAULT_SESSION_PATH

DEFAULT_BROWSER_PROFILE = Path(__file__).resolve().parents[3] / "data" / "wechat-discovery-browser"
LOGIN_URL = "https://mp.weixin.qq.com/"
_ADMIN_URL = re.compile(r"^https://mp\.weixin\.qq\.com/.*(?:[?&])token=[^&]+")
_BROWSER_ENV_NAMES = frozenset(
    {
        "HOME",
        "PATH",
        "TMPDIR",
        "TEMP",
        "TMP",
        "LANG",
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XAUTHORITY",
        "DBUS_SESSION_BUS_ADDRESS",
        "__CF_USER_TEXT_ENCODING",
    }
)


class DiscoveryLoginError(RuntimeError):
    pass


def safe_browser_env(source: Mapping[str, str]) -> dict[str, str | float | bool]:
    return {
        key: value
        for key, value in source.items()
        if key in _BROWSER_ENV_NAMES or key.startswith("LC_")
    }


def extract_admin_token(url: str) -> str | None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "mp.weixin.qq.com":
        return None
    values = parse_qs(parsed.query).get("token", [])
    token = str(values[0]).strip() if values else ""
    return token or None


def save_credentials(
    path: str | Path,
    *,
    token: str,
    cookies: Sequence[Mapping[str, object]],
) -> None:
    session_path = Path(path)
    session_path.parent.mkdir(parents=True, exist_ok=True)
    filtered = [
        dict(cookie)
        for cookie in cookies
        if (
            str(cookie.get("domain", "")).lstrip(".").lower() == "weixin.qq.com"
            or str(cookie.get("domain", "")).lstrip(".").lower().endswith(".weixin.qq.com")
        )
        and str(cookie.get("name", "")).strip()
        and str(cookie.get("value", "")).strip()
    ]
    if not token.strip() or not filtered:
        raise DiscoveryLoginError("login completed without a usable admin token and WeChat cookies")
    payload = {"version": 1, "token": token.strip(), "cookies": filtered}
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=session_path.parent,
            prefix=".wechat-discovery-session.",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            os.chmod(temp_path, 0o600)
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, session_path)
        os.chmod(session_path, 0o600)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def capture_login(
    *,
    session_path: str | Path = DEFAULT_SESSION_PATH,
    browser_profile: str | Path = DEFAULT_BROWSER_PROFILE,
    timeout_seconds: int = 300,
) -> None:
    if not 30 <= timeout_seconds <= 900:
        raise ValueError("WeChat discovery login timeout must be between 30 and 900 seconds")
    profile_path = Path(browser_profile)
    profile_path.mkdir(parents=True, exist_ok=True)
    os.chmod(profile_path, 0o700)

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise DiscoveryLoginError("Playwright Chromium is unavailable for WeChat discovery login") from exc

    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                profile_path,
                headless=False,
                env=safe_browser_env(os.environ),
                no_viewport=True,
                proxy=playwright_launch_proxy(
                    LOGIN_URL,
                    callsite_id="wechat_discovery.login.browser",
                ),
            )
            saved = False
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45_000)
                if extract_admin_token(page.url) is None:
                    page.wait_for_url(_ADMIN_URL, timeout=timeout_seconds * 1000)
                token = extract_admin_token(page.url)
                if token is None:
                    raise DiscoveryLoginError("login finished without reaching the WeChat admin home")
                cookies = context.cookies([LOGIN_URL])
                save_credentials(session_path, token=token, cookies=cookies)
                saved = True
            finally:
                try:
                    context.close()
                except Exception:
                    if not saved:
                        raise
    except PlaywrightTimeoutError as exc:
        raise DiscoveryLoginError("timed out before WeChat admin login completed") from exc
    except DiscoveryLoginError:
        raise
    except Exception as exc:
        raise DiscoveryLoginError("WeChat discovery login browser failed") from exc
