"""The outbound-link edge that replaced the reverse related-discussions scan.

The reverse lookup's failure mode is that it returns *fewer* rows and nothing
raises -- a reader sees a missing "关联讨论 N 条" badge and cannot tell it from an
article that genuinely has none. So most of these assert that a specific link
is still found, and several carry a negative control showing the assertion can
fail when the mechanism is wrong.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from airadar import db
from airadar.presentation import links


def _migrated(tmp_path: Path) -> sqlite3.Connection:
    path = tmp_path / "links.db"
    db.migrate(path)
    return db.get_conn(path)


def _seed_item(conn: sqlite3.Connection, item_id: str, url: str, content: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO sources (id, name, url, tier, enabled, kind, meta_json, synced_at)"
        " VALUES ('s', 'Source', 'https://example.invalid/s', 'T1', 1, 'feed', '{}', '2026-08-20T00:00:00Z')"
    )
    conn.execute(
        """
        INSERT INTO items (
          id, source_id, url, title, author, published_at, fetched_at,
          content_text, content_html, content_hash, extra_json
        ) VALUES (?, 's', ?, ?, 'A', '2026-08-20T00:00:00Z', '2026-08-20T00:00:00Z',
                  ?, NULL, ?, '{}')
        """,
        (item_id, url, item_id, content, f"hash-{item_id}"),
    )


# --- prefix_successor -------------------------------------------------------


def test_prefix_successor_covers_supplementary_plane() -> None:
    """The bound must sort above URLs continuing past the BMP.

    `prefix + '\\uffff'` looks right and is wrong: U+FFFF is not the largest
    code point, so any continuation in a supplementary plane sorts above it and
    falls outside the range.
    """
    prefix = "https://a/b"
    emoji_url = prefix + "\U0001f600"

    assert emoji_url < links.prefix_successor(prefix)
    # Negative control: the plausible-but-wrong bound excludes it, which is the
    # defect this function exists to avoid. If this stops holding, the test
    # above has stopped discriminating.
    assert not emoji_url < prefix + "￿"


def test_prefix_successor_steps_over_surrogates() -> None:
    successor = links.prefix_successor("x퟿")
    assert successor is not None
    assert ord(successor[-1]) == 0xE000
    successor.encode("utf-8")  # would raise for a lone surrogate


def test_prefix_successor_carries_and_can_be_unbounded() -> None:
    max_char = chr(0x10FFFF)
    assert links.prefix_successor("a" + max_char) == "b"
    # Nothing sorts above an all-max string: the caller must drop the bound
    # rather than be handed one that excludes real rows.
    assert links.prefix_successor(max_char * 3) is None


def test_prefix_successor_of_empty_is_unbounded() -> None:
    assert links.prefix_successor("") is None


# --- maintenance ------------------------------------------------------------


def test_replace_item_links_drops_links_the_new_text_no_longer_has(tmp_path: Path) -> None:
    with _migrated(tmp_path) as conn:
        _seed_item(conn, "i1", "https://example.invalid/1", "see https://a/one and https://a/two")
        links.replace_item_links(conn, "i1", "see https://a/one and https://a/two")
        assert {r[0] for r in conn.execute("SELECT linked_url FROM item_links")} == {
            "https://a/one",
            "https://a/two",
        }

        links.replace_item_links(conn, "i1", "now only https://a/one")
        assert {r[0] for r in conn.execute("SELECT linked_url FROM item_links")} == {"https://a/one"}


def test_deleting_an_item_removes_its_links(tmp_path: Path) -> None:
    with _migrated(tmp_path) as conn:
        _seed_item(conn, "i1", "https://example.invalid/1", "see https://a/one")
        links.replace_item_links(conn, "i1", "see https://a/one")
        conn.execute("DELETE FROM items WHERE id = 'i1'")
        assert conn.execute("SELECT COUNT(*) FROM item_links").fetchone()[0] == 0


def test_renaming_an_item_id_carries_its_links(tmp_path: Path) -> None:
    with _migrated(tmp_path) as conn:
        _seed_item(conn, "i1", "https://example.invalid/1", "see https://a/one")
        links.replace_item_links(conn, "i1", "see https://a/one")
        conn.execute("UPDATE items SET id = 'i2' WHERE id = 'i1'")
        assert [r[0] for r in conn.execute("SELECT item_id FROM item_links")] == ["i2"]


def test_upsert_item_maintains_links_on_insert_and_on_content_change(tmp_path: Path) -> None:
    """The link set has to follow content_text through the real write path.

    Asserted through `upsert_item` rather than by calling `replace_item_links`
    directly: the thing that can break is a write branch forgetting to call it,
    which a direct test would not notice.
    """
    from airadar.fetcher.dedup import FetchedItem, upsert_item

    with _migrated(tmp_path) as conn:
        conn.execute(
            "INSERT INTO sources (id, name, url, tier, enabled, kind, meta_json, synced_at)"
            " VALUES ('s', 'Source', 'https://example.invalid/s', 'T1', 1, 'feed', '{}', '2026-08-20T00:00:00Z')"
        )
        item = FetchedItem(
            source_id="s",
            url="https://example.invalid/post",
            title="Post",
            author="A",
            published_at="2026-08-20T00:00:00Z",
            fetched_at="2026-08-20T00:00:00Z",
            content_text="cites https://a/one",
            content_html=None,
        )
        assert upsert_item(conn, item) is True
        assert {r[0] for r in conn.execute("SELECT linked_url FROM item_links")} == {"https://a/one"}

        revised = FetchedItem(**{**item.__dict__, "content_text": "now cites https://a/two"})
        upsert_item(conn, revised)
        assert {r[0] for r in conn.execute("SELECT linked_url FROM item_links")} == {"https://a/two"}


# --- backfill ledger --------------------------------------------------------


def test_links_are_not_trusted_until_the_backfill_says_complete(tmp_path: Path) -> None:
    with _migrated(tmp_path) as conn:
        assert links.links_ready(conn) is False
        _seed_item(conn, "i1", "https://example.invalid/1", "see https://a/one")
        conn.commit()
        links.backfill_item_links(conn)
        assert links.links_ready(conn) is True


def test_backfill_resumes_from_its_cursor_instead_of_restarting(tmp_path: Path) -> None:
    with _migrated(tmp_path) as conn:
        for n in range(5):
            _seed_item(conn, f"i{n}", f"https://example.invalid/{n}", f"see https://a/{n}")
        conn.commit()
        links.backfill_item_links(conn, batch_size=2)
        assert conn.execute("SELECT COUNT(*) FROM item_links").fetchone()[0] == 5

        # A resumed run starts past the recorded cursor. Pointing the cursor at
        # the last id means a fresh run has nothing left to do -- if it ignored
        # the cursor it would re-derive all five and this would still pass, so
        # the discriminating part is the row that gets added below.
        conn.execute("DELETE FROM item_links")
        conn.execute(
            "UPDATE item_links_backfill SET backfilled_through_id = 'i4', completed_at = NULL"
        )
        conn.commit()
        links.backfill_item_links(conn)
        assert conn.execute("SELECT COUNT(*) FROM item_links").fetchone()[0] == 0

        _seed_item(conn, "i9", "https://example.invalid/9", "see https://a/9")
        conn.commit()
        conn.execute(
            "UPDATE item_links_backfill SET backfilled_through_id = 'i4', completed_at = NULL"
        )
        conn.commit()
        links.backfill_item_links(conn)
        assert {r[0] for r in conn.execute("SELECT linked_url FROM item_links")} == {"https://a/9"}


def test_backfill_locks_out_concurrent_writers_for_the_whole_batch(tmp_path: Path) -> None:
    """The batch's read and its writes must be one step against the ingest path.

    In production the pipeline writes while this runs. If the batch fetched
    prose outside a write transaction, an article refetched in between would
    get fresh links from `upsert_item` and then have them overwritten with the
    older prose this loop is holding -- and the ledger would still say
    complete. Nothing raises; the article just stops showing its citations.

    Asserted by having a second connection with no busy timeout try to write
    while a batch is open: it must be refused. The control at the end -- the
    same write succeeding once the backfill has finished -- is what shows the
    refusal came from the batch's lock and not from a connection that could
    never write in the first place.
    """
    path = tmp_path / "links.db"
    db.migrate(path)
    outcomes: list[str] = []

    with db.get_conn(path) as conn:
        for n in range(4):
            _seed_item(conn, f"i{n}", f"https://example.invalid/{n}", f"see https://a/{n}")
        conn.commit()

        rival = sqlite3.connect(path, timeout=0)
        rival.execute("PRAGMA busy_timeout=0")

        def probe_during_batch(statement: str) -> None:
            # Fire once, after the batch has begun writing.
            if outcomes or not statement.lstrip().upper().startswith("DELETE FROM ITEM_LINKS"):
                return
            try:
                rival.execute("UPDATE items SET content_text='hijacked' WHERE id='i0'")
                rival.commit()
                outcomes.append("wrote")
            except sqlite3.OperationalError as exc:
                outcomes.append(f"refused: {exc}")

        conn.set_trace_callback(probe_during_batch)
        links.backfill_item_links(conn, batch_size=4)
        conn.set_trace_callback(None)

        assert outcomes, "the probe never ran; the batch did not delete any links"
        assert outcomes[0].startswith("refused"), (
            f"a concurrent write got through during the batch: {outcomes[0]}"
        )

        # Control: the same write outside the batch succeeds, so the refusal
        # above was the lock and not a permanently unusable connection.
        rival.execute("UPDATE items SET content_text='after' WHERE id='i0'")
        rival.commit()
        rival.close()


def test_links_ready_is_false_on_a_schema_without_the_ledger() -> None:
    conn = sqlite3.connect(":memory:")
    assert links.links_ready(conn) is False
    conn.close()


# --- reverse lookup ---------------------------------------------------------


def test_citing_item_ids_finds_links_written_with_a_tracking_suffix(tmp_path: Path) -> None:
    with _migrated(tmp_path) as conn:
        conn.execute(
            "INSERT INTO item_links VALUES ('citer', 'https://example.invalid/post?utm_source=x')"
        )
        target = "https://example.invalid/post"
        assert links.citing_item_ids(conn, [target]) == {"citer"}
        # Negative control: an unrelated target finds nothing, so the assertion
        # above is not simply matching everything.
        assert links.citing_item_ids(conn, ["https://example.invalid/other"]) == set()


def test_citing_item_ids_issues_one_statement_for_the_whole_page(tmp_path: Path) -> None:
    """One statement for a page-sized call, not one per URL.

    `/` hands this 40 URLs. A range query each would be 40 statements and would
    still return the right answer, which is why only a count catches it.
    (Not an ADR-004 requirement -- that ADR is about the timeline route's
    enrichment queries and left curated on its N+1.)
    """
    with _migrated(tmp_path) as conn:
        urls = [f"https://example.invalid/{n}" for n in range(40)]
        statements: list[str] = []
        conn.set_trace_callback(statements.append)
        links.citing_item_ids(conn, urls)
        assert sum("FROM item_links l" in statement for statement in statements) == 1
