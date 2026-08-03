from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import time
import tomllib
import urllib.request
from collections.abc import Generator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, sync_playwright

from airadar import db
from airadar.db import resolve_db_path

AI_RADAR_ROOT = Path(__file__).resolve().parents[2]
EXTERNAL_BASE_URL_ENV = "AI_RADAR_PLAYWRIGHT_BASE_URL"


def _external_base_url() -> str | None:
    if EXTERNAL_BASE_URL_ENV not in os.environ:
        return None
    base_url = os.environ[EXTERNAL_BASE_URL_ENV].strip().rstrip("/")
    if not base_url:
        raise ValueError(f"{EXTERNAL_BASE_URL_ENV} must be a non-empty URL when set")
    return base_url


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_health(base_url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.time() + 30
    last_error: Exception | None = None
    while time.time() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            raise RuntimeError(f"server exited early\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}")
        try:
            with urllib.request.urlopen(f"{base_url}/api/v1/healthz", timeout=1) as response:
                if response.status == 200:
                    return
        except Exception as exc:  # pragma: no cover - diagnostic path
            last_error = exc
        time.sleep(0.25)
    raise TimeoutError(f"server did not become healthy: {last_error!r}")


def _prepare_session_db(source: Path, destination: Path) -> None:
    source = source.resolve()
    destination = destination.resolve()
    if source == destination:
        raise ValueError("Playwright session DB must not be the source database")
    if not source.is_file():
        raise FileNotFoundError(f"Playwright source database does not exist: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True) as source_connection:
        with sqlite3.connect(destination) as destination_connection:
            source_connection.backup(destination_connection)
    db.migrate(destination)


def _serve_environment(session_db: Path) -> dict[str, str]:
    return {
        **os.environ,
        "AI_RADAR_DB": str(session_db.resolve()),
        "TZ": "Asia/Shanghai",
    }


@pytest.fixture(scope="session")
def playwright_db_path(tmp_path_factory: pytest.TempPathFactory) -> Path | None:
    if _external_base_url() is not None:
        return None
    session_db = tmp_path_factory.mktemp("playwright") / "radar.db"
    _prepare_session_db(resolve_db_path(), session_db)
    return session_db


@pytest.fixture(scope="session")
def base_url(playwright_db_path: Path | None) -> Generator[str, None, None]:
    external_base_url = _external_base_url()
    if external_base_url is not None:
        yield external_base_url
        return

    assert playwright_db_path is not None
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [
            str(AI_RADAR_ROOT / "run.sh"),
            "serve",
            "--pre-migrated-db",
            "--port",
            str(port),
        ],
        cwd=AI_RADAR_ROOT,
        env=_serve_environment(playwright_db_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_health(url, process)
        yield url
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.fixture(scope="session")
def browser() -> Generator[Browser, None, None]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture()
def page(browser: Browser) -> Generator[Page, None, None]:
    context = browser.new_context(viewport={"width": 1366, "height": 900})
    page = context.new_page()
    yield page
    context.close()


@pytest.fixture(scope="session")
def source_homepages() -> dict[str, str]:
    data = tomllib.loads((AI_RADAR_ROOT / "data" / "sources.toml").read_text(encoding="utf-8"))
    return {source["slug"]: source["homepage_url"] for source in data["source"] if source.get("homepage_url")}


@pytest.fixture(scope="session")
def historical_date(playwright_db_path: Path | None, base_url: str) -> str:
    if playwright_db_path is None:
        # 日报的「最近一期」以归档端点为准（它已排除未来 published_at），
        # 不用 /api/v1/curated 的 date——那是列表最新条目日期，语义不同且可能是未来。
        with urllib.request.urlopen(f"{base_url}/api/v1/curated/daily-archive", timeout=10) as response:
            payload = json.load(response)
        days = payload.get("data", {}).get("days") or []
        assert days, "daily-archive returned no days"
        return str(days[0]["date"])

    with sqlite3.connect(playwright_db_path) as conn:
        row = conn.execute(
            """
            SELECT date(datetime(i.published_at, '+08:00')) AS day, COUNT(*) AS count
            FROM curated_items c
            JOIN curation_runs r ON r.id = c.run_id
            JOIN items i ON i.id = c.item_id
            WHERE r.id = (
                SELECT id
                FROM curation_runs
                ORDER BY created_at DESC
                LIMIT 1
            )
            GROUP BY day
            HAVING count > 0 AND day <= date(datetime('now', '+08:00'))
            ORDER BY day DESC
            LIMIT 1
            """
        ).fetchone()
    assert row is not None
    return str(row[0])
