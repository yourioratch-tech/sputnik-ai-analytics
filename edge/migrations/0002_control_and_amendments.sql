CREATE TABLE IF NOT EXISTS price_amendments (
  id TEXT PRIMARY KEY,
  target_kind TEXT NOT NULL CHECK(target_kind IN ('bar', 'price')),
  symbol TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  event_time TEXT NOT NULL,
  replacement_json TEXT NOT NULL,
  reason TEXT NOT NULL,
  actor TEXT NOT NULL,
  source_url TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS price_amendments_target
  ON price_amendments(symbol, timeframe, event_time, target_kind, created_at DESC);

CREATE TABLE IF NOT EXISTS control_configuration_revisions (
  id TEXT PRIMARY KEY,
  version INTEGER NOT NULL UNIQUE,
  configuration_json TEXT NOT NULL,
  reason TEXT NOT NULL,
  actor TEXT NOT NULL,
  created_at TEXT NOT NULL
);
