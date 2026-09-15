-- Separate roles use the same migrated schema; no legacy inputs are imported.
CREATE TABLE IF NOT EXISTS ingestion_identity (
  singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
  database_id TEXT NOT NULL UNIQUE,
  target_id TEXT,
  target_path TEXT,
  raw_root TEXT
);
CREATE TABLE IF NOT EXISTS ingestion_outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  payload BLOB NOT NULL,
  sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ingestion_acks (
  queue_id TEXT NOT NULL,
  batch_id INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  generation TEXT NOT NULL,
  completed_run_at TEXT,
  PRIMARY KEY (queue_id, batch_id)
);
