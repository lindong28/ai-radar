from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from airadar.db import migrate
from airadar.web.app import create_app

SUMMARY_MD = """### 📋 文章概况

这是一篇关于 agent 工程实践的文章，摘要可直接放在卡片上。

### ✨ 独特亮点

- 提供了真实工程取舍。

### 🛠️ 可动手实践

- 可以马上复用检查清单。

### 🧠 可复用认知

- 先稳定输入输出契约，再扩展自动化。

### 🔑 关键词

Agent, 工程化, 自动化

### ⚖️ 价值判断

值得一看，因为它包含可执行的工程实践。
<script>alert("xss")</script><img src="x" onerror="alert(1)">
"""


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _seed_wechat_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = _connect(db_path)
    conn.execute(
        """
        INSERT INTO sources (
          id, name, url, tier, enabled, kind, homepage_url, icon_url, meta_json, synced_at
        )
        VALUES (
          'wx_mp2rss', '微信公众号（Mp2RSS 合集）', 'https://feed.example/rss', 'T2', 1,
          'wechat', 'https://mp.weixin.qq.com/', '/wechat-icon.svg', '{}', '2026-06-02T00:00:00Z'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO wechat_account_avatars (account, avatar_url, checked_at, updated_at)
        VALUES (
          '机器之心', 'https://example.com/avatar.png',
          '2026-06-02T00:00:00Z', '2026-06-02T00:00:00Z'
        )
        """
    )
    items = [
        (
            "item-newer",
            "https://mp.weixin.qq.com/s/newer",
            "新文章",
            "机器之心",
            "2026-06-02T10:00:00Z",
            "agent 工程实践正文",
            "h-newer",
        ),
        (
            "item-older",
            "https://mp.weixin.qq.com/s/older",
            "旧文章",
            "机器之心",
            "2026-06-01T10:00:00Z",
            "旧文章正文",
            "h-older",
        ),
        (
            "item-skip",
            "https://mp.weixin.qq.com/s/skip",
            "不值得读",
            "机器之心",
            "2026-05-31T10:00:00Z",
            "短消息正文",
            "h-skip",
        ),
    ]
    conn.executemany(
        """
        INSERT INTO items (
          id, source_id, url, title, author, published_at, fetched_at,
          content_text, content_html, content_hash, extra_json
        )
        VALUES (?, 'wx_mp2rss', ?, ?, ?, ?, ?, ?, NULL, ?, '{}')
        """,
        [(item_id, url, title, author, published_at, published_at, content, content_hash)
         for item_id, url, title, author, published_at, content, content_hash in items],
    )
    conn.execute(
        """
        INSERT INTO wechat_interpretations (
          item_id, slug, recommendation, save_decision, save_reason, abstract,
          tags_json, summary_md, model, kb_synced, processed_at, error
        )
        VALUES (
          'item-newer', 'newer-slug', '值得一看', 1, '有实践价值',
          '这是一篇关于 agent 工程实践的文章，摘要可直接放在卡片上。',
          '["Agent","工程化"]', ?, 'fake-model', 1, '2026-06-02T10:10:00Z', NULL
        )
        """,
        (SUMMARY_MD,),
    )
    conn.execute(
        """
        INSERT INTO wechat_interpretations (
          item_id, slug, recommendation, save_decision, save_reason, abstract,
          tags_json, summary_md, model, kb_synced, processed_at, error
        )
        VALUES (
          'item-older', 'older-slug', '必读', 1, '强相关',
          '旧文章摘要', '["AI"]', ?, 'fake-model', 1, '2026-06-01T10:10:00Z', NULL
        )
        """,
        (SUMMARY_MD,),
    )
    conn.execute(
        """
        INSERT INTO wechat_interpretations (
          item_id, slug, recommendation, save_decision, save_reason, abstract,
          tags_json, summary_md, model, kb_synced, processed_at, error
        )
        VALUES (
          'item-skip', 'skip-slug', '可跳过', 0, '信息密度低',
          '短消息摘要', '["低价值"]', ?, 'fake-model', 0, '2026-05-31T10:10:00Z', NULL
        )
        """,
        (SUMMARY_MD,),
    )
    conn.commit()
    conn.close()
    return db_path


def _seed_runner_db(tmp_path: Path, *, item_id: str = "item-1", title: str = "测试文章") -> Path:
    db_path = tmp_path / "runner.db"
    migrate(db_path)
    conn = _connect(db_path)
    conn.execute(
        """
        INSERT INTO sources (
          id, name, url, tier, enabled, kind, homepage_url, icon_url, meta_json, synced_at
        )
        VALUES (
          'wx_mp2rss', '微信公众号（Mp2RSS 合集）', 'https://feed.example/rss', 'T2', 1,
          'wechat', 'https://mp.weixin.qq.com/', '/wechat-icon.svg', '{}', '2026-06-02T00:00:00Z'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO items (
          id, source_id, url, title, author, published_at, fetched_at,
          content_text, content_html, content_hash, extra_json
        )
        VALUES (?, 'wx_mp2rss', 'https://mp.weixin.qq.com/s/test', ?, '机器之心',
                '2026-06-02T10:00:00Z', '2026-06-02T10:01:00Z',
                '这是一篇足够长的微信正文，用来喂给 summarize-article。', NULL, ?, '{}')
        """,
        (item_id, title, f"h-{item_id}"),
    )
    conn.commit()
    conn.close()
    return db_path


def _assistant_root(tmp_path: Path) -> Path:
    root = tmp_path / "ai-assistant"
    script_dir = root / "agents" / "summary-agent"
    script_dir.mkdir(parents=True)
    for name in ("summarize.sh", "run.sh"):
        path = script_dir / name
        path.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)
    return root


def test_wechat_migration_creates_table_and_indexes(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = _connect(db_path)
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(wechat_interpretations)").fetchall()}
        indexes = {row["name"] for row in conn.execute("PRAGMA index_list(wechat_interpretations)").fetchall()}
    finally:
        conn.close()

    assert {"item_id", "slug", "save_decision", "abstract", "tags_json", "summary_md", "kb_synced"} <= columns
    assert "idx_wechat_interp_decision" in indexes
    assert "idx_wechat_interp_slug" in indexes


def test_wechat_api_returns_only_worth_reading_items_with_pagination(tmp_path: Path) -> None:
    db_path = _seed_wechat_db(tmp_path)
    client = TestClient(create_app(db_path))

    response = client.get("/api/v1/wechat?limit=1&page=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["total"] == 2
    assert payload["data"]["page"] == 1
    assert payload["data"]["limit"] == 1
    assert [item["slug"] for item in payload["data"]["items"]] == ["newer-slug"]
    item = payload["data"]["items"][0]
    assert item["title"] == "新文章"
    assert item["abstract"]
    assert item["tags"] == ["Agent", "工程化"]
    assert item["author"] == "机器之心"
    assert item["avatar_url"] == "https://example.com/avatar.png"

    full_response = client.get("/api/v1/wechat?limit=500")
    assert full_response.status_code == 200


def test_wechat_api_clamps_out_of_range_page_and_carries_page_in_detail_urls(tmp_path: Path) -> None:
    db_path = _seed_wechat_db(tmp_path)
    client = TestClient(create_app(db_path))

    response = client.get("/api/v1/wechat?limit=1&page=999")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 2
    assert data["page"] == 2
    assert [item["slug"] for item in data["items"]] == ["older-slug"]
    assert data["items"][0]["detail_url"] == "/wechat/older-slug?page=2"

    listing = client.get("/wechat?limit=1&page=999")
    assert listing.status_code == 200
    assert "暂无微信文章解读" not in listing.text
    assert "/wechat/older-slug?page=2" in listing.text


def test_wechat_title_artifact_repair_cleans_existing_title_and_unique_slug(tmp_path: Path) -> None:
    from airadar.interpret.runner import repair_wechat_title_artifacts

    db_path = _seed_wechat_db(tmp_path)
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE items SET title=? WHERE id='item-older'",
            ("居然可以在 Claude 桌面端用三方模型了！\\n\\n只需要",),
        )
        conn.execute(
            "UPDATE wechat_interpretations SET slug=? WHERE item_id='item-older'",
            ("居然可以在-claude-桌面端用三方模型了-n-n只需要",),
        )
        conn.commit()

        changed = repair_wechat_title_artifacts(conn)
        item = conn.execute("SELECT title FROM items WHERE id='item-older'").fetchone()
        interp = conn.execute("SELECT slug FROM wechat_interpretations WHERE item_id='item-older'").fetchone()

    assert changed == 1
    assert item["title"] == "居然可以在 Claude 桌面端用三方模型了！ 只需要"
    assert "\\n" not in item["title"]
    assert "\n" not in item["title"]
    assert interp["slug"] == "居然可以在-claude-桌面端用三方模型了-只需要"
    assert "-n-" not in interp["slug"]


def test_wechat_pages_render_preload_detail_and_sanitize_markdown(tmp_path: Path) -> None:
    db_path = _seed_wechat_db(tmp_path)
    client = TestClient(create_app(db_path))

    listing = client.get("/wechat")
    assert listing.status_code == 200
    assert "微信文章解读" in listing.text
    assert "新文章" in listing.text
    assert "/wechat/newer-slug" in listing.text
    assert 'data-detail-url="/wechat/newer-slug"' in listing.text
    assert 'role="link"' in listing.text
    assert "skip-slug" not in listing.text
    assert '<script id="__PRELOAD__" type="application/json">' in listing.text

    detail = client.get("/wechat/newer-slug")
    assert detail.status_code == 200
    assert "‹ 返回列表" in detail.text
    for heading in ("文章概况", "独特亮点", "可动手实践", "可复用认知", "关键词", "价值判断"):
        assert heading in detail.text
    assert "<h3" in detail.text
    assert "<script" not in detail.text.lower()
    assert "onerror" not in detail.text.lower()

    detail_from_page = client.get("/wechat/newer-slug?page=2")
    assert detail_from_page.status_code == 200
    assert 'href="/wechat?page=2"' in detail_from_page.text

    skipped = client.get("/wechat/skip-slug")
    assert skipped.status_code == 404
    assert "微信文章解读" in skipped.text
    assert 'href="/wechat"' in skipped.text
    assert "side-link-active" in skipped.text


def test_wechat_sidebar_link_and_curated_alias_exist_on_public_pages(tmp_path: Path) -> None:
    db_path = _seed_wechat_db(tmp_path)
    client = TestClient(create_app(db_path))

    for path in ["/", "/all", "/daily", "/about", "/wechat"]:
        response = client.get(path)
        assert response.status_code == 200
        assert 'href="/wechat"' in response.text
        assert "微信文章解读" in response.text

    curated = client.get("/curated")
    assert curated.status_code == 200
    assert str(curated.url).endswith("/curated")
    assert 'href="/wechat"' in curated.text


def test_interpret_runner_saves_worth_reading_result_and_patches_kb_meta(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from airadar.interpret.runner import run_interpret

    db_path = _seed_runner_db(tmp_path)
    assistant_root = _assistant_root(tmp_path)
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()
    (batch_dir / "test-slug_summary.md").write_text(SUMMARY_MD, encoding="utf-8")
    (batch_dir / "test-slug_meta.json").write_text(
        json.dumps({"slug": "test-slug", "url": "https://wrong.example", "source": "wrong"}, ensure_ascii=False),
        encoding="utf-8",
    )
    calls: list[list[str]] = []
    saved_meta: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append([str(part) for part in cmd])
        if "--check-url" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"found": False}, indent=2), stderr="")
        if "summarize.sh" in str(cmd[0]):
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    {
                        "ok": True,
                        "batch_dir": str(batch_dir),
                        "result": {
                            "slug": "test-slug",
                            "save_decision": True,
                            "save_reason": "有实践价值",
                            "recommendation": "值得一看",
                            "tags": ["Agent", "工程化"],
                            "model": "fake-model",
                            "summary_md": "stdout should not be used",
                        },
                    },
                    ensure_ascii=False,
                ),
                stderr="",
            )
        if "--save-from-batch" in cmd:
            saved_meta.update(json.loads(cmd[cmd.index("--meta-json") + 1]))
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps({"ok": True, "summary_file_path": "/kb/test-slug_output.md"}),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with _connect(db_path) as conn:
        summary = run_interpret(conn, backfill=True, assistant_root=assistant_root, tmp_root=tmp_path / "tmp")
        row = conn.execute("SELECT * FROM wechat_interpretations WHERE item_id='item-1'").fetchone()

    assert summary.processed == 1
    assert summary.errors == 0
    assert row["slug"] == "test-slug"
    assert row["save_decision"] == 1
    assert row["kb_synced"] == 1
    assert row["summary_md"] == SUMMARY_MD
    assert row["abstract"] == "这是一篇关于 agent 工程实践的文章，摘要可直接放在卡片上。"
    assert json.loads(row["tags_json"]) == ["Agent", "工程化"]
    assert saved_meta["url"] == "https://mp.weixin.qq.com/s/test"
    assert saved_meta["source"] == "机器之心"
    assert saved_meta["publish_date"] == "2026-06-02T10:00:00Z"
    assert any("--save-from-batch" in call for call in calls)


def test_interpret_runner_skips_kb_for_not_worth_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from airadar.interpret.runner import run_interpret

    db_path = _seed_runner_db(tmp_path)
    assistant_root = _assistant_root(tmp_path)
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()
    (batch_dir / "skip-slug_summary.md").write_text(SUMMARY_MD, encoding="utf-8")
    save_calls = 0

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal save_calls
        if "--check-url" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"found": False}, indent=2), stderr="")
        if "summarize.sh" in str(cmd[0]):
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    {
                        "ok": True,
                        "batch_dir": str(batch_dir),
                        "result": {
                            "slug": "skip-slug",
                            "save_decision": False,
                            "save_reason": "信息密度低",
                            "recommendation": "可跳过",
                            "tags": ["低价值"],
                        },
                    },
                    ensure_ascii=False,
                ),
                stderr="",
            )
        if "--save-from-batch" in cmd:
            save_calls += 1
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with _connect(db_path) as conn:
        summary = run_interpret(conn, backfill=True, assistant_root=assistant_root, tmp_root=tmp_path / "tmp")
        row = conn.execute("SELECT * FROM wechat_interpretations WHERE item_id='item-1'").fetchone()

    assert summary.processed == 1
    assert summary.errors == 0
    assert row["save_decision"] == 0
    assert row["kb_synced"] == 0
    assert row["error"] is None
    assert save_calls == 0


def test_interpret_runner_reuses_kb_check_url_hit_without_llm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from airadar.interpret.runner import run_interpret

    db_path = _seed_runner_db(tmp_path)
    assistant_root = _assistant_root(tmp_path)
    kb_summary = tmp_path / "kb-existing_output.md"
    kb_summary.write_text(SUMMARY_MD, encoding="utf-8")
    index_path = assistant_root / "data" / "summary_agent" / "dong_lin" / "index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(
            [
                {
                    "title": "测试文章",
                    "output": {"summary_file_path": str(kb_summary)},
                    "metadata": {
                        "url": "https://mp.weixin.qq.com/s/test",
                        "tags": ["Agent"],
                        "model_name": "existing-model",
                    },
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    summarize_calls = 0

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal summarize_calls
        if "--check-url" in cmd:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    {
                        "found": True,
                        "slug": "kb-existing",
                        "summary_file_path": str(kb_summary),
                        "recommendation": "必读",
                        "save_reason": "已在 KB",
                    },
                    ensure_ascii=False,
                ),
                stderr="",
            )
        if "summarize.sh" in str(cmd[0]):
            summarize_calls += 1
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with _connect(db_path) as conn:
        summary = run_interpret(conn, backfill=True, assistant_root=assistant_root, tmp_root=tmp_path / "tmp")
        row = conn.execute("SELECT * FROM wechat_interpretations WHERE item_id='item-1'").fetchone()

    assert summary.processed == 1
    assert summary.errors == 0
    assert row["slug"] == "kb-existing"
    assert row["save_decision"] == 1
    assert row["kb_synced"] == 1
    assert json.loads(row["tags_json"]) == ["Agent"]
    assert row["model"] == "existing-model"
    assert summarize_calls == 0


def test_interpret_runner_falls_back_when_kb_summary_file_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from airadar.interpret.runner import run_interpret

    db_path = _seed_runner_db(tmp_path)
    assistant_root = _assistant_root(tmp_path)
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()
    (batch_dir / "fallback-slug_summary.md").write_text(SUMMARY_MD, encoding="utf-8")
    summarize_calls = 0

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal summarize_calls
        if "--check-url" in cmd:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    {"found": True, "slug": "missing", "summary_file_path": str(tmp_path / "missing.md")},
                    indent=2,
                    ensure_ascii=False,
                ),
                stderr="",
            )
        if "summarize.sh" in str(cmd[0]):
            summarize_calls += 1
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    {
                        "ok": True,
                        "batch_dir": str(batch_dir),
                        "result": {
                            "slug": "fallback-slug",
                            "save_decision": False,
                            "save_reason": "fallback complete",
                            "recommendation": "可跳过",
                            "tags": ["Fallback"],
                        },
                    },
                    ensure_ascii=False,
                ),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with _connect(db_path) as conn:
        summary = run_interpret(conn, backfill=True, assistant_root=assistant_root, tmp_root=tmp_path / "tmp")
        row = conn.execute("SELECT * FROM wechat_interpretations WHERE item_id='item-1'").fetchone()

    assert summary.processed == 1
    assert summary.errors == 0
    assert row["slug"] == "fallback-slug"
    assert row["save_decision"] == 0
    assert summarize_calls == 1


def test_interpret_runner_retries_duplicate_kb_slug_with_unique_slug(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from airadar.interpret.runner import run_interpret

    db_path = _seed_runner_db(tmp_path)
    assistant_root = _assistant_root(tmp_path)
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()
    (batch_dir / "existing-slug_summary.md").write_text(SUMMARY_MD, encoding="utf-8")
    (batch_dir / "existing-slug_article.md").write_text("# 测试文章\n\n正文", encoding="utf-8")
    (batch_dir / "existing-slug_meta.json").write_text("{}", encoding="utf-8")
    kb_summary_rel = Path("data/summary_agent/dong_lin/article_summaries/existing-slug_output.md")
    kb_summary = assistant_root / kb_summary_rel
    kb_summary.parent.mkdir(parents=True)
    kb_summary.write_text(SUMMARY_MD, encoding="utf-8")
    index_path = assistant_root / "data" / "summary_agent" / "dong_lin" / "index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(
            [
                {
                    "title": "测试文章",
                    "output": {"summary_file_path": str(kb_summary_rel)},
                    "metadata": {"tags": ["Agent"], "model_name": "existing-model"},
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if "--check-url" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"found": False}, indent=2), stderr="")
        if "summarize.sh" in str(cmd[0]):
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    {
                        "ok": True,
                        "batch_dir": str(batch_dir),
                        "result": {
                            "slug": "existing-slug",
                            "save_decision": True,
                            "save_reason": "有实践价值",
                            "recommendation": "值得一看",
                            "tags": ["Agent"],
                            "model": "fresh-model",
                        },
                    },
                    ensure_ascii=False,
                ),
                stderr="",
            )
        if "--save-from-batch" in cmd:
            save_slug = cmd[cmd.index("--save-from-batch") + 1]
            if save_slug == "existing-slug":
                raise subprocess.CalledProcessError(
                    returncode=1,
                    cmd=cmd,
                    stderr="Error: Slug 'existing-slug' already exists in index.json\n",
                )
            saved_meta = json.loads(cmd[cmd.index("--meta-json") + 1])
            assert save_slug == "existing-slug_radar_item-1"
            assert saved_meta["slug"] == save_slug
            assert saved_meta["url"] == "https://mp.weixin.qq.com/s/test"
            assert (batch_dir / f"{save_slug}_summary.md").exists()
            assert (batch_dir / f"{save_slug}_article.md").exists()
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps({"ok": True, "summary_file_path": f"/kb/{save_slug}_output.md"}),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with _connect(db_path) as conn:
        summary = run_interpret(conn, backfill=True, assistant_root=assistant_root, tmp_root=tmp_path / "tmp")
        row = conn.execute("SELECT * FROM wechat_interpretations WHERE item_id='item-1'").fetchone()

    assert summary.processed == 1
    assert summary.errors == 0
    assert row["slug"] == "existing-slug-radar-item-1"
    assert row["save_decision"] == 1
    assert row["kb_synced"] == 1
    assert row["error"] is None
    assert row["model"] == "fresh-model"
    assert json.loads(row["tags_json"]) == ["Agent"]


def test_interpret_subprocess_does_not_inherit_radar_virtualenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from airadar.interpret.runner import _run_json

    seen_env: dict[str, str] = {}
    monkeypatch.setenv("VIRTUAL_ENV", "/Users/lindong/research/ai-radar/.venv")

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen_env.update(kwargs["env"])
        return subprocess.CompletedProcess(cmd, 0, stdout='{"ok": true}', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    payload = _run_json(["/fake/run.sh", "--check-url", "https://example.test"], cwd=tmp_path)

    assert payload == {"ok": True}
    assert "VIRTUAL_ENV" not in seen_env


def test_interpret_runner_records_errors_without_aborting_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from airadar.interpret.runner import run_interpret

    db_path = _seed_runner_db(tmp_path)
    assistant_root = _assistant_root(tmp_path)

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(returncode=2, cmd=cmd, stderr="boom")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with _connect(db_path) as conn:
        summary = run_interpret(conn, backfill=True, assistant_root=assistant_root, tmp_root=tmp_path / "tmp")
        row = conn.execute("SELECT * FROM wechat_interpretations WHERE item_id='item-1'").fetchone()

    assert summary.processed == 0
    assert summary.errors == 1
    assert row["save_decision"] == 0
    assert row["kb_synced"] == 0
    assert "boom" in row["error"]


def test_interpret_runner_preflight_skip_for_missing_assistant_root(tmp_path: Path) -> None:
    from airadar.interpret.runner import run_interpret

    db_path = _seed_runner_db(tmp_path)
    with _connect(db_path) as conn:
        summary = run_interpret(conn, backfill=True, assistant_root=tmp_path / "missing", tmp_root=tmp_path / "tmp")

    assert summary.skipped is True
    assert summary.processed == 0
    assert "skip" in summary.message.lower()


def test_interpret_cli_exists() -> None:
    from airadar.cli import build_parser

    args = build_parser().parse_args(["interpret", "--backfill"])

    assert args.command == "interpret"
    assert args.backfill is True


def test_pipeline_runs_interpret_after_curate() -> None:
    pipeline = Path("pipeline.sh").read_text(encoding="utf-8")

    assert "run_stage interpret" in pipeline
    assert pipeline.index("run_stage curate") < pipeline.index("run_stage interpret")
