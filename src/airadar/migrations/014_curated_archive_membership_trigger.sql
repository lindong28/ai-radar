DROP TRIGGER IF EXISTS archive_cache_curated_ai;

CREATE TRIGGER archive_cache_curated_ai AFTER INSERT ON curated_items
WHEN NOT EXISTS (
  SELECT 1
  FROM curated_items existing
  WHERE existing.item_id = new.item_id
    AND existing.run_id IS NOT new.run_id
) BEGIN
  UPDATE archive_cache_generations
  SET archive_generation = archive_generation + 1
  WHERE id = 1;
END;
