CREATE TABLE IF NOT EXISTS webhook_events (
  id TEXT PRIMARY KEY,
  received_at TEXT NOT NULL,
  kind TEXT NOT NULL,
  symbol TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  event_time TEXT NOT NULL,
  event_name TEXT,
  price REAL,
  payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS webhook_events_symbol_time
  ON webhook_events(symbol, timeframe, event_time DESC);

CREATE TABLE IF NOT EXISTS market_bars (
  symbol TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  event_time TEXT NOT NULL,
  open REAL NOT NULL,
  high REAL NOT NULL,
  low REAL NOT NULL,
  close REAL NOT NULL,
  volume REAL NOT NULL,
  received_at TEXT NOT NULL,
  event_id TEXT NOT NULL REFERENCES webhook_events(id),
  PRIMARY KEY(symbol, timeframe, event_time)
);

CREATE TABLE IF NOT EXISTS price_points (
  event_id TEXT PRIMARY KEY REFERENCES webhook_events(id),
  symbol TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  event_time TEXT NOT NULL,
  price REAL NOT NULL,
  received_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS price_points_latest
  ON price_points(symbol, timeframe, event_time DESC);

CREATE TABLE IF NOT EXISTS news_items (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  title TEXT NOT NULL,
  url TEXT NOT NULL,
  published_at TEXT NOT NULL,
  received_at TEXT NOT NULL,
  category TEXT,
  summary TEXT,
  payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS news_items_published
  ON news_items(published_at DESC);

CREATE TABLE IF NOT EXISTS news_symbols (
  news_id TEXT NOT NULL REFERENCES news_items(id) ON DELETE CASCADE,
  symbol TEXT NOT NULL,
  PRIMARY KEY(news_id, symbol)
);

CREATE INDEX IF NOT EXISTS news_symbols_symbol
  ON news_symbols(symbol, news_id);
