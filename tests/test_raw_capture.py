from __future__ import annotations

import gzip
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from airadar.fetcher import raw_capture as raw
from airadar.fetcher import runner
from airadar.fetcher.dedup import FetchedItem
from airadar.fetcher.http_client import FeedResponse
from airadar.sources.loader import SourceConfig


@pytest.fixture
def source():
    return SourceConfig(slug="test", name="Test", url="https://example.invalid/feed?token=private",
                        tier=1, meta={"etag": "version-1"})


@pytest.fixture
def item():
    return FetchedItem("test", "https://example.invalid/story", "title", "author",
                       "2026-09-15T00:00:00Z", "2026-09-15T01:00:00Z", "body" * 3000,
                       "<p>full html</p>", {"tag": ["raw"]})


def archive(tmp_path, source):
    return raw.RawCapture(tmp_path, [source], code_root=Path(__file__).resolve().parents[1])


def complete(tmp_path, source, item):
    a = archive(tmp_path, source)
    a.record(source, [item], status="success", response_meta={"etag": "version-1"}, http_status=200)
    a.finish()
    return a


def test_full_input_and_config_privacy(tmp_path, source, item):
    a = complete(tmp_path, source, item)
    manifest, rows = raw.read_run(tmp_path, a.run_id)
    assert rows == [asdict(item)]
    assert len(rows[0]["content_text"]) > 4000
    assert b"private" not in (a.directory / "manifest.json").read_bytes()
    assert manifest["sources"][source.slug]["status"] == "success"


@pytest.mark.parametrize("mutation", ["none", "validator", "config", "payload", "missing"])
def test_304_requires_exact_cached_representation(tmp_path, source, item, mutation):
    first = complete(tmp_path, source, item)
    request = source
    if mutation == "validator":
        request = replace(source, meta={"etag": "version-2"})
    elif mutation == "config":
        request = replace(source, url="https://another.invalid/feed")
    elif mutation == "payload":
        (first.directory / "items.jsonl.gz").write_bytes(gzip.compress(b"corrupt"))
    elif mutation == "missing":
        (first.directory / "items.jsonl.gz").unlink()
    second = archive(tmp_path, request)
    prepared = second.prepare(request)
    assert ("etag" in prepared.meta) == (mutation == "none")
    second.record(prepared, [], status="not_modified", http_status=304)
    second.finish()
    m, rows = raw.read_run(tmp_path, second.run_id)
    assert rows == []
    assert m["sources"][source.slug]["status"] == ("not_modified" if mutation == "none" else "unresolved")
    if mutation == "none":
        assert raw.dependency_closure(tmp_path, {second.run_id}) == {first.run_id, second.run_id}


def test_empty_failure_and_interruption_are_distinct(tmp_path, source):
    first = archive(tmp_path, source)
    with pytest.raises(ValueError, match="incomplete"):
        raw.read_run(tmp_path, first.run_id)
    first.record(source, [], status="failed", http_status=503)
    first.finish()
    second = archive(tmp_path, source)
    second.record(source, [], status="success", http_status=200)
    second.finish()
    assert raw.read_run(tmp_path, first.run_id)[0]["sources"]["test"]["status"] == "failed"
    assert raw.read_run(tmp_path, second.run_id)[0]["sources"]["test"]["status"] == "success"


def test_io_error_cannot_finish_as_success(tmp_path, source, item, monkeypatch):
    a = archive(tmp_path, source)
    monkeypatch.setattr(a, "_save", lambda: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        a.record(source, [item], status="success")
    with pytest.raises(OSError, match="write failed"):
        a.finish()
    a.close()


def test_archive_rejects_concurrent_writer(tmp_path, source):
    first = archive(tmp_path, source)
    with pytest.raises(BlockingIOError):
        archive(tmp_path, source)
    first.close()
    second = archive(tmp_path, source)
    second.close()


def test_retention_and_freeze_keep_old_304_dependency(tmp_path, source, item):
    root = tmp_path / "live"
    first = complete(root, source, item)
    old = datetime.now(UTC) - timedelta(days=40)
    first.manifest["started_at"] = old.isoformat()
    raw._write(first.directory / "manifest.json", first.manifest)
    second = archive(root, source)
    prepared = second.prepare(source)
    second.record(prepared, [], status="not_modified", http_status=304)
    second.finish()
    assert raw.retention_candidates(root, now=datetime.now(UTC)) == []
    frozen = tmp_path / "frozen"
    raw.freeze(root, frozen, {second.run_id})
    assert raw.dependency_closure(frozen, {second.run_id}) == {first.run_id, second.run_id}
    (first.directory / "items.jsonl.gz").unlink()
    assert raw.read_run(frozen, first.run_id)[1] == [asdict(item)]
    with pytest.raises(FileExistsError):
        raw.freeze(frozen, frozen, {second.run_id})


def test_prune_only_old_unreferenced_valid_runs(tmp_path, source, item):
    a = complete(tmp_path, source, item)
    a.manifest["started_at"] = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    raw._write(a.directory / "manifest.json", a.manifest)
    newer = complete(tmp_path, source, replace(item, title="new"))
    assert raw.prune(tmp_path, now=datetime.now(UTC)) == [a.run_id]
    assert not a.directory.exists()
    assert raw.read_run(tmp_path, newer.run_id)[1][0]["title"] == "new"


def test_coverage_missing_slot_failure_and_disabled_period(tmp_path, source, item):
    a = complete(tmp_path, source, item)
    start = datetime.fromisoformat(a.manifest["started_at"])
    kwargs = dict(start=start, enabled_at=start, cadence_seconds=900, source_ids={"test"})
    assert raw.coverage(tmp_path, end=start+timedelta(minutes=15), **kwargs)["complete"]
    result = raw.coverage(tmp_path, end=start+timedelta(minutes=30), **kwargs)
    assert not result["complete"] and result["gaps"][0]["reason"] == "missing_run"
    kwargs["enabled_at"] = start + timedelta(seconds=1)
    assert not raw.coverage(tmp_path, end=start+timedelta(minutes=15), **kwargs)["complete"]
    kwargs["enabled_at"] = start
    kwargs["source_ids"] = {"absent"}
    assert not raw.coverage(tmp_path, end=start+timedelta(minutes=15), **kwargs)["complete"]


@pytest.mark.parametrize("kind", ["rss", "wechat"])
def test_real_apply_archives_after_enrichment_before_upsert(tmp_path, source, item, monkeypatch, kind):
    import sqlite3
    source = replace(source, kind=kind)
    enriched = replace(item, content_text="enriched" * 2000)
    a = archive(tmp_path, source)
    conn = sqlite3.connect(":memory:")
    monkeypatch.setattr(runner, "_enrich_wechat_bodies", lambda conn, rows: [enriched])
    def upsert(conn, observed, **kwargs):
        payload = gzip.decompress((a.directory / "items.jsonl.gz").read_bytes())
        assert json.loads(payload) == asdict(observed)
        return True
    monkeypatch.setattr(runner, "upsert_item", upsert)
    result = runner._apply_source_feed_result(conn, runner._SourceFeedResult(
        source=source, response=FeedResponse(200, b"", headers={"etag": "version-1"}), items=[item]), a)
    assert result.error is None and result.inserted == 1
    a.finish()
    assert raw.read_run(tmp_path, a.run_id)[1] == [asdict(enriched if kind == "wechat" else item)]
    conn.close()
