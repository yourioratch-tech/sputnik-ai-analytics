CREATE TABLE IF NOT EXISTS agent_jobs (
  id TEXT PRIMARY KEY,
  role TEXT NOT NULL,
  model TEXT NOT NULL,
  output_format TEXT NOT NULL,
  task TEXT NOT NULL,
  context TEXT,
  status TEXT NOT NULL,
  requested_at TEXT NOT NULL,
  started_at TEXT,
  completed_at TEXT,
  worker_id TEXT,
  result_json TEXT,
  error TEXT
);
CREATE INDEX IF NOT EXISTS agent_jobs_status_requested
ON agent_jobs(status, requested_at);
