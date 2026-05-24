CREATE INDEX IF NOT EXISTS idx_items_source_url_norm
ON items(source_id, lower(rtrim(url, '/')));

CREATE INDEX IF NOT EXISTS idx_items_published_fetched_id
ON items(published_at DESC, fetched_at DESC, id DESC);
