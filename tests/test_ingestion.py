from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from airadar import db
from airadar.fetcher import ingestion, runner
from airadar.fetcher.dedup import FetchedItem
from airadar.fetcher.http_client import FeedResponse
from airadar.fetcher.raw_capture import read_run
from airadar.sources.loader import SourceConfig


@pytest.fixture
def setup_queue(tmp_path, monkeypatch):
    main, queue, raw = tmp_path / "main.db", tmp_path / "queue.db", tmp_path / "raw"
    config = tmp_path / "sources.toml"
    config.write_text('[[source]]\nslug="test"\nname="Test"\nurl="https://example.invalid/feed"\ntier="T1"\n')
    source = SourceConfig("test", "Test", "https://example.invalid/feed", "T1")
    monkeypatch.setenv("AI_RADAR_DB", str(main))
    ingestion.initialize(main, queue, raw, config)
    with db.get_conn(main) as conn:
        runner.reload_sources(conn, config)
    return main, queue, raw, config, source


def make_item(source, suffix="one"):
    return FetchedItem(source.slug, f"https://example.invalid/{suffix}", suffix, "author",
                       "2026-09-16T00:00:00Z", "2026-09-16T00:01:00Z", "raw body " * 1000,
                       "<p>raw</p>", {"tags": ["original"]})


def fake_network(monkeypatch, source, items):
    monkeypatch.setattr(runner, "_fetch_source_feed", lambda s: runner._SourceFeedResult(
        s, response=FeedResponse(status_code=200, body=b"", headers={}), items=items))


def test_collection_survives_main_writer_lock(setup_queue, monkeypatch, tmp_path):
    main, queue, raw, config, source = setup_queue
    item = make_item(source)
    fake_network(monkeypatch, source, [item])
    with (tmp_path / ".pipeline.flock").open("a+b") as stream, db.get_conn(main) as busy:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        busy.execute("BEGIN IMMEDIATE")
        summary = ingestion.collect(main, queue, raw, config)
        assert summary.failed == 0 and summary.raw_capture_error is None
        assert busy.execute("SELECT count(*) FROM items").fetchone()[0] == 0
        busy.rollback()
    run = next((raw / "runs").iterdir())
    assert read_run(raw, run.name)[1] == [asdict(item)]
    result = ingestion.consume(main, queue, generation="first", sources=config)
    assert result == {"applied_batches": 2, "already_applied_batches": 0, "pending_batches": 0}
    with db.get_conn(main) as conn:
        assert conn.execute("SELECT content_text FROM items").fetchone()[0] == item.content_text
        assert conn.execute("SELECT count(*) FROM ingestion_acks WHERE completed_run_at IS NOT NULL").fetchone()[0] == 1


def test_raw_finish_crash_does_not_lose_committed_source(setup_queue, monkeypatch):
    main, queue, raw, config, source = setup_queue
    fake_network(monkeypatch, source, [make_item(source)])
    def fail_finish(self):
        raise OSError("injected archive finish failure")
    monkeypatch.setattr(runner.RawCapture, "finish", fail_finish)
    summary = ingestion.collect(main, queue, raw, config)
    assert summary.raw_capture_error == "OSError"
    result = ingestion.consume(main, queue, generation="after-crash", sources=config)
    assert result["applied_batches"] == 1
    with db.get_conn(main) as conn:
        assert conn.execute("SELECT count(*) FROM items").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM ingestion_acks WHERE completed_run_at IS NOT NULL").fetchone()[0] == 0


@pytest.mark.parametrize("via_env", [True, False])
def test_raw_error_reporting_matches_env_and_explicit_path(setup_queue, monkeypatch, via_env):
    main, queue, raw, config, source = setup_queue
    fake_network(monkeypatch, source, [])
    monkeypatch.setenv("AI_RADAR_RAW_CAPTURE_DIR", str(raw))
    def fail_finish(self):
        raise OSError("injected archive finish failure")
    monkeypatch.setattr(runner.RawCapture, "finish", fail_finish)
    summary = runner.fetch_all(config, main, **({} if via_env else {"raw_root": raw}))
    assert summary.raw_capture_error == "OSError"


def test_delivery_failure_rolls_back_items_and_metadata(setup_queue, monkeypatch):
    main, queue, raw, config, source = setup_queue
    item = make_item(source)
    monkeypatch.setattr(runner, "_fetch_source_feed", lambda s: runner._SourceFeedResult(
        s, response=FeedResponse(status_code=200, body=b"", headers={}), items=[item],
        meta_update=runner._SourceMetaUpdate(source.slug, '{"etag":"new"}')))
    def fail_record(*args):
        raise OSError("injected outbox failure")
    monkeypatch.setattr(ingestion.Outbox, "record", fail_record)
    summary = ingestion.collect(main, queue, raw, config)
    assert summary.failed == 1
    with db.get_conn(queue) as conn:
        assert conn.execute("SELECT count(*) FROM items").fetchone()[0] == 0
        assert "etag" not in json.loads(conn.execute("SELECT meta_json FROM sources").fetchone()[0])


def test_ack_then_delete_failure_retries_without_overwriting(setup_queue, monkeypatch):
    main, queue, raw, config, source = setup_queue
    fake_network(monkeypatch, source, [make_item(source)])
    ingestion.collect(main, queue, raw, config)
    discard = ingestion._discard
    monkeypatch.setattr(ingestion, "_discard", lambda *a: (_ for _ in ()).throw(OSError("crash")))
    with pytest.raises(OSError):
        ingestion.consume(main, queue, generation="before-crash", sources=config)
    with db.get_conn(main) as conn:
        conn.execute("UPDATE items SET title='newer main title'")
        conn.commit()
    monkeypatch.setattr(ingestion, "_discard", discard)
    result = ingestion.consume(main, queue, generation="retry", sources=config)
    assert result["already_applied_batches"] == 1 and result["pending_batches"] == 0
    with db.get_conn(main) as conn:
        assert conn.execute("SELECT title FROM items").fetchone()[0] == "newer main title"
    assert ingestion.consume(main, queue, generation="empty", sources=config)["applied_batches"] == 0


@pytest.mark.parametrize("damage", ["payload", "target", "source"])
def test_invalid_delivery_keeps_pending_batch(setup_queue, monkeypatch, damage):
    main, queue, raw, config, source = setup_queue
    fake_network(monkeypatch, source, [make_item(source)])
    ingestion.collect(main, queue, raw, config)
    with db.get_conn(queue) as conn:
        if damage == "payload":
            conn.execute("UPDATE ingestion_outbox SET payload=x'00' WHERE id=1")
        elif damage == "target":
            conn.execute("UPDATE ingestion_identity SET target_id='wrong'")
        conn.commit()
    if damage == "source":
        config.write_text(config.read_text().replace("example.invalid/feed", "other.invalid/feed"))
    with pytest.raises(ValueError):
        ingestion.consume(main, queue, generation="invalid", sources=config)
    with db.get_conn(queue) as conn:
        assert conn.execute("SELECT count(*) FROM ingestion_outbox").fetchone()[0] == 2
    with db.get_conn(main) as conn:
        assert conn.execute("SELECT count(*) FROM items").fetchone()[0] == 0


def test_two_rounds_use_corresponding_metadata_snapshot(setup_queue, monkeypatch):
    main, queue, raw, config, source = setup_queue
    for version in ("first", "second"):
        monkeypatch.setattr(runner, "_fetch_source_feed", lambda s: runner._SourceFeedResult(
            s, response=FeedResponse(status_code=200, body=b"", headers={}),
            items=[make_item(source, version)],
            meta_update=runner._SourceMetaUpdate(source.slug, json.dumps({"etag": version}))))
        ingestion.collect(main, queue, raw, config)
    with db.get_conn(queue) as conn:
        import gzip
        payloads = [json.loads(gzip.decompress(row[0])) for row in conn.execute(
            "SELECT payload FROM ingestion_outbox ORDER BY id")]
    assert [v["source"]["meta"]["etag"] for v in payloads if v["kind"] == "source"] == ["first", "second"]
    ingestion.consume(main, queue, generation="both", sources=config)
    with db.get_conn(main) as conn:
        assert {row[0] for row in conn.execute("SELECT title FROM items")} == {"first", "second"}
        assert json.loads(conn.execute("SELECT meta_json FROM sources").fetchone()[0])["etag"] == "second"


def test_real_rss_http_to_raw_to_main_while_main_is_busy(tmp_path, monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            feed = self.path.strip("/")
            body = (f'<rss version="2.0"><channel><title>{feed}</title><item>'
                    f'<title>{feed} article</title><link>https://example.invalid/{feed}</link>'
                    f'<description>Full {feed} input body</description></item></channel></rss>').encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/rss+xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    main, queue, raw, config = (tmp_path / name for name in ("main.db", "queue.db", "raw", "sources.toml"))
    monkeypatch.setenv("AI_RADAR_SQLITE_BUSY_TIMEOUT_MS", "1")
    config.write_text("\n".join(
        f'[[source]]\nslug="{slug}"\nname="{slug}"\nurl="http://127.0.0.1:{server.server_port}/{slug}"\ntier="T1"'
        for slug in ("first", "second")))
    try:
        ingestion.initialize(main, queue, raw, config)
        with db.get_conn(main) as busy:
            busy.execute("BEGIN IMMEDIATE")
            # Negative path: the old owner cannot even reload sources under this write lock.
            with pytest.raises(Exception, match="locked"):
                runner.fetch_all(config, db_path=main)
            result = ingestion.collect(main, queue, raw, config)
            assert result.attempted == 2 and result.failed == 0 and not result.raw_capture_error
            busy.rollback()
        run = next((raw / "runs").iterdir())
        rows = read_run(raw, run.name)[1]
        assert {r["title"] for r in rows} == {"first article", "second article"}
        ingestion.consume(main, queue, generation="real-http", sources=config)
        with db.get_conn(main) as conn:
            assert {r[0] for r in conn.execute("SELECT content_text FROM items")} == {
                "Full first input body", "Full second input body"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_abrupt_process_exit_after_source_commit_is_recoverable(setup_queue):
    main, queue, raw, config, source = setup_queue
    code = '''
import os, sys
from pathlib import Path
from airadar.fetcher import ingestion, runner
from airadar.fetcher.dedup import FetchedItem
from airadar.fetcher.http_client import FeedResponse
main, queue, raw, config = map(Path, sys.argv[1:])
def fetch(s):
    return runner._SourceFeedResult(s, response=FeedResponse(200,b""), items=[
        FetchedItem(s.slug,"https://example.invalid/crash","crash",None,
                    "2026-09-16T00:00:00Z","2026-09-16T00:01:00Z","full body")])
runner._fetch_source_feed = fetch
runner.RawCapture.finish = lambda self: os._exit(17)
ingestion.collect(main, queue, raw, config)
'''
    proc = subprocess.run([sys.executable, "-c", code, str(main), str(queue), str(raw), str(config)],
                          env=dict(os.environ), capture_output=True, text=True, timeout=20)
    assert proc.returncode == 17, proc.stderr
    assert ingestion.consume(main, queue, generation="recovery", sources=config)["applied_batches"] == 1
    with db.get_conn(main) as conn:
        assert conn.execute("SELECT title FROM items").fetchone()[0] == "crash"
