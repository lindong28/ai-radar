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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, sync_playwright

from airadar import db
from airadar.sources.loader import SourceConfig, load_sources
from airadar.sources.sync import sync_to_db

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


def _fixture_timestamp(base: datetime, hours_ago: int) -> str:
    return (base - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")


def _build_deterministic_session_db(destination: Path) -> None:
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    db.migrate(destination)

    sources = [
        source
        for source in load_sources(AI_RADAR_ROOT / "data" / "sources.toml")
        if source.slug != "wx_mp2rss"
    ]
    sources.append(
        SourceConfig(
            slug="wx_mp2rss",
            name="微信文章解读测试源",
            url="https://fixture.invalid/mp2rss.xml",
            tier="T2",
            kind="wechat",
            homepage_url="https://mp.weixin.qq.com/",
            optional=True,
            required_env="MP2RSS_FEED_URL",
            wechat_only=True,
            public_url_override="https://mp.weixin.qq.com/",
        )
    )

    base = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=5)
    main_item_ids = [f"playwright-main-{index:03d}" for index in range(120)]
    category_tags = (
        ("模型发布", "论文/研究"),
        ("产品更新", "MCP/工具"),
        ("行业动态", "现象/趋势"),
        ("论文/研究", "安全/对齐"),
        ("教程/实践", "部署/工程"),
    )
    reason = json.dumps(
        {
            "scores": {
                "relevance": 8.0,
                "density": 8.0,
                "recency": 8.0,
                "authority": 8.0,
                "engineering": 8.0,
                "reasoning": "该条目提供可核对的一手 AI 进展，适合先读摘要再决定是否查看原文。",
            }
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    scoring = json.dumps(
        {
            "relevance": 8.0,
            "density": 8.0,
            "recency": 8.0,
            "authority": 8.0,
            "engineering": 8.0,
        },
        separators=(",", ":"),
    )

    with sqlite3.connect(destination) as connection:
        sync_to_db(sources, connection)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executemany(
            "UPDATE sources SET icon_url='http://127.0.0.1:9/playwright-icon.png' WHERE id=?",
            [(source_id,) for source_id in ("openai_blog", "x_openai", "anthropic_news", "x_anthropicai")],
        )
        connection.executemany(
            """
            INSERT INTO items (
              id, source_id, url, title, author, published_at, fetched_at,
              content_text, content_html, content_hash, extra_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}')
            """,
            [
                (
                    item_id,
                    (
                        "openai_blog"
                        if index < 80 and index % 2 == 0
                        else "x_openai"
                        if index < 80
                        else "anthropic_news"
                        if index % 2 == 0
                        else "x_anthropicai"
                    ),
                    (
                        f"https://openai.com/index/playwright-fixture-{index:03d}"
                        if index < 80 and index % 2 == 0
                        else f"https://x.com/OpenAI/status/{1900000000000000000 + index}"
                        if index < 80
                        else f"https://www.anthropic.com/news/playwright-fixture-{index:03d}"
                        if index % 2 == 0
                        else f"https://x.com/AnthropicAI/status/{1900000000000000000 + index}"
                    ),
                    (
                        f"OpenAI Playwright 数据契约条目 {index:03d}"
                        if index < 80
                        else f"Anthropic Playwright 数据契约条目 {index:03d}"
                    ),
                    (
                        ("OpenAI" if index % 2 else "OpenAI Editorial")
                        if index < 80
                        else ("AnthropicAI" if index % 2 else "Anthropic Editorial")
                    ),
                    _fixture_timestamp(base, index),
                    _fixture_timestamp(base, index),
                    (
                        f"{'OpenAI' if index < 80 else 'Anthropic'} Playwright fixture provides a deterministic, sufficiently long body "
                        f"for browser reading-surface checks and search pagination item {index:03d}."
                    ),
                    (
                        '<p>Fixture media</p><img src="http://127.0.0.1:9/playwright-media.png">'
                        if index in {0, 30}
                        else None
                    ),
                    f"playwright-main-hash-{index:03d}",
                )
                for index, item_id in enumerate(main_item_ids)
            ],
        )
        for index, item_id in enumerate(main_item_ids):
            tags = category_tags[index % len(category_tags)]
            enrichment = json.dumps(
                {
                    "title_zh": (
                        f"OpenAI Playwright 数据契约条目 {index:03d}"
                        if index < 80
                        else f"Anthropic Playwright 数据契约条目 {index:03d}"
                    ),
                    "summary_zh": "这是确定性浏览器夹具的中文摘要，用于验证列表、筛选、日报与翻页的真实消费链路，并确保正文阅读面具有足够长度。",
                    "why_recommend": "它覆盖一手来源、标签与时间顺序，可用于核对用户实际看到的数据关系。",
                    "tags": list(tags),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            evaluated_at = _fixture_timestamp(base, index)
            connection.execute(
                """
                INSERT INTO item_evaluations (
                  item_id, stage, ruleset_version, model_id, input_json, output_json,
                  numeric_json, latency_ms, cost_usd, evaluated_at, error
                ) VALUES (?, 'enrich', 'playwright.r1', 'fixture', '{}', ?, NULL, 1, NULL, ?, NULL)
                """,
                (item_id, enrichment, evaluated_at),
            )
            connection.execute(
                """
                INSERT INTO item_evaluations (
                  item_id, stage, ruleset_version, model_id, input_json, output_json,
                  numeric_json, latency_ms, cost_usd, evaluated_at, error
                ) VALUES (?, 'scoring', 'playwright.r1', 'fixture', '{}', NULL, ?, 1, NULL, ?, NULL)
                """,
                (item_id, scoring, evaluated_at),
            )

        curated_ids = main_item_ids[30:120]
        connection.execute(
            """
            INSERT INTO curation_runs (
              id, ruleset_version, weights_json, threshold,
              input_eval_ids, output_curated_ids, created_at
            ) VALUES ('playwright-curation', 'playwright.r1', '{}', 0, '[]', ?, ?)
            """,
            (
                json.dumps(curated_ids, separators=(",", ":")),
                _fixture_timestamp(base, 0),
            ),
        )
        connection.executemany(
            """
            INSERT INTO curated_items (
              run_id, item_id, weighted_score, rank, reason_json, summary_json
            ) VALUES ('playwright-curation', ?, 8.0, ?, ?, NULL)
            """,
            [(item_id, rank, reason) for rank, item_id in enumerate(curated_ids, start=1)],
        )

        wechat_rows = []
        interpretation_rows = []
        for index in range(75):
            item_id = f"playwright-wechat-{index:03d}"
            published_at = _fixture_timestamp(base, index + 1)
            wechat_rows.append(
                (
                    item_id,
                    f"https://mp.weixin.qq.com/s/playwright-{index:03d}",
                    f"微信文章解读测试条目 {index:03d}",
                    "AI Planet",
                    published_at,
                    published_at,
                    f"微信原文测试正文 {index:03d}",
                    f"playwright-wechat-hash-{index:03d}",
                )
            )
            interpretation_rows.append(
                (
                    item_id,
                    f"playwright-wechat-{index:03d}",
                    "值得一看",
                    "稳定夹具中的已保存解读",
                    "这是一段确定性的微信文章摘要，用于验证搜索、分页和详情返回链路。",
                    '["智能体","产品更新"]',
                    "### 核心观点\n\n这是只用于浏览器测试的确定性解读正文。",
                    published_at,
                )
            )
        connection.executemany(
            """
            INSERT INTO items (
              id, source_id, url, title, author, published_at, fetched_at,
              content_text, content_hash, extra_json
            ) VALUES (?, 'wx_mp2rss', ?, ?, ?, ?, ?, ?, ?, '{}')
            """,
            wechat_rows,
        )
        connection.executemany(
            """
            INSERT INTO wechat_interpretations (
              item_id, slug, recommendation, save_decision, save_reason, abstract,
              tags_json, summary_md, model, kb_synced, processed_at, error
            ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, 'fixture', 0, ?, NULL)
            """,
            interpretation_rows,
        )
        connection.commit()

        expected = {
            "feed_items": 60,
            "x_items": 60,
            "wechat_items": 75,
            "curated_items": 90,
        }
        actual = {
            "feed_items": int(
                connection.execute(
                    "SELECT COUNT(*) FROM items WHERE source_id IN ('openai_blog','anthropic_news')"
                ).fetchone()[0]
            ),
            "x_items": int(
                connection.execute(
                    "SELECT COUNT(*) FROM items WHERE source_id IN ('x_openai','x_anthropicai')"
                ).fetchone()[0]
            ),
            "wechat_items": int(
                connection.execute(
                    "SELECT COUNT(*) FROM wechat_interpretations WHERE save_decision=1"
                ).fetchone()[0]
            ),
            "curated_items": int(connection.execute("SELECT COUNT(*) FROM curated_items").fetchone()[0]),
        }
        if actual != expected:
            raise AssertionError(f"deterministic Playwright fixture mismatch: {actual!r}")


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
    _build_deterministic_session_db(session_db)
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


def _page_context_options() -> dict[str, Any]:
    return {
        "viewport": {"width": 1366, "height": 900},
        "color_scheme": "light",
    }


@pytest.fixture()
def page(browser: Browser) -> Generator[Page, None, None]:
    context = browser.new_context(**_page_context_options())
    context.add_init_script(
        "if (!localStorage.getItem('ai-radar:theme')) localStorage.setItem('ai-radar:theme', 'light')"
    )
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
