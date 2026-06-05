CREATE INDEX IF NOT EXISTS idx_evaluations_stage_error_item_id
ON item_evaluations(stage, error, item_id, id DESC);
