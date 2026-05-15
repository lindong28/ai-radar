-- Phase 2: server-side search over title, body, and scoring reasoning.
-- Rebuild rows on every migrate() call so this migration remains idempotent
-- even before a formal migrations table exists.

CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
  item_id UNINDEXED,
  title,
  content_text,
  reasoning,
  tokenize='trigram'
);

DELETE FROM items_fts;

INSERT INTO items_fts(item_id, title, content_text, reasoning)
SELECT i.id, i.title, i.content_text,
  COALESCE(
    json_extract(
      (
        SELECT numeric_json
        FROM item_evaluations
        WHERE item_id = i.id
          AND stage = 'scoring'
          AND error IS NULL
        ORDER BY evaluated_at DESC, id DESC
        LIMIT 1
      ),
      '$.reasoning'
    ),
    ''
  )
FROM items i;

CREATE TRIGGER IF NOT EXISTS items_ai_fts AFTER INSERT ON items BEGIN
  INSERT INTO items_fts(item_id, title, content_text, reasoning)
  VALUES (new.id, new.title, new.content_text, '');
END;

CREATE TRIGGER IF NOT EXISTS items_ad_fts AFTER DELETE ON items BEGIN
  DELETE FROM items_fts WHERE item_id = old.id;
END;

CREATE TRIGGER IF NOT EXISTS items_au_fts AFTER UPDATE ON items BEGIN
  UPDATE items_fts
  SET item_id = new.id,
      title = new.title,
      content_text = new.content_text
  WHERE item_id = old.id;
END;

CREATE TRIGGER IF NOT EXISTS evals_ai_fts AFTER INSERT ON item_evaluations
WHEN new.stage = 'scoring' AND new.numeric_json IS NOT NULL BEGIN
  UPDATE items_fts
  SET reasoning = COALESCE(json_extract(new.numeric_json, '$.reasoning'), '')
  WHERE item_id = new.item_id;
END;
