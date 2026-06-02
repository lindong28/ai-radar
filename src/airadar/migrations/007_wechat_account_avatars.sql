CREATE TABLE IF NOT EXISTS wechat_account_avatars (
  account TEXT PRIMARY KEY CHECK (length(trim(account)) > 0),
  avatar_url TEXT,
  checked_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
