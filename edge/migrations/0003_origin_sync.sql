CREATE TABLE IF NOT EXISTS origin_sync_outbox (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK(kind IN ('market', 'news')),
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  synced_at TEXT
);

CREATE INDEX IF NOT EXISTS origin_sync_pending
  ON origin_sync_outbox(synced_at, created_at);
