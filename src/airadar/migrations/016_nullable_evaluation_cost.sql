-- item_evaluations does not own token usage, so its historical hard-coded $0
-- was an invented measurement. Preserve the deprecated column for rollout
-- compatibility, but keep it nullable and remove all old fake values.
--
-- This is a one-time whole-table rewrite. It intentionally falls outside the
-- ADR-014 <20 MB steady-state sync budget and must be applied before a watched
-- db-sync round; the next replica sync is expected to take the base-copy path.
-- A historical build used marker 014_nullable_evaluation_cost. db.py treats
-- that marker as an alias only when the nullable schema is already present,
-- and migration 017 normalizes it to the canonical 016 marker.

DROP TRIGGER IF EXISTS enrich_ai_fts;
DROP TRIGGER IF EXISTS archive_cache_enrich_ai;
DROP TRIGGER IF EXISTS archive_cache_enrich_au;
DROP TRIGGER IF EXISTS archive_cache_enrich_ad;
DROP INDEX IF EXISTS idx_evaluations_item_stage_ruleset;
DROP INDEX IF EXISTS idx_evaluations_stage_error_item_id;

ALTER TABLE item_evaluations RENAME TO item_evaluations_old;

CREATE TABLE item_evaluations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id TEXT NOT NULL REFERENCES items(id),
  stage TEXT NOT NULL CHECK (stage IN ('prefilter', 'scoring', 'enrich')),
  ruleset_version TEXT NOT NULL,
  model_id TEXT NOT NULL,
  input_json TEXT NOT NULL,
  output_json TEXT,
  numeric_json TEXT,
  latency_ms INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL DEFAULT NULL,
  evaluated_at TEXT NOT NULL,
  error TEXT
);

INSERT INTO item_evaluations (
  id, item_id, stage, ruleset_version, model_id, input_json, output_json,
  numeric_json, latency_ms, cost_usd, evaluated_at, error
)
SELECT
  id, item_id, stage, ruleset_version, model_id, input_json, output_json,
  numeric_json, latency_ms, NULL, evaluated_at, error
FROM item_evaluations_old;

DROP TABLE item_evaluations_old;

CREATE INDEX idx_evaluations_item_stage_ruleset
ON item_evaluations(item_id, stage, ruleset_version);

CREATE INDEX idx_evaluations_stage_error_item_id
ON item_evaluations(stage, error, item_id, id DESC);

CREATE TRIGGER enrich_ai_fts AFTER INSERT ON item_evaluations
WHEN new.stage = 'enrich' AND new.error IS NULL AND new.output_json IS NOT NULL BEGIN
  UPDATE items_fts
  SET title_zh = COALESCE(json_extract(new.output_json, '$.title_zh'), '')
  WHERE item_id = new.item_id;
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

INSERT OR IGNORE INTO airadar_migrations(id, applied_at)
VALUES ('016_nullable_evaluation_cost', datetime('now'));
