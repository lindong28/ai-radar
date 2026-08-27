"""One-time backfill of X tweet media metadata onto already-stored items.

Why this exists: the fetcher only asks X for posts newer than each source's
checkpoint, so turning on media expansions gets media for *new* posts only.
Items already in the database keep an ``extra_json`` without ``x_media``
forever unless they are looked up again by id.

Two invariants this must not break:

* **Checkpoints do not move.** This reads ``/2/tweets?ids=`` directly; it never
  touches ``sources.meta``, so a concurrent or subsequent incremental fetch
  behaves exactly as if the backfill had not run.
* **The receipt reconciles candidates against processed**, not "did anything
  come back". A run that resolves nothing because the API returned nothing and
  a run that resolves nothing because we asked for nothing look identical on a
  count of updated rows alone.

Cost note: X bills per returned Post resource, so a full backfill costs roughly
``candidates x per-post-price``. The caller is told the candidate count before
any request goes out, and ``--limit`` exists to run it in bounded slices.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..egress import selector_httpx_client
from ..fetcher.x_api import (
    X_API_BASE_URL,
    X_MEDIA_FIELDS,
    X_TWEET_FIELDS,
    _media_index,
    _post_media,
)
from ..runtime_env import read_value

# X caps /2/tweets at 100 ids per request.
X_LOOKUP_BATCH = 100
_BEARER_ENV = "X_BEARER_TOKEN"
# The shared connection sets busy_timeout=5000, which is tuned for readers. It
# is not enough here: the 15-minute pipeline holds write transactions on a
# multi-GB database for longer than that, and unlike a reader we have *already
# paid* X for this batch by the time we try to write it. Losing the write means
# paying again for the same posts, so wait a lot longer before giving up.
_WRITE_RETRY_BUDGET_SECONDS = 90.0
_WRITE_RETRY_INITIAL_SLEEP = 0.5


@dataclass
class BackfillReceipt:
    """Expected-vs-actual, so a silent no-op cannot read as success."""

    candidates: int = 0
    requested: int = 0
    returned: int = 0
    with_media: int = 0
    updated: int = 0
    unresolved: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidates": self.candidates,
            "requested": self.requested,
            "returned": self.returned,
            "with_media": self.with_media,
            "updated": self.updated,
            "unresolved_count": len(self.unresolved),
            "unresolved_sample": self.unresolved[:10],
            "reconciled": self.requested == self.returned + len(self.unresolved),
        }


def candidate_rows(conn: sqlite3.Connection, *, limit: int | None = None) -> list[sqlite3.Row]:
    """X items that carry a post id but no media yet.

    ``x_media`` absent is the marker; an item whose tweet genuinely has no
    media gets an empty list written, so it is not revisited on the next run.
    """
    sql = """
        SELECT i.id, i.extra_json
        FROM items i
        JOIN sources s ON s.id = i.source_id
        WHERE COALESCE(s.kind, 'feed') = 'x'
          AND i.extra_json IS NOT NULL
          AND json_extract(i.extra_json, '$.x_post_id') IS NOT NULL
          AND json_extract(i.extra_json, '$.x_media') IS NULL
        ORDER BY i.published_at DESC
    """
    if limit is not None:
        # SQLite reads a negative LIMIT as "no limit", so a slip of the hand
        # (--limit -1) would turn a run meant to cap the bill into one that
        # queries every candidate. Refuse rather than silently uncap.
        if int(limit) < 1:
            raise ValueError(f"limit must be >= 1, got {limit}")
        sql += f" LIMIT {int(limit)}"
    return list(conn.execute(sql))


def _persist(
    conn: sqlite3.Connection,
    updates: list[tuple[str, str]],
    *,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    """Write one batch, waiting out a concurrent writer rather than dropping it.

    Only ``database is locked`` is retried. Any other OperationalError (schema
    mismatch, disk full, corruption) is a real fault and must not be retried
    into a 90-second stall that reports the wrong cause.
    """
    deadline = monotonic() + _WRITE_RETRY_BUDGET_SECONDS
    delay = _WRITE_RETRY_INITIAL_SLEEP
    while True:
        try:
            conn.executemany("UPDATE items SET extra_json = ? WHERE id = ?", updates)
            conn.commit()
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or monotonic() >= deadline:
                raise
            sleep(min(delay, max(0.0, deadline - monotonic())))
            delay = min(delay * 2, 5.0)


def _batches(rows: list[sqlite3.Row]) -> Iterator[list[tuple[str, str, dict[str, Any]]]]:
    batch: list[tuple[str, str, dict[str, Any]]] = []
    for row in rows:
        try:
            extra = json.loads(row["extra_json"])
        except (TypeError, ValueError):
            continue
        post_id = str(extra.get("x_post_id") or "")
        if not post_id:
            continue
        batch.append((row["id"], post_id, extra))
        if len(batch) == X_LOOKUP_BATCH:
            yield batch
            batch = []
    if batch:
        yield batch


def backfill_x_media(
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
    dry_run: bool = False,
    client: httpx.Client | None = None,
) -> BackfillReceipt:
    receipt = BackfillReceipt()
    rows = candidate_rows(conn, limit=limit)
    receipt.candidates = len(rows)
    if dry_run or not rows:
        return receipt

    token = read_value(_BEARER_ENV).strip()
    if not token:
        raise RuntimeError(f"{_BEARER_ENV} is not configured")

    owned = client is None
    http = client or selector_httpx_client(
        callsite_id="admin.x_media_backfill",
        request_url=X_API_BASE_URL,
        timeout=30.0,
    )
    try:
        for batch in _batches(rows):
            ids = [post_id for _item_id, post_id, _extra in batch]
            receipt.requested += len(ids)
            response = http.get(
                f"{X_API_BASE_URL}/2/tweets",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "ids": ",".join(ids),
                    "tweet.fields": X_TWEET_FIELDS,
                    "expansions": "attachments.media_keys",
                    "media.fields": X_MEDIA_FIELDS,
                },
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("invalid X lookup response: payload is not an object")

            index = _media_index(payload)
            posts = payload.get("data")
            by_id = {str(p["id"]): p for p in posts if isinstance(p, dict) and p.get("id")} if isinstance(posts, list) else {}
            receipt.returned += len(by_id)

            updates: list[tuple[str, str]] = []
            for item_id, post_id, extra in batch:
                post = by_id.get(post_id)
                if post is None:
                    # Deleted, protected, or otherwise not returned. Left alone
                    # so a later run can retry it; counted so it is visible.
                    receipt.unresolved.append(post_id)
                    continue
                media = _post_media(post, index)
                extra["x_media"] = media
                if media:
                    receipt.with_media += 1
                updates.append(
                    (json.dumps(extra, ensure_ascii=False, sort_keys=True, separators=(",", ":")), item_id)
                )
            # Count only what actually landed: a receipt claiming `updated` for
            # rows lost to a failed write would send the operator looking for
            # media that is not there, and hide that those posts were billed.
            _persist(conn, updates)
            receipt.updated += len(updates)
    finally:
        if owned:
            http.close()
    return receipt
