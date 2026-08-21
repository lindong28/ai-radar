-- Two hot-path costs on `/` and `/all`, both measured on the serving replica
-- (2 vCPU origin, 2.7GB database) before this migration:
--
--   * `SELECT ... FROM curation_runs ORDER BY created_at DESC LIMIT 1` scanned
--     the whole table. curation_runs is only 8235 rows but 688MB -- the two
--     id-list TEXT columns are wide -- so "take the newest row" read 688MB of
--     overflow pages. It runs 3x inside _timeline_data_version() and once in
--     _latest_run(), which is 0.433s of /all's 0.608s and 0.349s of /'s 1.380s.
--
--   * The reverse half of related-discussions ("who links to this item?") had
--     no index to ask, so it ran 40 `content_text LIKE '%url%'` patterns over
--     items_fts -- a full scan of 54750 articles' text, 1.008s of /'s 1.380s.
--     item_links answers the same question from a normalized link edge.

-- created_at alone is not a total order: two runs can share a timestamp, and
-- then "the latest run" differs between callers depending on scan order. The
-- id tie-break is part of the index *and* of every caller's ORDER BY, so all
-- latest-run lookups agree on the same row.
CREATE INDEX IF NOT EXISTS idx_curation_runs_created_at
ON curation_runs(created_at DESC, id DESC);

-- One row per (item, outbound URL). linked_url is stored already normalized by
-- presentation.links.clean_url, so lookups are plain range scans -- putting the
-- normalization in the query instead would defeat the index, which is the trap
-- ADR-060 already recorded once (a datetime() call in WHERE took hours=48 from
-- 0.54s to 1.77s).
CREATE TABLE IF NOT EXISTS item_links (
  item_id TEXT NOT NULL,
  linked_url TEXT NOT NULL,
  PRIMARY KEY (item_id, linked_url)
) WITHOUT ROWID;

-- Covering: the reverse lookup selects only these two columns.
CREATE INDEX IF NOT EXISTS idx_item_links_url
ON item_links(linked_url, item_id);

-- Backfill is Python (extracting URLs from prose needs a regex, which SQLite
-- triggers cannot do), so the schema arriving does not mean the rows have. A
-- half-filled table would silently under-report related discussions -- the
-- failure shows up as "fewer badges", never as an error. Readers consult this
-- ledger and fall back to the old scan until it says complete.
CREATE TABLE IF NOT EXISTS item_links_backfill (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  -- Resume cursor: every item id <= this has been processed. NULL = not started.
  backfilled_through_id TEXT,
  completed_at TEXT
);

INSERT OR IGNORE INTO item_links_backfill (id, backfilled_through_id, completed_at)
VALUES (1, NULL, NULL);

-- Incremental maintenance for inserts and content edits lives in
-- presentation.links.replace_item_links, called in upsert_item's transaction.
-- Deletes and id rewrites are pure key operations with no regex in them, so
-- they stay in the database where no code path can forget them.
DROP TRIGGER IF EXISTS item_links_items_ad;
DROP TRIGGER IF EXISTS item_links_items_au_id;

CREATE TRIGGER item_links_items_ad AFTER DELETE ON items BEGIN
  DELETE FROM item_links WHERE item_id = old.id;
END;

CREATE TRIGGER item_links_items_au_id AFTER UPDATE OF id ON items
WHEN old.id IS NOT new.id BEGIN
  UPDATE item_links SET item_id = new.id WHERE item_id = old.id;
END;
