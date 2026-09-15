"""Independent collection and transactional, at-least-once delivery.

The outbox is not an evaluation dataset. Only completed raw captures qualify
for the existing raw-data validation/freeze path.
"""
from __future__ import annotations

import fcntl
import gzip
import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path

from .. import db
from ..sources.loader import SourceConfig
from ..sources.sync import _load_sources_from_db
from ..sources.x_state import validate_x_runtime_meta
from .dedup import FetchedItem, upsert_item
from .raw_capture import RawCapture, configuration_digest


@contextmanager
def lock(path: Path, inherited_fd: int | None = None):
    """Reuse only a verified inherited open description, otherwise acquire ours."""
    path.parent.mkdir(parents=True, exist_ok=True)
    inherited = False
    if inherited_fd is not None:
        try:
            descriptor = os.fstat(inherited_fd)
            target = path.stat()
            if (descriptor.st_dev, descriptor.st_ino) == (target.st_dev, target.st_ino):
                fcntl.flock(inherited_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                inherited = True
        except (OSError, ValueError):
            pass
    if inherited:
        yield
        return
    with path.open("a+b") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def identity(conn: sqlite3.Connection) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM ingestion_identity WHERE singleton=1").fetchone()
    if row is None:
        raise ValueError("ingestion is not initialized; run ingestion-init under the pipeline lock")
    return row


def initialize(main_path: Path, queue_path: Path, raw_root: Path, sources: Path | None = None) -> None:
    """One-time cutover seed; caller holds both collection and main writer locks."""
    from .runner import reload_sources

    main_path, queue_path, raw_root = main_path.resolve(), queue_path.resolve(), raw_root.resolve()
    if main_path == queue_path or (main_path.exists() and queue_path.exists()
                                   and os.path.samefile(main_path, queue_path)):
        raise ValueError("collection database must differ from the main database")
    db.migrate(main_path)
    db.migrate(queue_path)
    main, queue = db.get_conn(main_path), db.get_conn(queue_path)
    try:
        with main:
            main.execute("INSERT OR IGNORE INTO ingestion_identity(singleton,database_id) VALUES(1,?)",
                         (str(uuid.uuid4()),))
        main_id = identity(main)["database_id"]
        existing = queue.execute("SELECT * FROM ingestion_identity").fetchone()
        if existing is not None:
            if (existing["target_id"], existing["target_path"], existing["raw_root"]) != (
                    main_id, str(main_path), str(raw_root)):
                raise ValueError("queue is already bound to a different target or raw root")
            return
        if queue.execute("SELECT count(*) FROM items").fetchone()[0]:
            raise ValueError("refusing to initialize an existing non-queue article database")
        reload_sources(queue, sources)
        configured = {s.slug: s for s in _load_sources_from_db(queue, where_sql="1=1")}
        with queue:
            for source in _load_sources_from_db(main, where_sql="1=1"):
                current = configured.get(source.slug)
                if current is None or configuration_digest(source) != configuration_digest(current):
                    continue
                queue.execute("UPDATE sources SET meta_json=? WHERE id=?",
                              (json.dumps(source.meta, ensure_ascii=False), source.slug))
                if source.kind == "wechat":
                    for row in main.execute("SELECT * FROM items WHERE source_id=?", (source.slug,)):
                        upsert_item(queue, FetchedItem(
                            source.slug, row["url"], row["title"], row["author"], row["published_at"],
                            row["fetched_at"], row["content_text"], row["content_html"],
                            json.loads(row["extra_json"] or "{}")), wechat=True)
            for row in main.execute("SELECT * FROM wechat_account_avatars"):
                queue.execute("INSERT OR REPLACE INTO wechat_account_avatars VALUES(?,?,?,?)", tuple(row))
            queue.execute("INSERT INTO ingestion_identity VALUES(1,?,?,?,?)",
                          (str(uuid.uuid4()), main_id, str(main_path), str(raw_root)))
    finally:
        main.close()
        queue.close()


class Outbox:
    @staticmethod
    def _insert(conn: sqlite3.Connection, run_id: str, value: dict) -> None:
        payload = gzip.compress(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":")).encode(), mtime=0)
        conn.execute("INSERT INTO ingestion_outbox(run_id,payload,sha256) VALUES(?,?,?)",
                     (run_id, payload, hashlib.sha256(payload).hexdigest()))

    def record(self, conn: sqlite3.Connection, source: SourceConfig,
               items: list[FetchedItem], run_id: str) -> None:
        # Read this transaction's runtime, not a later mutable collector snapshot.
        metadata = json.loads(conn.execute("SELECT meta_json FROM sources WHERE id=?",
                                           (source.slug,)).fetchone()[0])
        if source.kind == "x" and metadata.get("adapter") == "x_api":
            validate_x_runtime_meta(metadata, context=source.slug)
        avatars = ([dict(row) for row in conn.execute("SELECT * FROM wechat_account_avatars")]
                   if source.kind == "wechat" else [])
        self._insert(conn, run_id, {"format": "ingestion_batch_v1", "kind": "source",
                                   "source": asdict(replace(source, meta=metadata)),
                                   "items": [asdict(item) for item in items], "avatars": avatars})

    def finish(self, conn: sqlite3.Connection, archive: RawCapture, failed: int) -> None:
        self._insert(conn, archive.run_id, {"format": "ingestion_batch_v1", "kind": "round",
                                           "completed_at": archive.manifest["completed_at"],
                                           "failed": failed})
        conn.commit()


def collect(main_path: Path, queue_path: Path, raw_root: Path, sources: Path | None = None):
    from .runner import fetch_all

    with lock(queue_path.with_suffix(".collector.lock")):
        with db.get_conn(queue_path) as conn:
            binding = identity(conn)
            if (binding["target_path"], binding["raw_root"]) != (
                    str(main_path.resolve()), str(raw_root.resolve())):
                raise ValueError("collector target/raw root differs from initialized queue")
        return fetch_all(sources, db_path=queue_path, outbox=Outbox(), raw_root=raw_root)


def _apply(conn: sqlite3.Connection, value: dict) -> str | None:
    if value.get("format") != "ingestion_batch_v1":
        raise ValueError("unsupported ingestion batch format")
    if value.get("kind") == "round":
        return value["completed_at"] if value["failed"] == 0 else None
    if value.get("kind") != "source":
        raise ValueError("unsupported ingestion batch kind")
    source = SourceConfig(**value["source"])
    current = next((s for s in _load_sources_from_db(conn, where_sql="1=1") if s.slug == source.slug), None)
    if current is None or configuration_digest(current) != configuration_digest(source):
        raise ValueError(f"queued source configuration differs: {source.slug}; batch retained")
    items = [FetchedItem(**row) for row in value["items"]]
    if any(item.source_id != source.slug for item in items):
        raise ValueError("batch source/item identity mismatch")
    if source.kind == "x" and source.meta.get("adapter") == "x_api":
        validate_x_runtime_meta(source.meta, context=source.slug)
    for item in items:
        upsert_item(conn, item, wechat=source.kind == "wechat")
    conn.execute("UPDATE sources SET meta_json=? WHERE id=?",
                 (json.dumps(source.meta, ensure_ascii=False), source.slug))
    for avatar in value["avatars"]:
        conn.execute("INSERT INTO wechat_account_avatars VALUES(?,?,?,?) "
                     "ON CONFLICT(account) DO UPDATE SET avatar_url=excluded.avatar_url, "
                     "checked_at=excluded.checked_at, updated_at=excluded.updated_at "
                     "WHERE excluded.checked_at >= wechat_account_avatars.checked_at",
                     (avatar["account"], avatar["avatar_url"], avatar["checked_at"], avatar["updated_at"]))
    return None


def _discard(conn: sqlite3.Connection, batch_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM ingestion_outbox WHERE id=?", (batch_id,))


def consume(main_path: Path, queue_path: Path, *, generation: str,
            sources: Path | None = None) -> dict[str, int]:
    """Caller owns the main writer lock. No collector lock is acquired here."""
    from .runner import reload_sources

    main, queue = db.get_conn(main_path), db.get_conn(queue_path)
    applied = repeated = 0
    try:
        target, binding = identity(main), identity(queue)
        if (binding["target_id"], binding["target_path"]) != (
                target["database_id"], str(main_path.resolve())):
            raise ValueError("queue target database identity mismatch")
        reload_sources(main, sources)
        batch_ids = [row[0] for row in queue.execute("SELECT id FROM ingestion_outbox ORDER BY id")]
        for batch_id in batch_ids:
            row = queue.execute("SELECT * FROM ingestion_outbox WHERE id=?", (batch_id,)).fetchone()
            if row is None:
                raise ValueError("pending batch disappeared during consumption")
            if hashlib.sha256(row["payload"]).hexdigest() != row["sha256"]:
                raise ValueError("ingestion payload hash mismatch; batch retained")
            value = json.loads(gzip.decompress(row["payload"]))
            with main:
                ack = main.execute("SELECT sha256 FROM ingestion_acks WHERE queue_id=? AND batch_id=?",
                                   (binding["database_id"], batch_id)).fetchone()
                if ack is not None:
                    if ack[0] != row["sha256"]:
                        raise ValueError("acknowledged batch payload changed")
                    repeated += 1
                else:
                    completed_at = _apply(main, value)
                    main.execute("INSERT INTO ingestion_acks VALUES(?,?,?,?,?)",
                                 (binding["database_id"], batch_id, row["sha256"], generation, completed_at))
                    applied += 1
            # Crash here is safe: the ack prevents replay from overwriting newer main data.
            _discard(queue, batch_id)
        return {"applied_batches": applied, "already_applied_batches": repeated,
                "pending_batches": queue.execute("SELECT count(*) FROM ingestion_outbox").fetchone()[0]}
    finally:
        main.close()
        queue.close()
