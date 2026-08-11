-- Phase 2: server-side search over title, body, source_name, author, and title_zh.
-- Rebuilt idempotently on every migrate() via DROP ... IF EXISTS.
-- Keep item_evaluations/enrich_ai_fts DDL byte-equivalent after normalization
-- across migrations 003, 004, and 016.

DROP TRIGGER IF EXISTS items_ai_fts;
DROP TRIGGER IF EXISTS items_au_fts;
DROP TRIGGER IF EXISTS items_ad_fts;
DROP TRIGGER IF EXISTS evals_ai_fts;
DROP TRIGGER IF EXISTS enrich_ai_fts;
DROP TRIGGER IF EXISTS sources_au_fts;
DROP TABLE IF EXISTS items_fts;

CREATE VIRTUAL TABLE items_fts USING fts5(
  item_id UNINDEXED,
  title,
  content_text,
  source_name,
  author,
  title_zh,
  tokenize='trigram'
);

INSERT INTO items_fts(item_id, title, content_text, source_name, author, title_zh)
SELECT
  i.id,
  i.title,
  i.content_text,
  COALESCE(s.name, ''),
  COALESCE(i.author, ''),
  COALESCE(
    json_extract(
      (
        SELECT output_json
        FROM item_evaluations
        WHERE item_id = i.id
          AND stage = 'enrich'
          AND error IS NULL
        ORDER BY evaluated_at DESC, id DESC
        LIMIT 1
      ),
      '$.title_zh'
    ),
    ''
  )
FROM items i
LEFT JOIN sources s ON s.id = i.source_id;

CREATE TRIGGER items_ai_fts AFTER INSERT ON items BEGIN
  INSERT INTO items_fts(item_id, title, content_text, source_name, author, title_zh)
  VALUES (
    new.id,
    new.title,
    new.content_text,
    COALESCE((SELECT name FROM sources WHERE id = new.source_id), ''),
    COALESCE(new.author, ''),
    ''
  );
END;

CREATE TRIGGER items_au_fts AFTER UPDATE ON items BEGIN
  UPDATE items_fts
  SET title = new.title,
      content_text = new.content_text,
      source_name = COALESCE((SELECT name FROM sources WHERE id = new.source_id), ''),
      author = COALESCE(new.author, '')
  WHERE item_id = old.id;
END;

CREATE TRIGGER items_ad_fts AFTER DELETE ON items BEGIN
  DELETE FROM items_fts WHERE item_id = old.id;
END;

CREATE TRIGGER sources_au_fts AFTER UPDATE OF name ON sources BEGIN
  UPDATE items_fts
  SET source_name = COALESCE(new.name, '')
  WHERE item_id IN (SELECT id FROM items WHERE source_id = new.id);
END;

-- Keep this enrich_ai_fts block byte-identical in 003 and 004.
-- It keeps the title_zh FTS snapshot current after successful enrich evaluations.
-- Failed enrich rows are ignored so retries with errors cannot erase a good title_zh.
CREATE TRIGGER IF NOT EXISTS enrich_ai_fts AFTER INSERT ON item_evaluations
WHEN new.stage = 'enrich' AND new.error IS NULL AND new.output_json IS NOT NULL BEGIN
  UPDATE items_fts
  SET title_zh = COALESCE(json_extract(new.output_json, '$.title_zh'), '')
  WHERE item_id = new.item_id;
END;
