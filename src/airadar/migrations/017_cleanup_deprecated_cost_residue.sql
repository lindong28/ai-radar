-- Normalize the legacy nullable-cost migration marker and clear every retired
-- stored-cost carrier. This rebuilds only the tiny retired main-db llm_usage
-- table so its NOT NULL legacy schema can preserve rows with NULL costs; it
-- must not replay migration 016's 388 MiB item_evaluations rewrite.

DROP INDEX IF EXISTS idx_llm_usage_created_model;
DROP INDEX IF EXISTS idx_llm_usage_stage_created;
DROP INDEX IF EXISTS idx_llm_usage_item;
ALTER TABLE llm_usage RENAME TO llm_usage_deprecated_cost_old;
CREATE TABLE llm_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  stage TEXT NOT NULL CHECK (stage IN ('prefilter', 'score', 'enrich', 'interpret')),
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  item_id TEXT,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  total_tokens INTEGER NOT NULL DEFAULT 0,
  input_item_count INTEGER NOT NULL DEFAULT 1,
  input_char_count INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL DEFAULT NULL,
  attribution_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
INSERT INTO llm_usage (
  id, stage, provider, model, item_id, input_tokens, output_tokens, total_tokens,
  input_item_count, input_char_count, cost_usd, attribution_json, created_at
)
SELECT
  id, stage, provider, model, item_id, input_tokens, output_tokens, total_tokens,
  input_item_count, input_char_count, NULL, attribution_json, created_at
FROM llm_usage_deprecated_cost_old;
DROP TABLE llm_usage_deprecated_cost_old;
CREATE INDEX idx_llm_usage_created_model ON llm_usage(created_at, model);
CREATE INDEX idx_llm_usage_stage_created ON llm_usage(stage, created_at);
CREATE INDEX idx_llm_usage_item ON llm_usage(item_id);

UPDATE item_evaluations SET cost_usd = NULL WHERE cost_usd IS NOT NULL;

DELETE FROM airadar_migrations WHERE id = '014_nullable_evaluation_cost';
INSERT OR IGNORE INTO airadar_migrations (id, applied_at)
VALUES ('016_nullable_evaluation_cost', datetime('now'));
INSERT OR IGNORE INTO airadar_migrations (id, applied_at)
VALUES ('017_cleanup_deprecated_cost_residue', datetime('now'));
