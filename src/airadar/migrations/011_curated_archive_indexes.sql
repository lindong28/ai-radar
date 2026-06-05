CREATE INDEX IF NOT EXISTS idx_curated_items_item_run
ON curated_items(item_id, run_id);
