-- Errored interpretations were permanently skipped by the NOT EXISTS candidate
-- filter, so transient provider outages (quota, balance) silently froze the
-- /wechat feed. Track a per-row retry counter so the runner can re-attempt
-- errored rows with exponential backoff instead of never.
ALTER TABLE wechat_interpretations ADD COLUMN error_retry_count INTEGER NOT NULL DEFAULT 0;
