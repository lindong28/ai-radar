from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from airadar import cli
from airadar.admin import wechat_kb
from airadar.admin.wechat_kb import CatalogSnapshot, import_catalog
from airadar.db import get_conn, migrate
from airadar.fetcher.dedup import FetchedItem, wechat_duplicate_id
from airadar.sources.loader import SourceConfig
from airadar.sources.sync import load_enabled_sources_from_db, sync_to_db
from airadar.web.app import create_app
from airadar.wechat_archive import ARCHIVE_SOURCE_ID


def test_catalog_loader_uses_the_versioned_local_offline_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assistant_root = tmp_path / "assistant"
    run_script = assistant_root / "agents/summary-agent/run.sh"
    run_script.parent.mkdir(parents=True)
    run_script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    run_script.chmod(0o755)
    observed: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed.update(command=command, **kwargs)
        stdout = "\n".join(
            [
                json.dumps(
                    {
                        "record_type": "catalog",
                        "schema_version": 1,
                        "user": "dong_lin",
                        "index_rows": 1,
                        "manifest_rows": 1,
                        "vector_rows": 1,
                        "vector_ndim": 2,
                        "vector_dim": 1536,
                        "expected_vector_dim": 1536,
                        "alignment_status": "exact",
                    }
                ),
                json.dumps({"record_type": "article", "schema_version": 1}),
            ]
        )
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(wechat_kb.subprocess, "run", fake_run)

    snapshot = wechat_kb.load_catalog(assistant_root, "dong_lin")

    assert len(snapshot.articles) == 1
    assert observed["command"] == [
        str(run_script),
        "--list-article-records",
        "--user",
        "dong_lin",
    ]
    assert observed["cwd"] == assistant_root
    assert observed["env"]["UV_OFFLINE"] == "1"  # type: ignore[index]
    assert "VIRTUAL_ENV" not in observed["env"]  # type: ignore[operator]


def test_catalog_loader_rejects_a_non_exact_header(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assistant_root = tmp_path / "assistant"
    run_script = assistant_root / "agents/summary-agent/run.sh"
    run_script.parent.mkdir(parents=True)
    run_script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    run_script.chmod(0o755)
    header = {
        "record_type": "catalog",
        "schema_version": 1,
        "user": "dong_lin",
        "index_rows": 1,
        "manifest_rows": 0,
        "vector_rows": 1,
        "vector_ndim": 2,
        "vector_dim": 1536,
        "expected_vector_dim": 1536,
        "alignment_status": "mismatch",
    }
    article = {"record_type": "article", "schema_version": 1}
    monkeypatch.setattr(
        wechat_kb.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [],
            0,
            stdout=f"{json.dumps(header)}\n{json.dumps(article)}\n",
            stderr="",
        ),
    )

    with pytest.raises(ValueError, match="not an exact aligned snapshot"):
        wechat_kb.load_catalog(assistant_root, "dong_lin")


def test_catalog_paths_must_be_absolute(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be absolute"):
        wechat_kb._safe_catalog_path(tmp_path, "data/summary_agent/articles/example.md")


def test_admin_sources_list_remains_reachable_after_wechat_kb_routing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO sources (id, name, url, tier, enabled, meta_json, synced_at, kind)
            VALUES ('example', 'Example', 'https://example.com/feed', 'T2', 1, '{}',
                    '2026-08-31T00:00:00Z', 'feed')
            """
        )
        conn.commit()
    monkeypatch.setattr(cli.db, "get_conn", lambda: get_conn(db_path))

    args = cli.build_parser().parse_args(["admin", "sources", "list"])

    assert cli._admin(args) == 0
    assert capsys.readouterr().out == "example\tT2\t1\thttps://example.com/feed\n"


def test_wechat_kb_cli_explains_batch_scope_and_skipped_reasons(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    receipt = wechat_kb.ImportReceipt(
        run_id="wechat-kb-example",
        dry_run=False,
        catalog_articles=10,
        eligible=3,
        imported=1,
        skipped=2,
        remaining=2,
        changed=True,
        postcheck="passed",
    )
    receipt.skipped_reasons.update(
        {
            "article_metadata_or_date_invalid": 1,
            "canonical_url_mismatch": 1,
            "not_wechat_article": 1,
        }
    )
    monkeypatch.setattr(cli, "import_catalog", lambda *_args, **_kwargs: receipt)
    args = cli.build_parser().parse_args(
        [
            "admin",
            "wechat-kb",
            "import",
            "--assistant-root",
            str(tmp_path / "assistant"),
            "--user",
            "dong_lin",
            "--db-path",
            str(db_path),
        ]
    )

    assert cli._admin(args) == 0
    output = capsys.readouterr().out
    assert "WeChat KB import: BATCH COMPLETE (more eligible articles remain)" in output
    assert "article metadata or date invalid: 1" in output
    assert "catalog URL and canonical URL disagree: 1" in output
    assert "not a WeChat article URL: 1" in output
    assert "Next: rerun import to process the remaining eligible articles" in output


def test_wechat_kb_cli_does_not_offer_success_batch_rollback() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["admin", "wechat-kb", "rollback", "--run-id", "example"])


def test_wechat_kb_cli_explains_what_to_do_when_every_candidate_was_skipped(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    receipt = wechat_kb.ImportReceipt(
        run_id="wechat-kb-skipped",
        dry_run=False,
        catalog_articles=1,
        eligible=0,
        imported=0,
        skipped=1,
        changed=False,
        postcheck="passed",
    )
    receipt.skipped_reasons["canonical_url_mismatch"] = 1
    monkeypatch.setattr(cli, "import_catalog", lambda *_args, **_kwargs: receipt)
    args = cli.build_parser().parse_args(
        ["admin", "wechat-kb", "import", "--db-path", str(db_path)]
    )

    assert cli._admin(args) == 0
    output = capsys.readouterr().out
    assert "catalog URL and canonical URL disagree: 1" in output
    assert "Next: review skipped reasons; correct source records you expected to import, then rerun" in output
    assert "search /wechat for an imported title" not in output


def _snapshot(assistant_root: Path, *, url: str = "https://mp.weixin.qq.com/s/seedance-story") -> CatalogSnapshot:
    article_path = assistant_root / "data/summary_agent/articles/seedance-story.md"
    summary_path = assistant_root / "data/summary_agent/dong_lin/article_summaries/seedance-story_output.md"
    article_path.parent.mkdir(parents=True)
    summary_path.parent.mkdir(parents=True)
    article_path.write_text(
        """# 我手搓了一个 Seedance 2.0 分镜 Skill

**作者**: AI寒武纪
**发布时间**: 2026-02-10 11:03:04
**来源**: https://mp.weixin.qq.com/s/seedance-story

这是完整实测正文。
""",
        encoding="utf-8",
    )
    summary_path.write_text(
        """### 📋 文章概况
这篇文章介绍 Seedance 分镜提示词的实践方法。

### 📊 价值判断
**推荐等级**: 值得一看
""",
        encoding="utf-8",
    )
    record = {
        "record_type": "article",
        "schema_version": 1,
        "user": "dong_lin",
        "kb_slug": "seedance-story",
        "title": "Seedance catalog title",
        "url": url,
        "canonical_url": url,
        "source": "AI寒武纪",
        "saved_at": "2026-02-28 22:30",
        "tags": ["视频生成", "Skill管理"],
        "keywords": ["Seedance2.0"],
        "article_file_path": str(article_path),
        "summary_file_path": str(summary_path),
        "entry_status": "ok",
        "file_status": "ok",
        "vector_status": "ok",
    }
    return CatalogSnapshot(
        header={"record_type": "catalog", "schema_version": 1, "index_rows": 1},
        articles=(record,),
    )


def _loader(snapshot: CatalogSnapshot):  # noqa: ANN202
    return lambda _root, _user: snapshot


def test_import_rejects_a_canonical_url_that_does_not_match_the_catalog_url(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.db"
    assistant_root = tmp_path / "assistant"
    migrate(db_path)
    original = _snapshot(assistant_root)
    record = dict(original.articles[0])
    record["canonical_url"] = "https://mp.weixin.qq.com/s/different-article"
    snapshot = CatalogSnapshot(header=original.header, articles=(record,))

    with get_conn(db_path) as conn:
        receipt = import_catalog(
            conn,
            assistant_root=assistant_root,
            user="dong_lin",
            dry_run=False,
            limit=None,
            catalog_loader=_loader(snapshot),
        )

        assert receipt.imported == 0
        assert receipt.skipped_reasons == {"canonical_url_mismatch": 1}
        assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0


def test_import_is_dry_run_safe_idempotent_visible_and_searchable(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.db"
    assistant_root = tmp_path / "assistant"
    migrate(db_path)
    snapshot = _snapshot(assistant_root)

    with get_conn(db_path) as conn:
        dry_run = import_catalog(
            conn,
            assistant_root=assistant_root,
            user="dong_lin",
            dry_run=True,
            limit=None,
            catalog_loader=_loader(snapshot),
        )
        assert dry_run.eligible == 1
        assert dry_run.imported == 0
        assert conn.execute("SELECT 1 FROM sources WHERE id=?", (ARCHIVE_SOURCE_ID,)).fetchone() is None

        receipt = import_catalog(
            conn,
            assistant_root=assistant_root,
            user="dong_lin",
            dry_run=False,
            limit=None,
            catalog_loader=_loader(snapshot),
        )
        assert receipt.imported == 1
        assert receipt.postcheck == "passed"
        assert receipt.changed is True
        source = conn.execute("SELECT enabled, kind, url FROM sources WHERE id=?", (ARCHIVE_SOURCE_ID,)).fetchone()
        assert tuple(source) == (0, "wechat", "internal://ai-assistant-kb")
        extra = json.loads(conn.execute("SELECT extra_json FROM items").fetchone()[0])
        assert extra == {
            "import_run_id": receipt.run_id,
            "kb_slug": "seedance-story",
            "origin": "ai_assistant_kb_archive",
            "published_at_basis": "article_header",
            "upstream_canonical_url": "https://mp.weixin.qq.com/s/seedance-story",
        }

        rerun = import_catalog(
            conn,
            assistant_root=assistant_root,
            user="dong_lin",
            dry_run=False,
            limit=None,
            catalog_loader=_loader(snapshot),
        )
        assert rerun.imported == 0
        assert rerun.already_present == 1
        assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1

    client = TestClient(create_app(db_path))
    listing = client.get("/api/v1/wechat", params={"q": "Seedance 分镜 实测"}).json()["data"]
    assert listing["total"] == 1
    assert listing["items"][0]["slug"] == "seedance-story"
    detail = client.get("/wechat/seedance-story")
    assert detail.status_code == 200
    assert "我手搓了一个 Seedance 2.0 分镜 Skill" in detail.text
    assert ARCHIVE_SOURCE_ID not in {row["id"] for row in client.get("/api/v1/sources").json()["data"]["sources"]}
    v2 = client.get("/api/v2/sources").json()["data"]
    assert ARCHIVE_SOURCE_ID not in {row["id"] for row in v2["sources"]}
    assert v2["counts"]["enabled_loaded_source_count"] == 0


def test_archive_item_participates_in_cross_source_wechat_dedup(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.db"
    assistant_root = tmp_path / "assistant"
    migrate(db_path)
    snapshot = _snapshot(assistant_root)
    with get_conn(db_path) as conn:
        import_catalog(
            conn,
            assistant_root=assistant_root,
            user="dong_lin",
            dry_run=False,
            limit=None,
            catalog_loader=_loader(snapshot),
        )
        conn.execute(
            """
            INSERT INTO sources (id, name, url, tier, enabled, meta_json, synced_at, kind)
            VALUES ('wx_live', 'Live', 'https://example.com/feed', 'T2', 1, '{}',
                    '2026-08-31T00:00:00Z', 'wechat')
            """
        )
        duplicate = wechat_duplicate_id(
            conn,
            FetchedItem(
                source_id="wx_live",
                url="https://mp.weixin.qq.com/s/another-url",
                title="我手搓了一个 Seedance 2.0 分镜 Skill",
                author="AI寒武纪",
                published_at="2026-02-10T03:05:00Z",
                fetched_at="2026-08-31T00:00:00Z",
                content_text="different provider body",
            ),
        )
        assert duplicate is not None
        assert duplicate.startswith("kb-")


def test_paused_mp2rss_history_still_deduplicates_active_wechat_source(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO sources (id, name, url, tier, enabled, paused, meta_json, synced_at, kind)
            VALUES ('wx_mp2rss', 'Mp2RSS', 'https://example.com/mp.xml', 'T2', 1, 1, '{}',
                    '2026-09-04T00:00:00Z', 'wechat')
            """
        )
        conn.execute(
            """
            INSERT INTO sources (id, name, url, tier, enabled, paused, meta_json, synced_at, kind)
            VALUES ('wx_wechat2rss', 'Wechat2RSS', 'https://example.com/new.xml', 'T2', 1, 0, '{}',
                    '2026-09-04T00:00:00Z', 'wechat')
            """
        )
        conn.execute(
            """
            INSERT INTO items (
              id, source_id, url, title, author, published_at, fetched_at,
              content_text, content_html, content_hash, extra_json
            ) VALUES (
              'old-anchor', 'wx_mp2rss', 'https://mp.weixin.qq.com/s/old', '同一篇文章',
              '测试号', '2026-09-04T01:00:00Z', '2026-09-04T01:01:00Z',
              'old body', NULL, 'old-hash', '{}'
            )
            """
        )
        candidate = FetchedItem(
            source_id="wx_wechat2rss",
            url="https://mp.weixin.qq.com/s/new-provider-url",
            title="同一篇文章",
            author="测试号",
            published_at="2026-09-04T01:02:00Z",
            fetched_at="2026-09-04T02:00:00Z",
            content_text="different body",
        )

        assert wechat_duplicate_id(conn, candidate) == "old-anchor"
        conn.execute("UPDATE sources SET enabled=0 WHERE id='wx_mp2rss'")
        assert wechat_duplicate_id(conn, candidate) is None


def test_source_reload_keeps_archive_disabled_and_out_of_runtime_source_loading(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.db"
    assistant_root = tmp_path / "assistant"
    migrate(db_path)
    snapshot = _snapshot(assistant_root)
    with get_conn(db_path) as conn:
        import_catalog(
            conn,
            assistant_root=assistant_root,
            user="dong_lin",
            dry_run=False,
            limit=None,
            catalog_loader=_loader(snapshot),
        )
        sync_to_db(
            [
                SourceConfig(
                    slug="regular",
                    name="Regular",
                    url="https://example.com/feed",
                    tier="T2",
                    enabled=True,
                    kind="feed",
                )
            ],
            conn,
        )

        archive = conn.execute(
            "SELECT enabled, kind, url FROM sources WHERE id=?",
            (ARCHIVE_SOURCE_ID,),
        ).fetchone()
        assert tuple(archive) == (0, "wechat", "internal://ai-assistant-kb")
        assert [source.slug for source in load_enabled_sources_from_db(conn)] == ["regular"]


def test_existing_item_without_interpretation_is_reported_and_not_mutated(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.db"
    assistant_root = tmp_path / "assistant"
    migrate(db_path)
    snapshot = _snapshot(assistant_root)
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO sources (id, name, url, tier, enabled, meta_json, synced_at, kind)
            VALUES ('wx_live', 'Live', 'https://example.com/feed', 'T2', 1, '{}',
                    '2026-08-31T00:00:00Z', 'wechat')
            """
        )
        conn.execute(
            """
            INSERT INTO items (
              id, source_id, url, title, author, published_at, fetched_at,
              content_text, content_hash, extra_json
            ) VALUES ('existing', 'wx_live', 'https://mp.weixin.qq.com/s/seedance-story?scene=1',
                      'Existing', 'AI寒武纪', '2026-02-10T11:03:04Z',
                      '2026-08-31T00:00:00Z', 'body', 'hash', '{}')
            """
        )
        conn.commit()

        receipt = import_catalog(
            conn,
            assistant_root=assistant_root,
            user="dong_lin",
            dry_run=False,
            limit=None,
            catalog_loader=_loader(snapshot),
        )

        assert receipt.existing_without_interpretation == 1
        assert receipt.imported == 0
        assert conn.execute("SELECT COUNT(*) FROM wechat_interpretations").fetchone()[0] == 0


def test_failed_postcheck_rolls_back_every_imported_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "radar.db"
    assistant_root = tmp_path / "assistant"
    migrate(db_path)
    snapshot = _snapshot(assistant_root)
    monkeypatch.setattr(wechat_kb, "_postcheck", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

    with get_conn(db_path) as conn, pytest.raises(RuntimeError, match="boom"):
        import_catalog(
            conn,
            assistant_root=assistant_root,
            user="dong_lin",
            dry_run=False,
            limit=None,
            catalog_loader=_loader(snapshot),
        )

    with get_conn(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM sources WHERE id=?", (ARCHIVE_SOURCE_ID,)).fetchone()[0] == 0
