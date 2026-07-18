CREATE TABLE IF NOT EXISTS archive_cache_generations (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  archive_generation INTEGER NOT NULL DEFAULT 0,
  category_generation INTEGER NOT NULL DEFAULT 0
);

INSERT OR IGNORE INTO archive_cache_generations (
  id, archive_generation, category_generation
) VALUES (1, 0, 0);

DROP TRIGGER IF EXISTS archive_cache_items_ai;
DROP TRIGGER IF EXISTS archive_cache_items_au;
DROP TRIGGER IF EXISTS archive_cache_items_ad;
DROP TRIGGER IF EXISTS archive_cache_sources_ai;
DROP TRIGGER IF EXISTS archive_cache_sources_au_id;
DROP TRIGGER IF EXISTS archive_cache_sources_ad;
DROP TRIGGER IF EXISTS archive_cache_curated_ai;
DROP TRIGGER IF EXISTS archive_cache_curated_au;
DROP TRIGGER IF EXISTS archive_cache_curated_ad;
DROP TRIGGER IF EXISTS archive_cache_enrich_ai;
DROP TRIGGER IF EXISTS archive_cache_enrich_au;
DROP TRIGGER IF EXISTS archive_cache_enrich_ad;

CREATE TRIGGER archive_cache_items_ai AFTER INSERT ON items
WHEN EXISTS (
  SELECT 1
  FROM curated_items curated_item
  WHERE curated_item.item_id = new.id
)
OR EXISTS (
  SELECT 1
  FROM items duplicate_item
  WHERE duplicate_item.id <> new.id
    AND duplicate_item.source_id = new.source_id
    AND lower(rtrim(duplicate_item.url, '/')) = lower(rtrim(new.url, '/'))
    AND EXISTS (
      SELECT 1
      FROM curated_items curated_duplicate
      WHERE curated_duplicate.item_id = duplicate_item.id
    )
    AND NOT EXISTS (
      SELECT 1
      FROM items prior_item
      WHERE prior_item.id <> new.id
        AND prior_item.source_id = duplicate_item.source_id
        AND lower(rtrim(prior_item.url, '/')) = lower(rtrim(duplicate_item.url, '/'))
        AND (
          prior_item.published_at > duplicate_item.published_at
          OR (
            prior_item.published_at = duplicate_item.published_at
            AND prior_item.fetched_at > duplicate_item.fetched_at
          )
          OR (
            prior_item.published_at = duplicate_item.published_at
            AND prior_item.fetched_at = duplicate_item.fetched_at
            AND prior_item.id > duplicate_item.id
          )
        )
    )
    AND (
      new.published_at > duplicate_item.published_at
      OR (
        new.published_at = duplicate_item.published_at
        AND new.fetched_at > duplicate_item.fetched_at
      )
      OR (
        new.published_at = duplicate_item.published_at
        AND new.fetched_at = duplicate_item.fetched_at
        AND new.id > duplicate_item.id
      )
    )
) BEGIN
  UPDATE archive_cache_generations
  SET archive_generation = archive_generation + 1
  WHERE id = 1;
END;

CREATE TRIGGER archive_cache_items_au
AFTER UPDATE OF id, source_id, url, published_at, fetched_at ON items
WHEN old.id IS NOT new.id
  OR old.source_id IS NOT new.source_id
  OR old.url IS NOT new.url
  OR (
    (
      old.published_at IS NOT new.published_at
      OR old.fetched_at IS NOT new.fetched_at
    )
    AND EXISTS (
      SELECT 1
      FROM items duplicate_item
      WHERE duplicate_item.id <> new.id
        AND duplicate_item.source_id = new.source_id
        AND lower(rtrim(duplicate_item.url, '/')) = lower(rtrim(new.url, '/'))
    )
  ) BEGIN
  UPDATE archive_cache_generations
  SET archive_generation = archive_generation + 1
  WHERE id = 1;
END;

CREATE TRIGGER archive_cache_items_ad AFTER DELETE ON items BEGIN
  UPDATE archive_cache_generations
  SET archive_generation = archive_generation + 1
  WHERE id = 1;
END;

CREATE TRIGGER archive_cache_sources_ai AFTER INSERT ON sources BEGIN
  UPDATE archive_cache_generations
  SET archive_generation = archive_generation + 1
  WHERE id = 1;
END;

CREATE TRIGGER archive_cache_sources_au_id AFTER UPDATE OF id ON sources
WHEN old.id IS NOT new.id BEGIN
  UPDATE archive_cache_generations
  SET archive_generation = archive_generation + 1
  WHERE id = 1;
END;

CREATE TRIGGER archive_cache_sources_ad AFTER DELETE ON sources BEGIN
  UPDATE archive_cache_generations
  SET archive_generation = archive_generation + 1
  WHERE id = 1;
END;

CREATE TRIGGER archive_cache_curated_ai AFTER INSERT ON curated_items BEGIN
  UPDATE archive_cache_generations
  SET archive_generation = archive_generation + 1
  WHERE id = 1;
END;

CREATE TRIGGER archive_cache_curated_au
AFTER UPDATE OF run_id, item_id ON curated_items
WHEN old.run_id IS NOT new.run_id OR old.item_id IS NOT new.item_id BEGIN
  UPDATE archive_cache_generations
  SET archive_generation = archive_generation + 1
  WHERE id = 1;
END;

CREATE TRIGGER archive_cache_curated_ad AFTER DELETE ON curated_items BEGIN
  UPDATE archive_cache_generations
  SET archive_generation = archive_generation + 1
  WHERE id = 1;
END;

CREATE TRIGGER archive_cache_enrich_ai AFTER INSERT ON item_evaluations
WHEN new.stage = 'enrich' AND new.error IS NULL AND new.output_json IS NOT NULL BEGIN
  UPDATE archive_cache_generations
  SET category_generation = category_generation + 1
  WHERE id = 1;
END;

CREATE TRIGGER archive_cache_enrich_au
AFTER UPDATE OF item_id, stage, output_json, error ON item_evaluations
WHEN (
  (
    old.stage = 'enrich'
    AND old.error IS NULL
    AND old.output_json IS NOT NULL
  )
  OR (
    new.stage = 'enrich'
    AND new.error IS NULL
    AND new.output_json IS NOT NULL
  )
)
AND (
  old.item_id IS NOT new.item_id
  OR old.stage IS NOT new.stage
  OR old.output_json IS NOT new.output_json
  OR old.error IS NOT new.error
) BEGIN
  UPDATE archive_cache_generations
  SET category_generation = category_generation + 1
  WHERE id = 1;
END;

CREATE TRIGGER archive_cache_enrich_ad AFTER DELETE ON item_evaluations
WHEN old.stage = 'enrich' AND old.error IS NULL AND old.output_json IS NOT NULL BEGIN
  UPDATE archive_cache_generations
  SET category_generation = category_generation + 1
  WHERE id = 1;
END;
