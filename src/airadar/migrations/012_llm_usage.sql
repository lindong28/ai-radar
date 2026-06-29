CREATE TABLE IF NOT EXISTS llm_usage (
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
  cost_usd REAL NOT NULL DEFAULT 0,
  attribution_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_llm_usage_created_model
ON llm_usage(created_at, model);

CREATE INDEX IF NOT EXISTS idx_llm_usage_stage_created
ON llm_usage(stage, created_at);

CREATE INDEX IF NOT EXISTS idx_llm_usage_item
ON llm_usage(item_id);
