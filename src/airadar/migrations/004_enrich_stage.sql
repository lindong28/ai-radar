-- Phase 3: add the enrich evaluation stage.
-- db.py skips this entire file after airadar_migrations records 004_enrich_stage.
-- On cold databases this file still rebuilds item_evaluations once, so it must
-- recreate the enrich_ai_fts trigger after the RENAME below.

CREATE TABLE IF NOT EXISTS airadar_migrations (
  id TEXT PRIMARY KEY,
  applied_at TEXT NOT NULL
);

CREATE TEMP TABLE IF NOT EXISTS _airadar_migration_004_apply (
  should_apply INTEGER NOT NULL CHECK (should_apply IN (0, 1))
);

DELETE FROM _airadar_migration_004_apply;

INSERT INTO _airadar_migration_004_apply(should_apply)
SELECT CASE
  WHEN EXISTS (SELECT 1 FROM airadar_migrations WHERE id = '004_enrich_stage') THEN 0
  ELSE 1
END;

DELETE FROM curated_items
WHERE (SELECT should_apply FROM _airadar_migration_004_apply) = 1;

DELETE FROM curation_runs
WHERE (SELECT should_apply FROM _airadar_migration_004_apply) = 1;

DELETE FROM item_evaluations
WHERE (SELECT should_apply FROM _airadar_migration_004_apply) = 1;

DROP INDEX IF EXISTS idx_evaluations_item_stage_ruleset;

DROP TRIGGER IF EXISTS evals_ai_fts;

DROP TABLE IF EXISTS item_evaluations_new;

CREATE TABLE item_evaluations_new (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id TEXT NOT NULL REFERENCES items(id),
  stage TEXT NOT NULL CHECK (stage IN ('prefilter', 'scoring', 'enrich')),
  ruleset_version TEXT NOT NULL,
  model_id TEXT NOT NULL,
  input_json TEXT NOT NULL,
  output_json TEXT,
  numeric_json TEXT,
  latency_ms INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL NOT NULL DEFAULT 0,
  evaluated_at TEXT NOT NULL,
  error TEXT
);

INSERT INTO item_evaluations_new (
  id, item_id, stage, ruleset_version, model_id, input_json, output_json,
  numeric_json, latency_ms, cost_usd, evaluated_at, error
)
SELECT
  id, item_id, stage, ruleset_version, model_id, input_json, output_json,
  numeric_json, latency_ms, cost_usd, evaluated_at, error
FROM item_evaluations;

DROP TABLE item_evaluations;

ALTER TABLE item_evaluations_new RENAME TO item_evaluations;

CREATE INDEX IF NOT EXISTS idx_evaluations_item_stage_ruleset
ON item_evaluations(item_id, stage, ruleset_version);

-- Keep this enrich_ai_fts block byte-identical in 003 and 004.
-- It keeps the title_zh FTS snapshot current after successful enrich evaluations.
-- Failed enrich rows are ignored so retries with errors cannot erase a good title_zh.
CREATE TRIGGER IF NOT EXISTS enrich_ai_fts AFTER INSERT ON item_evaluations
WHEN new.stage = 'enrich' AND new.error IS NULL AND new.output_json IS NOT NULL BEGIN
  UPDATE items_fts
  SET title_zh = COALESCE(json_extract(new.output_json, '$.title_zh'), '')
  WHERE item_id = new.item_id;
END;

INSERT INTO airadar_migrations(id, applied_at)
SELECT '004_enrich_stage', datetime('now')
WHERE (SELECT should_apply FROM _airadar_migration_004_apply) = 1;

DROP TABLE _airadar_migration_004_apply;
