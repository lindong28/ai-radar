-- Phase 1: extend sources with kind / homepage_url / icon_url for AIHOT parity.
-- sqlite ALTER TABLE ADD COLUMN does not support IF NOT EXISTS, so the
-- migrate() helper in db.py treats "duplicate column name" errors as
-- idempotent no-ops on re-run.

ALTER TABLE sources ADD COLUMN kind TEXT NOT NULL DEFAULT 'feed';
ALTER TABLE sources ADD COLUMN homepage_url TEXT;
ALTER TABLE sources ADD COLUMN icon_url TEXT;
