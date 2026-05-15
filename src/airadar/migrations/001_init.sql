PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  url TEXT NOT NULL,
  tier TEXT NOT NULL CHECK (tier IN ('T1', 'T1.5', 'T2')),
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
  meta_json TEXT NOT NULL DEFAULT '{}',
  synced_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES sources(id),
  url TEXT NOT NULL,
  title TEXT NOT NULL,
  author TEXT,
  published_at TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  content_text TEXT NOT NULL,
  content_html TEXT,
  content_hash TEXT NOT NULL,
  extra_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE (source_id, content_hash)
);

CREATE INDEX IF NOT EXISTS idx_items_source_published
ON items(source_id, published_at DESC);

CREATE TABLE IF NOT EXISTS item_evaluations (
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

CREATE INDEX IF NOT EXISTS idx_evaluations_item_stage_ruleset
ON item_evaluations(item_id, stage, ruleset_version);

CREATE TABLE IF NOT EXISTS curation_runs (
  id TEXT PRIMARY KEY,
  ruleset_version TEXT NOT NULL,
  weights_json TEXT NOT NULL,
  threshold REAL NOT NULL,
  input_eval_ids TEXT NOT NULL,
  output_curated_ids TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS curated_items (
  run_id TEXT NOT NULL REFERENCES curation_runs(id),
  item_id TEXT NOT NULL REFERENCES items(id),
  weighted_score REAL NOT NULL,
  rank INTEGER NOT NULL,
  reason_json TEXT NOT NULL,
  PRIMARY KEY (run_id, item_id)
);

CREATE INDEX IF NOT EXISTS idx_curated_items_run_rank
ON curated_items(run_id, rank);

CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id TEXT NOT NULL,
  signal TEXT NOT NULL,
  body TEXT,
  ruleset_version TEXT NOT NULL,
  created_at TEXT NOT NULL
);
