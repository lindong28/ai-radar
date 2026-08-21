"""The outbound-link edge behind related discussions.

`related_discussions` asks two questions about one article: *which articles does
this one link to* (forward, answerable from the row's own ``content_text``) and
*which articles link to this one* (reverse). The reverse half used to be asked
as ``content_text LIKE '%url%'`` -- 40 patterns OR-ed together over the whole
FTS content table, measured at 1.008s of `/`'s 1.380s on the serving replica.

``item_links`` stores the same edge as data instead of re-deriving it per
request, so the reverse question becomes an index range scan.

Two properties are load-bearing:

* **Stored values are already normalized** (:func:`clean_url`). Normalizing
  inside the query would put a function on the indexed column and lose the
  index -- the same trap ADR-060 recorded when a ``datetime()`` call took the
  hot-topics window query from 0.54s to 1.77s.
* **Lookup is by prefix, not equality.** A citing article may write the target
  URL with something appended (``https://a/b?utm_source=x``), and the old
  substring match found those. Equality would silently drop them.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime

URL_RE = re.compile(r"https?://[^\s<>)\"']+")

# Highest scalar value Unicode defines. Incrementing past it has no successor,
# which is what makes the carry in prefix_successor necessary rather than
# decorative.
_MAX_CODE_POINT = 0x10FFFF
# Surrogates are not scalar values: chr(0xD800) exists in Python but cannot be
# encoded to UTF-8, so a successor must step over the whole block.
_SURROGATE_START = 0xD800
_SURROGATE_END = 0xDFFF


def clean_url(value: str | None) -> str:
    """Normalize a URL the way the related-discussion comparison expects.

    The normalization expression is unchanged from the pre-existing
    `related._clean_url`, so every input that reached it before gets the same
    answer -- including ``""``, which returned ``""`` there and returns ``""``
    here. The one difference is ``None``: the old function raised
    ``AttributeError`` on it, this one returns ``""``. That equivalence is what
    lets the stored edge and the in-Python confirmation step agree on what "the
    same URL" means.
    """
    if not value:
        return ""
    return value.strip().rstrip(".,;:!?)»”'\"").rstrip("/").lower()


def urls_in_text(value: str | None) -> list[str]:
    """Every distinct outbound URL in ``value``, normalized, in first-seen order."""
    if not value:
        return []
    seen: set[str] = set()
    urls: list[str] = []
    for match in URL_RE.findall(value):
        cleaned = clean_url(match)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            urls.append(cleaned)
    return urls


def prefix_successor(prefix: str) -> str | None:
    """Smallest string that sorts after every string starting with ``prefix``.

    ``None`` means no such string exists -- ``prefix`` is all-max code points,
    so the range is open-ended and the caller must drop the upper bound rather
    than invent one.

    Written out rather than approximated as ``prefix + '\\uffff'``: U+FFFF is
    not the largest code point, so that approximation silently excludes any URL
    continuing into a supplementary plane (an emoji in a path, for one). SQLite
    compares TEXT with BINARY collation, i.e. by UTF-8 bytes, and UTF-8 byte
    order matches code-point order for all scalar values -- so stepping the
    final code point is the correct successor on both sides.
    """
    for index in range(len(prefix) - 1, -1, -1):
        code_point = ord(prefix[index])
        if code_point >= _MAX_CODE_POINT:
            continue  # carry: this position cannot go higher, drop it and try left
        nxt = code_point + 1
        if _SURROGATE_START <= nxt <= _SURROGATE_END:
            nxt = _SURROGATE_END + 1
        return prefix[:index] + chr(nxt)
    return None


def replace_item_links(conn: sqlite3.Connection, item_id: str, content_text: str | None) -> None:
    """Make ``item_links`` match ``content_text`` for one item.

    Delete-then-insert rather than a merge: the set is small (median 0 links,
    30394 edges across 54750 items on the production database) and a merge would
    have to detect removals anyway.
    """
    conn.execute("DELETE FROM item_links WHERE item_id = ?", (item_id,))
    urls = urls_in_text(content_text)
    if not urls:
        return
    conn.executemany(
        "INSERT OR IGNORE INTO item_links (item_id, linked_url) VALUES (?, ?)",
        [(item_id, url) for url in urls],
    )


def links_ready(conn: sqlite3.Connection) -> bool:
    """Whether the backfill has covered every pre-existing item.

    Callers must branch on this. An incomplete ``item_links`` answers the
    reverse question with *fewer* rows, not with an error, so trusting it early
    would drop related discussions silently -- the reader sees a missing badge
    and has no way to tell it apart from an article that genuinely has none.
    """
    try:
        row = conn.execute("SELECT completed_at FROM item_links_backfill WHERE id = 1").fetchone()
    except sqlite3.OperationalError:
        # No ledger table: a connection whose schema predates migration 019.
        # Answering "not ready" routes the caller to the pre-019 scan, which is
        # the behaviour that connection already expects. Deploys cannot reach
        # here silently -- schema_gate.py refuses code whose migrate() declares
        # a table the active database lacks.
        return False
    return bool(row and row[0])


def backfill_item_links(conn: sqlite3.Connection, *, batch_size: int = 500) -> int:
    """Populate ``item_links`` for existing items; resumable, returns rows written.

    Resumable because the source is a 2.7GB database on a 2-vCPU origin and an
    interrupted run must not start over. The cursor is the item id, so a resumed
    run re-processes at most one batch.

    **Each batch reads and writes inside one `BEGIN IMMEDIATE`.** The fetch and
    the writes have to be one atomic step against the ingest path, which is
    running concurrently in production: with the read outside the transaction,
    an article refetched between the two would get its fresh links written by
    `upsert_item` and then overwritten with the prose this loop read a moment
    earlier -- after which the ledger would still be marked complete. Nothing
    raises in that sequence; the article simply stops showing the discussions
    that cite it. `BEGIN IMMEDIATE` takes the write lock up front so the two
    writers serialise instead of interleaving (`upsert_item`'s connection waits
    out its `busy_timeout` rather than racing).

    ``batch_size`` is what that lock costs: it is how long ingest can be made to
    wait. 500 rows is ~0.03s of work on the production snapshot, against a
    pipeline that runs every 15 minutes.
    """
    if conn.in_transaction:
        # This function owns its transaction boundaries -- that is the whole
        # point of the batching above. Committing the caller's open work to get
        # there would silently publish whatever they had staged, so say so
        # instead.
        raise RuntimeError(
            "backfill_item_links needs to control its own transactions; "
            "commit or roll back before calling it"
        )
    row = conn.execute(
        "SELECT backfilled_through_id FROM item_links_backfill WHERE id = 1"
    ).fetchone()
    cursor = row[0] if row else None
    written = 0
    while True:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if cursor is None:
                batch = conn.execute(
                    "SELECT id, content_text FROM items ORDER BY id LIMIT ?",
                    (batch_size,),
                ).fetchall()
            else:
                batch = conn.execute(
                    "SELECT id, content_text FROM items WHERE id > ? ORDER BY id LIMIT ?",
                    (cursor, batch_size),
                ).fetchall()
            if not batch:
                conn.rollback()
                break
            for item_id, content_text in batch:
                replace_item_links(conn, item_id, content_text)
                written += len(urls_in_text(content_text))
                cursor = item_id
            conn.execute(
                "UPDATE item_links_backfill SET backfilled_through_id = ? WHERE id = 1",
                (cursor,),
            )
        except BaseException:
            conn.rollback()
            raise
        conn.commit()
    conn.execute(
        "UPDATE item_links_backfill SET completed_at = ? WHERE id = 1",
        (datetime.now(UTC).isoformat().replace("+00:00", "Z"),),
    )
    conn.commit()
    return written


def citing_item_ids(conn: sqlite3.Connection, urls: list[str]) -> set[str]:
    """Ids of items whose text links to any of ``urls``, by prefix.

    **One statement for the whole page**, not one per URL: `/`'s page carries
    40 URLs, and the obvious implementation -- a range query each -- returns
    the same answer while reintroducing an N+1. The bounds ride in as a VALUES
    co-routine, which SQLite drives against the index -- ``SEARCH l USING
    COVERING INDEX idx_item_links_url (linked_url>? AND linked_url<?)`` on the
    production snapshot.

    (ADR-004 is about the *timeline* route's enrichment queries and explicitly
    decided to leave curated on its N+1; it does not govern this query. The
    single-statement shape here is a choice made for this code, not an existing
    contract being honoured -- an earlier version of this comment claimed
    otherwise.)

    Prefix rather than equality because citing text often appends to the URL it
    quotes. Measured on the production snapshot: of 30394 stored edges, 12913
    resolve to a known item's URL exactly and a further **326 are strict
    extensions** of one. Equality would drop those 326 silently.

    The query those three numbers came from, so the claim stays checkable::

        SELECT COUNT(*) FROM item_links;
        SELECT COUNT(*) FROM item_links l
          JOIN items i ON lower(rtrim(i.url,'/')) = l.linked_url;
        SELECT COUNT(*) FROM item_links l WHERE EXISTS (
          SELECT 1 FROM items i
          WHERE l.linked_url > lower(rtrim(i.url,'/'))
            AND l.linked_url < lower(rtrim(i.url,'/')) || char(1114111)
            AND l.linked_url <> lower(rtrim(i.url,'/')));
    """
    if not urls:
        return set()
    bounds: list[object] = []
    for url in urls:
        bounds.extend((url, prefix_successor(url)))
    values = ", ".join("(?, ?)" for _ in urls)
    rows = conn.execute(
        f"""
        WITH targets(lo, hi) AS (VALUES {values})
        SELECT DISTINCT l.item_id
        FROM item_links l
        JOIN targets t
          ON l.linked_url >= t.lo
         AND (t.hi IS NULL OR l.linked_url < t.hi)
        """,
        bounds,
    ).fetchall()
    return {row[0] for row in rows}
