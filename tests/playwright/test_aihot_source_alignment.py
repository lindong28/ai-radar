from __future__ import annotations

import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest
from playwright.sync_api import APIResponse, Browser, Page, expect

from airadar.db import migrate
from airadar.sources.loader import load_sources
from airadar.sources.sync import sync_to_db

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "tests" / "fixtures" / "aihot_sources.json"
REPRESENTATIVES = {
    "openai_blog": ("FeedAlignmentMarker", "https://openai.com/index/alignment-fixture"),
    "anthropic_news": ("WebHtmlAlignmentMarker", "https://www.anthropic.com/news/alignment-fixture"),
    "inclusionai_models": ("WebJsonAlignmentMarker", "https://huggingface.co/inclusionAI/alignment-fixture"),
    "deepseek_api_updates": (
        "DeepSeekUpdatesAlignmentMarker",
        "https://api-docs.deepseek.com/zh-cn/updates#%E6%97%B6%E9%97%B4-2026-08-13",
    ),
    "x_openai": ("XAlignmentMarker", "https://x.com/OpenAI/status/1000000000000000000"),
}
REMOVED_SLUGS = (
    "lilianweng",
    "sebastianraschka",
    "latent_space",
    "importai",
    "hn_ai",
    "lobsters_ai",
    "the_batch",
    "last_week_ai",
    "simonw_mastodon",
)
LEGACY_WECHAT_SLUG = "wx_legacy"
LEGACY_WECHAT_MARKER = "LegacyWechatAlignmentMarker"
ALIGNMENT_BATCH_ID = uuid.uuid4().hex
ALIGNMENT_CODE_PATHS = (
    "src/airadar/sources/sync.py",
    "src/airadar/web/app.py",
    "src/airadar/web/routes/curated.py",
    "src/airadar/web/routes/curated_archive.py",
    "src/airadar/web/routes/curated_digest.py",
    "src/airadar/web/routes/sources.py",
    "src/airadar/web/routes/timeline.py",
    "src/airadar/web/routes/wechat.py",
    "web/static/app.js",
    "web/templates/about.html",
    "tests/playwright/test_aihot_source_alignment.py",
)
TIER_LABELS = {"T1": "核心", "T1.5": "重点", "T2": "扩展"}
KIND_LABELS = {"feed": "订阅源", "web": "网页列表", "x": "X 账号", "wechat": "微信专用"}
PRECOMPUTED_RUN_ID = "alignment-01-precomputed"
LATEST_RUN_ID = "alignment-02-latest"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_health(base_url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.time() + 30
    while time.time() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            raise RuntimeError(f"server exited early\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}")
        try:
            with httpx.Client(trust_env=False, timeout=1) as client:
                response = client.get(f"{base_url}/api/v1/healthz")
                if response.status_code == 200:
                    return
        except Exception:
            time.sleep(0.2)
    process.terminate()
    stdout, stderr = process.communicate(timeout=10)
    raise TimeoutError(f"AI Radar server did not become healthy\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}")


WECHAT_FEED_ENVS = ("MP2RSS_FEED_URL", "WECHAT2RSS_FEED_URL")
MAIN_SOURCE_COUNT = 161


def _expected_source_count(feed_urls: dict[str, str] | None) -> int:
    return MAIN_SOURCE_COUNT + (len(WECHAT_FEED_ENVS) if feed_urls else 0)


class _Mp2RSSHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            body = b"ok"
            content_type = "text/plain"
        elif self.path in {"/paid-secret-feed", "/self-hosted-feed"}:
            body = b'<?xml version="1.0"?><rss version="2.0"><channel><title>fixture</title></channel></rss>'
            content_type = "application/rss+xml"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@contextmanager
def _mp2rss_fixture(enabled: bool) -> Iterator[dict[str, str] | None]:
    """Stand in for every optional WeChat feed, keyed by the env var it needs.

    Each such feed URL embeds a subscription token, so the assertions below
    check that none of them reaches a public surface; running more than one
    keeps that check honest as feeds are added.
    """
    if not enabled:
        yield None
        return
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Mp2RSSHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    with httpx.Client(trust_env=False, timeout=2) as client:
        assert client.get(f"{base}/healthz").status_code == 200
    try:
        yield {
            "MP2RSS_FEED_URL": f"{base}/paid-secret-feed",
            "WECHAT2RSS_FEED_URL": f"{base}/self-hosted-feed",
        }
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _insert_item(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    source_id: str,
    title: str,
    url: str,
) -> None:
    conn.execute(
        """
        INSERT INTO items (
          id, source_id, url, title, author, published_at, fetched_at,
          content_text, content_html, content_hash, extra_json
        ) VALUES (?, ?, ?, ?, 'fixture', '2026-08-13T08:00:00Z',
                  '2026-08-13T08:01:00Z', ?, NULL, ?, '{}')
        """,
        (item_id, source_id, url, title, title, f"hash-{item_id}"),
    )


def _insert_source(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    name: str,
    url: str,
    tier: str,
    kind: str,
    homepage_url: str,
    icon_url: str | None = None,
    meta: dict[str, object] | None = None,
    public_url_override: str | None = None,
    optional: bool = False,
    required_env: str | None = None,
    wechat_only: bool = False,
) -> None:
    conn.execute(
        """
        INSERT INTO sources (
          id, name, url, tier, enabled, kind, homepage_url, icon_url,
          meta_json, synced_at, public_url_override, optional, required_env, wechat_only
        ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, '2026-08-12T00:00:00Z', ?, ?, ?, ?)
        """,
        (
            source_id,
            name,
            url,
            tier,
            kind,
            homepage_url,
            icon_url,
            json.dumps(meta or {}, ensure_ascii=False, sort_keys=True),
            public_url_override,
            int(optional),
            required_env,
            int(wechat_only),
        ),
    )


def _insert_config_source(conn: sqlite3.Connection, source: object) -> None:
    meta = dict(source.meta)
    if source.kind == "x" and meta.get("adapter") == "x_api":
        meta.update(
            x_state_schema_version=1,
            x_cursor_state="identity_pending",
            x_reference_status="pending",
        )
    _insert_source(
        conn,
        source_id=source.slug,
        name=source.name,
        url=source.url,
        tier=source.tier,
        kind=source.kind,
        homepage_url=source.homepage_url,
        icon_url=source.icon_url,
        meta=meta,
        public_url_override=source.public_url_override,
        optional=source.optional,
        required_env=source.required_env,
        wechat_only=source.wechat_only,
    )


def _precomputed_summary(
    *,
    item_id: str,
    source_id: str,
    source_name: str,
    source_kind: str,
    tier: str,
    title: str,
    url: str,
) -> str:
    return json.dumps(
        {
            "id": item_id,
            "source_id": source_id,
            "source_name": source_name,
            "source_kind": source_kind,
            "tier": tier,
            "url": url,
            "title": title,
            "title_zh": title,
            "author": "fixture",
            "published_at": "2026-08-13T08:00:00Z",
            "fetched_at": "2026-08-13T08:01:00Z",
            "content_preview": title,
            "summary_zh": None,
            "why_recommend": None,
            "enriched_tags": [],
            "topic_tags": [],
            "reasoning": None,
            "related_discussions": [],
            "media_assets": [],
            "weighted_score": 1,
            "rank": 1,
            "reason": {},
            "scores": {},
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _insert_curated_relations(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    source_id: str,
    source_name: str,
    source_kind: str,
    tier: str,
    title: str,
    url: str,
    rank: int,
) -> None:
    conn.execute(
        """
        INSERT INTO curated_items (
          run_id, item_id, weighted_score, rank, reason_json, summary_json
        ) VALUES (?, ?, 1, ?, '{}', ?)
        """,
        (
            PRECOMPUTED_RUN_ID,
            item_id,
            rank,
            _precomputed_summary(
                item_id=item_id,
                source_id=source_id,
                source_name=source_name,
                source_kind=source_kind,
                tier=tier,
                title=title,
                url=url,
            ),
        ),
    )
    conn.execute(
        """
        INSERT INTO curated_items (
          run_id, item_id, weighted_score, rank, reason_json, summary_json
        ) VALUES (?, ?, 1, ?, '{}', NULL)
        """,
        (LATEST_RUN_ID, item_id, rank),
    )


def _prepare_upgrade_db(
    db_path: Path,
    *,
    feed_urls: dict[str, str] | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for env_name in WECHAT_FEED_ENVS:
        if feed_urls is None:
            monkeypatch.delenv(env_name, raising=False)
        else:
            monkeypatch.setenv(env_name, feed_urls[env_name])
    migrate(db_path)
    current_sources = load_sources(ROOT / "data" / "sources.toml")
    assert len(current_sources) == _expected_source_count(feed_urls)
    current_by_slug = {source.slug: source for source in current_sources}
    with sqlite3.connect(db_path) as conn:
        database_file = Path(conn.execute("PRAGMA database_list").fetchone()[2]).resolve()
        assert database_file == db_path.resolve()
        conn.execute(
            """
            INSERT INTO curation_runs (
              id, ruleset_version, weights_json, threshold,
              input_eval_ids, output_curated_ids, created_at
            ) VALUES (?, 'fixture', '{}', 0, '[]', '[]', ?)
            """,
            (PRECOMPUTED_RUN_ID, "2026-08-13T08:02:00Z"),
        )
        conn.execute(
            """
            INSERT INTO curation_runs (
              id, ruleset_version, weights_json, threshold,
              input_eval_ids, output_curated_ids, created_at
            ) VALUES (?, 'fixture', '{}', 0, '[]', '[]', ?)
            """,
            (LATEST_RUN_ID, "2026-08-13T08:03:00Z"),
        )
        for rank, source_id in enumerate(REMOVED_SLUGS, start=1):
            kind = "x" if source_id == "simonw_mastodon" else "feed"
            _insert_source(
                conn,
                source_id=source_id,
                name=f"Legacy removed source {source_id}",
                url=f"https://legacy.example.test/{source_id}/feed",
                tier="T1.5",
                kind=kind,
                homepage_url=f"https://legacy.example.test/{source_id}/",
            )
            item_id = f"removed-{source_id}"
            title = f"RemovedSourceMarker {source_id}"
            url = f"https://legacy.example.test/{source_id}/article"
            _insert_item(conn, item_id=item_id, source_id=source_id, title=title, url=url)
            _insert_curated_relations(
                conn,
                item_id=item_id,
                source_id=source_id,
                source_name=f"Legacy removed source {source_id}",
                source_kind=kind,
                tier="T1.5",
                title=title,
                url=url,
                rank=rank,
            )

        _insert_source(
            conn,
            source_id=LEGACY_WECHAT_SLUG,
            name="Legacy WeChat source",
            url="https://legacy.example.test/wechat/feed",
            tier="T2",
            kind="wechat",
            homepage_url="https://mp.weixin.qq.com/",
            wechat_only=True,
        )
        _insert_item(
            conn,
            item_id="alignment-legacy-wechat",
            source_id=LEGACY_WECHAT_SLUG,
            title=LEGACY_WECHAT_MARKER,
            url="https://mp.weixin.qq.com/s/alignment-legacy-fixture",
        )
        for rank, (source_id, (title, url)) in enumerate(REPRESENTATIVES.items(), start=1):
            source = current_by_slug[source_id]
            _insert_config_source(conn, source)
            item_id = f"alignment-{rank}"
            _insert_item(conn, item_id=item_id, source_id=source_id, title=title, url=url)
            _insert_curated_relations(
                conn,
                item_id=item_id,
                source_id=source_id,
                source_name=source.name,
                source_kind=source.kind,
                tier=source.tier,
                title=title,
                url=url,
                rank=len(REMOVED_SLUGS) + rank,
            )
        if feed_urls:
            wx_source = current_by_slug["wx_mp2rss"]
            _insert_config_source(conn, wx_source)
            _insert_item(
                conn,
                item_id="alignment-wechat",
                source_id="wx_mp2rss",
                title="WechatAlignmentMarker",
                url="https://mp.weixin.qq.com/s/alignment-fixture",
            )
            conn.execute(
                """
                INSERT INTO wechat_interpretations (
                  item_id, slug, recommendation, save_decision, save_reason,
                  abstract, tags_json, summary_md, model, kb_synced, processed_at, error
                ) VALUES (
                  'alignment-wechat', 'alignment-wechat', 'read', 1, 'fixture',
                  'fixture abstract', '[]', 'fixture summary', 'fixture', 0,
                  '2026-08-13T08:03:00Z', NULL
                )
                """
            )
        conn.execute(
            """
            INSERT INTO wechat_interpretations (
              item_id, slug, recommendation, save_decision, save_reason,
              abstract, tags_json, summary_md, model, kb_synced, processed_at, error
            ) VALUES (
              'alignment-legacy-wechat', 'alignment-legacy-wechat', 'read', 1, 'fixture',
              'legacy fixture abstract', '[]', 'legacy fixture summary', 'fixture', 0,
              '2026-08-13T08:04:00Z', NULL
            )
            """
        )
        conn.commit()


def _sync_current_sources(db_path: Path, *, feed_urls: dict[str, str] | None) -> None:
    sources = load_sources(ROOT / "data" / "sources.toml")
    assert len(sources) == _expected_source_count(feed_urls)
    with sqlite3.connect(db_path) as conn:
        sync_to_db(sources, conn)


@contextmanager
def _serve(db_path: Path) -> Iterator[str]:
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "airadar.web.app:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--lifespan",
            "off",
        ],
        cwd=ROOT,
        env={
            "AI_RADAR_DB": str(db_path.resolve()),
            **{name: os.environ.get(name, "") for name in WECHAT_FEED_ENVS},
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(ROOT / "src"),
            "TZ": "Asia/Shanghai",
        },
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_health(base_url, process)
        yield base_url
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def _assert_main_page(page: Page, base_url: str, path: str) -> None:
    page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
    for source_id, (title, url) in REPRESENTATIVES.items():
        card = page.locator(f'.timeline-card[data-source-id="{source_id}"]')
        expect(card).to_have_count(1)
        expected_name = next(
            source["name"]
            for source in json.loads(CONTRACT.read_text(encoding="utf-8"))["sources"]
            if source["slug"] == source_id
        )
        expect(card.locator(".source-name")).to_have_text(expected_name)
        expect(card.locator(".item-title")).to_have_attribute("href", url)
        expect(card.locator(".item-title")).to_have_text(title)
    expect(page.get_by_text("WechatAlignmentMarker", exact=True)).to_have_count(0)


def _assert_about_contract_rows(page: Page, expected_rows: list[dict[str, object]]) -> None:
    rendered_rows = page.locator("#sources-table tr").evaluate_all(
        """
        rows => rows.map(row => {
          const cells = Array.from(row.querySelectorAll("td"));
          const link = cells[1]?.querySelector("a");
          const note = cells[1]?.querySelector(".source-scope-note");
          return {
            slug: cells[0]?.textContent.trim(),
            name: link?.textContent.trim(),
            tier: cells[2]?.textContent.trim(),
            configuration_status: cells[3]?.textContent.trim(),
            kind: cells[5]?.textContent.trim(),
            public_landing_url: link?.getAttribute("href"),
            scope_note: note?.textContent.trim() || null,
          };
        })
        """
    )
    assert len(rendered_rows) == len(expected_rows)
    expected_by_slug = {str(row["slug"]): row for row in expected_rows}
    assert len(expected_by_slug) == len(expected_rows)
    assert {row["slug"] for row in rendered_rows} == set(expected_by_slug)
    for actual in rendered_rows:
        expected = expected_by_slug[actual["slug"]]
        assert actual == {
            "slug": expected["slug"],
            "name": expected["name"],
            "tier": f'{TIER_LABELS[str(expected["tier"])]}（{expected["tier"]}）',
            "configuration_status": "已启用",
            "kind": KIND_LABELS[str(expected["kind"])],
            "public_landing_url": expected.get("public_url_override", expected["homepage_url"]),
            "scope_note": (
                "仅用于微信文章解读，不属于主时间线" if expected["kind"] == "wechat" else None
            ),
        }


def _api_data(page: Page, url: str) -> tuple[APIResponse, dict[str, Any]]:
    response = page.request.get(url)
    assert response.ok, (url, response.status, response.text())
    return response, response.json()["data"]


def _item_ids(data: dict[str, Any]) -> set[str]:
    return {str(item["id"]) for item in data["items"]}


def _assert_upgrade_fixtures_reach_public_consumers(
    page: Page,
    base_url: str,
    evidence_dir: Path,
) -> None:
    sources_response = page.request.get(f"{base_url}/api/v2/sources")
    assert sources_response.ok
    source_ids = {row["id"] for row in sources_response.json()["data"]["sources"]}
    assert set(REMOVED_SLUGS) | {LEGACY_WECHAT_SLUG} <= source_ids
    (evidence_dir / "pre-sync-sources.json").write_text(
        sources_response.text() + "\n",
        encoding="utf-8",
    )

    removed_item_ids = {f"removed-{slug}" for slug in REMOVED_SLUGS}
    default_response, default_data = _api_data(page, f"{base_url}/api/v1/curated?limit=100")
    explicit_response, explicit_data = _api_data(
        page,
        f"{base_url}/api/v1/curated?run_id={PRECOMPUTED_RUN_ID}&limit=100",
    )
    timeline_response, timeline_data = _api_data(page, f"{base_url}/api/v1/timeline?limit=100")
    assert removed_item_ids <= _item_ids(default_data)
    assert removed_item_ids <= _item_ids(explicit_data)
    assert removed_item_ids <= _item_ids(timeline_data)
    assert default_data["total"] == len(REMOVED_SLUGS) + len(REPRESENTATIVES)
    assert timeline_data["total"] == len(REMOVED_SLUGS) + len(REPRESENTATIVES)
    assert explicit_data["count"] == len(REMOVED_SLUGS) + len(REPRESENTATIVES)
    for filename, response in (
        ("pre-sync-curated-default.json", default_response),
        ("pre-sync-curated-precomputed.json", explicit_response),
        ("pre-sync-timeline.json", timeline_response),
    ):
        (evidence_dir / filename).write_text(response.text() + "\n", encoding="utf-8")

    for path, screenshot in (("/", "pre-sync-selected.png"), ("/all", "pre-sync-all.png")):
        page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
        expect(page.locator(".timeline-card")).to_have_count(
            len(REMOVED_SLUGS) + len(REPRESENTATIVES)
        )
        expect(page.locator(".date-count")).to_have_text(
            f"{len(REMOVED_SLUGS) + len(REPRESENTATIVES)} 条"
        )
        for slug in REMOVED_SLUGS:
            card = page.locator(f'.timeline-card[data-source-id="{slug}"]')
            expect(card).to_have_count(1)
            expect(card.locator(".item-title")).to_have_text(f"RemovedSourceMarker {slug}")
        expect(page.get_by_text(LEGACY_WECHAT_MARKER, exact=True)).to_have_count(0)
        page.screenshot(path=evidence_dir / screenshot, full_page=True)

    page.goto(f"{base_url}/all?q=RemovedSourceMarker", wait_until="domcontentloaded")
    expect(page.locator(".timeline-card")).to_have_count(len(REMOVED_SLUGS))
    expect(page.locator(".date-count")).to_have_text(f"{len(REMOVED_SLUGS)} 条")
    for slug in REMOVED_SLUGS:
        expect(page.locator(f'.timeline-card[data-source-id="{slug}"]')).to_have_count(1)

    page.goto(f"{base_url}/about", wait_until="domcontentloaded")
    for slug in (*REMOVED_SLUGS, LEGACY_WECHAT_SLUG):
        expect(page.locator("#sources-table code").get_by_text(slug, exact=True)).to_have_count(1)

    wechat_response, wechat_data = _api_data(
        page,
        f"{base_url}/api/v1/wechat?q={LEGACY_WECHAT_MARKER}",
    )
    assert wechat_data["total"] == 1
    assert [item["slug"] for item in wechat_data["items"]] == ["alignment-legacy-wechat"]
    (evidence_dir / "pre-sync-wechat.json").write_text(
        wechat_response.text() + "\n",
        encoding="utf-8",
    )
    page.goto(f"{base_url}/wechat", wait_until="domcontentloaded")
    expect(page.get_by_text(LEGACY_WECHAT_MARKER, exact=True)).to_have_count(1)
    page.screenshot(path=evidence_dir / "pre-sync-wechat.png", full_page=True)
    page.goto(f"{base_url}/wechat/alignment-legacy-wechat", wait_until="domcontentloaded")
    expect(page.get_by_role("heading", name=LEGACY_WECHAT_MARKER, exact=True)).to_have_count(1)


def _assert_preserved_upgrade_rows(db_path: Path, *, with_mp2rss: bool) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        disabled = conn.execute(
            f"SELECT id, enabled FROM sources WHERE id IN ({','.join('?' for _ in (*REMOVED_SLUGS, LEGACY_WECHAT_SLUG))}) ORDER BY id",
            (*REMOVED_SLUGS, LEGACY_WECHAT_SLUG),
        ).fetchall()
        assert {row["id"] for row in disabled} == set(REMOVED_SLUGS) | {LEGACY_WECHAT_SLUG}
        assert {row["enabled"] for row in disabled} == {0}
        removed_items = conn.execute(
            f"SELECT id, source_id FROM items WHERE source_id IN ({','.join('?' for _ in REMOVED_SLUGS)})",
            REMOVED_SLUGS,
        ).fetchall()
        assert {(row["id"], row["source_id"]) for row in removed_items} == {
            (f"removed-{slug}", slug) for slug in REMOVED_SLUGS
        }
        assert conn.execute(
            f"SELECT COUNT(*) FROM curated_items WHERE item_id IN ({','.join('?' for _ in REMOVED_SLUGS)})",
            tuple(f"removed-{slug}" for slug in REMOVED_SLUGS),
        ).fetchone()[0] == len(REMOVED_SLUGS) * 2
        assert conn.execute(
            "SELECT COUNT(*) FROM items WHERE id='alignment-legacy-wechat' AND source_id=?",
            (LEGACY_WECHAT_SLUG,),
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM wechat_interpretations WHERE item_id='alignment-legacy-wechat' AND slug='alignment-legacy-wechat'",
        ).fetchone()[0] == 1
        wx_row = conn.execute("SELECT enabled FROM sources WHERE id='wx_mp2rss'").fetchone()
        if with_mp2rss:
            assert wx_row is not None
            assert bool(wx_row["enabled"])
        else:
            assert wx_row is None


def _assert_removed_content_is_publicly_absent(
    page: Page,
    base_url: str,
    evidence_dir: Path,
    *,
    with_mp2rss: bool,
) -> None:
    removed_item_ids = {f"removed-{slug}" for slug in REMOVED_SLUGS}
    sources_response, sources_data = _api_data(page, f"{base_url}/api/v2/sources")
    source_ids = {row["id"] for row in sources_data["sources"]}
    assert not (set(REMOVED_SLUGS) | {LEGACY_WECHAT_SLUG}) & source_ids
    assert ("wx_mp2rss" in source_ids) is with_mp2rss

    default_response, default_data = _api_data(page, f"{base_url}/api/v1/curated?limit=100")
    explicit_response, explicit_data = _api_data(
        page,
        f"{base_url}/api/v1/curated?run_id={PRECOMPUTED_RUN_ID}&limit=100",
    )
    timeline_response, timeline_data = _api_data(page, f"{base_url}/api/v1/timeline?limit=100")
    curated_search_response, curated_search = _api_data(
        page,
        f"{base_url}/api/v1/curated?q=RemovedSourceMarker&limit=100",
    )
    timeline_search_response, timeline_search = _api_data(
        page,
        f"{base_url}/api/v1/timeline?q=RemovedSourceMarker&limit=100",
    )
    for data in (default_data, explicit_data, timeline_data, curated_search, timeline_search):
        assert not removed_item_ids & _item_ids(data)
    assert default_data["total"] == len(REPRESENTATIVES)
    assert timeline_data["total"] == len(REPRESENTATIVES)
    assert explicit_data["count"] == len(REPRESENTATIVES)
    assert curated_search["count"] == 0
    assert timeline_search["total"] == 0
    for filename, response in (
        ("sources.json", sources_response),
        ("post-sync-curated-default.json", default_response),
        ("post-sync-curated-precomputed.json", explicit_response),
        ("post-sync-timeline.json", timeline_response),
        ("post-sync-curated-search.json", curated_search_response),
        ("post-sync-timeline-search.json", timeline_search_response),
    ):
        (evidence_dir / filename).write_text(response.text() + "\n", encoding="utf-8")

    for path in ("/", "/all"):
        page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
        expect(page.locator(".timeline-card")).to_have_count(len(REPRESENTATIVES))
        expect(page.locator(".date-count")).to_have_text(f"{len(REPRESENTATIVES)} 条")
        for slug in REMOVED_SLUGS:
            expect(page.locator(f'.timeline-card[data-source-id="{slug}"]')).to_have_count(0)
        expect(page.get_by_text(LEGACY_WECHAT_MARKER, exact=True)).to_have_count(0)

    page.goto(f"{base_url}/all?q=RemovedSourceMarker", wait_until="domcontentloaded")
    expect(page.locator(".timeline-card")).to_have_count(0)
    for slug in REMOVED_SLUGS:
        expect(page.locator(f'.timeline-card[data-source-id="{slug}"]')).to_have_count(0)

    wechat_response, wechat_data = _api_data(
        page,
        f"{base_url}/api/v1/wechat?q={LEGACY_WECHAT_MARKER}",
    )
    assert wechat_data["total"] == 0
    assert wechat_data["items"] == []
    (evidence_dir / "post-sync-wechat-search.json").write_text(
        wechat_response.text() + "\n",
        encoding="utf-8",
    )
    assert page.request.get(f"{base_url}/wechat/alignment-legacy-wechat").status == 404


@pytest.mark.parametrize("with_mp2rss", [False, True], ids=["without-mp2rss", "with-mp2rss"])
def test_aihot_source_alignment_four_page_matrix(
    browser: Browser,
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    with_mp2rss: bool,
) -> None:
    evidence_root = Path(
        os.environ.get(
            "AI_RADAR_ALIGNMENT_EVIDENCE_DIR",
            tmp_path_factory.getbasetemp() / "aihot-alignment-evidence",
        )
    )
    evidence_dir = evidence_root / ("with-mp2rss" if with_mp2rss else "without-mp2rss")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / ("with-mp2rss.db" if with_mp2rss else "without-mp2rss.db")
    print(f"AIHOT alignment DB: {db_path.resolve()}")

    with _mp2rss_fixture(with_mp2rss) as feed_urls:
        secret_urls = sorted((feed_urls or {}).values())
        _prepare_upgrade_db(db_path, feed_urls=feed_urls, monkeypatch=monkeypatch)
        with _serve(db_path) as pre_sync_base_url:
            pre_sync_context = browser.new_context(viewport={"width": 1366, "height": 900})
            pre_sync_requested_urls: list[str] = []
            pre_sync_context.on(
                "request",
                lambda request: pre_sync_requested_urls.append(request.url),
            )
            pre_sync_page = pre_sync_context.new_page()
            try:
                _assert_upgrade_fixtures_reach_public_consumers(
                    pre_sync_page,
                    pre_sync_base_url,
                    evidence_dir,
                )
                for secret_url in secret_urls:
                    assert secret_url not in pre_sync_page.content()
                    assert secret_url not in (evidence_dir / "pre-sync-sources.json").read_text(
                        encoding="utf-8"
                    )
                    assert all(secret_url not in url for url in pre_sync_requested_urls)
                (evidence_dir / "pre-sync-network-urls.json").write_text(
                    json.dumps(pre_sync_requested_urls, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            finally:
                pre_sync_context.close()

        _sync_current_sources(db_path, feed_urls=feed_urls)
        _assert_preserved_upgrade_rows(db_path, with_mp2rss=with_mp2rss)
        with _serve(db_path) as base_url:
            context = browser.new_context(viewport={"width": 1366, "height": 900})
            requested_urls: list[str] = []
            context.on("request", lambda request: requested_urls.append(request.url))
            page = context.new_page()
            try:
                _assert_removed_content_is_publicly_absent(
                    page,
                    base_url,
                    evidence_dir,
                    with_mp2rss=with_mp2rss,
                )
                _assert_main_page(page, base_url, "/")
                page.screenshot(path=evidence_dir / "selected.png", full_page=True)
                _assert_main_page(page, base_url, "/all")
                page.screenshot(path=evidence_dir / "all.png", full_page=True)
                for source_id, (title, _url) in REPRESENTATIVES.items():
                    page.goto(f"{base_url}/all?q={title}", wait_until="domcontentloaded")
                    expect(page.locator(f'.timeline-card[data-source-id="{source_id}"]')).to_have_count(1)

                page.goto(f"{base_url}/about", wait_until="domcontentloaded")
                expected_count = _expected_source_count(feed_urls)
                expect(page.locator("#sources-table tr")).to_have_count(expected_count, timeout=10_000)
                page.screenshot(path=evidence_dir / "about.png", full_page=True)

                response = context.request.get(f"{base_url}/api/v2/sources")
                assert response.ok
                response_text = response.text()
                (evidence_dir / "sources.json").write_text(response_text + "\n", encoding="utf-8")
                public_sources = response.json()["data"]["sources"]
                contract_rows = json.loads(CONTRACT.read_text(encoding="utf-8"))["sources"]
                expected_rows = [
                    row
                    for row in contract_rows
                    if row["ai_radar_main_timeline_member"] or with_mp2rss
                ]
                _assert_about_contract_rows(page, expected_rows)
                assert {row["id"] for row in public_sources} == {row["slug"] for row in expected_rows}
                by_id = {row["id"]: row for row in public_sources}
                for expected in expected_rows:
                    actual = by_id[expected["slug"]]
                    assert actual["name"] == expected["name"]
                    assert actual["tier"] == expected["tier"]
                    assert actual["configuration_status"] == "enabled"
                    assert actual["kind"] == expected["kind"]
                    assert actual["retrieval_entrypoint_url"] == (
                        None if expected["kind"] == "wechat" else expected["fetch_url"]
                    )
                    assert actual["public_landing_url"] == expected.get(
                        "public_url_override",
                        expected["homepage_url"],
                    )
                    assert actual["icon_url"] == expected["icon_url"]

                if with_mp2rss:
                    wx_row = page.locator("#sources-table tr").filter(has=page.get_by_text("wx_mp2rss", exact=True))
                    expect(wx_row).to_contain_text("仅用于微信文章解读，不属于主时间线")
                    expect(wx_row.locator("a")).to_have_attribute("href", "https://mp.weixin.qq.com/")
                else:
                    expect(page.get_by_text("wx_mp2rss", exact=True)).to_have_count(0)
                x_row = page.locator("#sources-table tr").filter(has=page.get_by_text("x_openai", exact=True))
                expect(x_row).to_contain_text("待首次验证")

                page.goto(f"{base_url}/wechat", wait_until="domcontentloaded")
                if with_mp2rss:
                    expect(page.get_by_text("WechatAlignmentMarker", exact=True)).to_have_count(1)
                else:
                    expect(page.locator(".wechat-card")).to_have_count(0)
                expect(page.get_by_text(LEGACY_WECHAT_MARKER, exact=True)).to_have_count(0)
                for title, _url in REPRESENTATIVES.values():
                    expect(page.get_by_text(title, exact=True)).to_have_count(0)
                page.screenshot(path=evidence_dir / "wechat.png", full_page=True)

                for secret_url in secret_urls:
                    assert secret_url not in page.content()
                    assert secret_url not in response_text
                    assert all(secret_url not in url for url in requested_urls)
                (evidence_dir / "network-urls.json").write_text(
                    json.dumps(requested_urls, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                artifact_hashes = {
                    path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in evidence_dir.iterdir()
                    if path.is_file() and path.name != "branch-manifest.json"
                }
                branch = "with_mp2rss" if with_mp2rss else "without_mp2rss"
                manifest_path = evidence_root / "manifest.json"
                input_hashes = {
                    "contract_sha256": hashlib.sha256(CONTRACT.read_bytes()).hexdigest(),
                    "config_sha256": hashlib.sha256((ROOT / "data/sources.toml").read_bytes()).hexdigest(),
                    "code_sha256_by_path_relative_to_repository_root": {
                        path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                        for path in ALIGNMENT_CODE_PATHS
                    },
                }
                branch_manifest = {
                    "schema_version": 1,
                    "artifact_type": "playwright_source_alignment_branch",
                    "batch_id": ALIGNMENT_BATCH_ID,
                    "branch": branch,
                    "verdict": "source_alignment_consumer_matrix_passed_for_branch",
                    "input_hashes": input_hashes,
                    "assertions": {
                        "pre_sync_upgrade_fixture_reached_real_consumers": "passed",
                        "post_sync_removed_sources_hidden_from_all_public_consumers": "passed",
                        "post_sync_historical_rows_and_relations_preserved_disabled": "passed",
                        "default_latest_and_explicit_precomputed_curated_paths": "passed",
                        "visible_browser_counts_exclude_disabled_items": "passed",
                        "selected_timeline_main_source_isolation": "passed",
                        "all_timeline_main_source_isolation": "passed",
                        "about_dom_exact_contract_row_projection": "passed",
                        "wechat_source_isolation": "passed",
                        "public_projection_secret_boundary": "passed",
                        "paid_mp2rss_url_non_disclosure": (
                            "passed" if secret_urls else "not_applicable_no_paid_url_configured"
                        ),
                    },
                    "artifact_sha256_by_path_relative_to_branch_evidence_dir": artifact_hashes,
                }
                branch_manifest_path = evidence_dir / "branch-manifest.json"
                temporary = branch_manifest_path.with_name(".branch-manifest.json.tmp")
                temporary.write_text(
                    json.dumps(branch_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                temporary.replace(branch_manifest_path)

                manifest = {
                    "schema_version": 1,
                    "artifact_type": "playwright_source_alignment",
                    "batch_id": ALIGNMENT_BATCH_ID,
                    "verdict": "pending",
                    "branches": {},
                }
                if manifest_path.exists():
                    existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    if existing_manifest.get("batch_id") == ALIGNMENT_BATCH_ID:
                        manifest = existing_manifest
                manifest["branches"][branch] = {
                    "branch_manifest_path_relative_to_evidence_root": branch_manifest_path.relative_to(evidence_root).as_posix(),
                    "branch_manifest_sha256": hashlib.sha256(branch_manifest_path.read_bytes()).hexdigest(),
                }
                expected_branches = {"with_mp2rss", "without_mp2rss"}
                if set(manifest["branches"]) == expected_branches:
                    for expected_branch in expected_branches:
                        reference = manifest["branches"][expected_branch]
                        path = evidence_root / reference["branch_manifest_path_relative_to_evidence_root"]
                        assert hashlib.sha256(path.read_bytes()).hexdigest() == reference["branch_manifest_sha256"]
                        persisted = json.loads(path.read_text(encoding="utf-8"))
                        assert persisted["batch_id"] == ALIGNMENT_BATCH_ID
                        assert persisted["branch"] == expected_branch
                        assert persisted["verdict"] == "source_alignment_consumer_matrix_passed_for_branch"
                        assert persisted["input_hashes"] == input_hashes
                        for filename, expected_sha in persisted[
                            "artifact_sha256_by_path_relative_to_branch_evidence_dir"
                        ].items():
                            artifact_path = path.parent / filename
                            assert hashlib.sha256(artifact_path.read_bytes()).hexdigest() == expected_sha
                    manifest["verdict"] = "both_mp2rss_configuration_branches_passed_source_alignment_matrix"
                temporary = manifest_path.with_name(".manifest.json.tmp")
                temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                temporary.replace(manifest_path)
            finally:
                context.close()
