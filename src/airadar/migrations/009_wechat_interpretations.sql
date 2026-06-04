CREATE TABLE IF NOT EXISTS wechat_interpretations (
  item_id TEXT PRIMARY KEY REFERENCES items(id),
  slug TEXT NOT NULL,
  recommendation TEXT,
  save_decision INTEGER NOT NULL DEFAULT 0,
  save_reason TEXT,
  abstract TEXT,
  tags_json TEXT NOT NULL DEFAULT '[]',
  summary_md TEXT NOT NULL DEFAULT '',
  model TEXT,
  kb_synced INTEGER NOT NULL DEFAULT 0,
  processed_at TEXT NOT NULL,
  error TEXT
);

CREATE INDEX IF NOT EXISTS idx_wechat_interp_decision
ON wechat_interpretations(save_decision, processed_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_wechat_interp_slug
ON wechat_interpretations(slug);
