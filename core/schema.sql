CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY,
  planet TEXT NOT NULL,
  job_type TEXT NOT NULL,
  key TEXT NOT NULL,
  payload TEXT NOT NULL,
  priority REAL NOT NULL DEFAULT 1.0,
  state TEXT NOT NULL DEFAULT 'queued',      -- queued | running | done | dead
  attempts INTEGER NOT NULL DEFAULT 0,
  next_run_at REAL NOT NULL,
  lease_owner TEXT,
  lease_until REAL,
  last_error TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  UNIQUE (job_type, key)
);
CREATE INDEX IF NOT EXISTS jobs_runnable ON jobs (state, next_run_at);

CREATE TABLE IF NOT EXISTS stage_artifacts (
  job_id INTEGER NOT NULL REFERENCES jobs (id),
  stage TEXT NOT NULL,
  output TEXT NOT NULL,
  created_at REAL NOT NULL,
  PRIMARY KEY (job_id, stage)
);

CREATE TABLE IF NOT EXISTS llm_calls (
  id INTEGER PRIMARY KEY,
  backend TEXT NOT NULL,
  task TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  input_hash TEXT NOT NULL,
  output TEXT,
  outcome TEXT NOT NULL,
  error TEXT,
  latency_ms INTEGER NOT NULL,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS llm_calls_cache ON llm_calls (backend, prompt_version, input_hash, outcome);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  type TEXT NOT NULL,
  version INTEGER NOT NULL,
  payload TEXT NOT NULL,
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS consumer_cursors (
  consumer TEXT PRIMARY KEY,
  last_event_id INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS quota_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  allowance REAL NOT NULL,
  used INTEGER NOT NULL,
  limited INTEGER NOT NULL,
  window_start REAL NOT NULL,
  consecutive_failures INTEGER NOT NULL,
  breaker_open_until REAL NOT NULL,
  last_error TEXT
);

CREATE TABLE IF NOT EXISTS review_queue (
  id INTEGER PRIMARY KEY,
  planet TEXT NOT NULL,
  kind TEXT NOT NULL,
  ref TEXT NOT NULL,
  payload TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'open',        -- open | approved | rejected
  decision_note TEXT,
  created_at REAL NOT NULL,
  decided_at REAL,
  UNIQUE (kind, ref)
);

CREATE TABLE IF NOT EXISTS manifests (
  id INTEGER PRIMARY KEY,
  planet TEXT NOT NULL,
  name TEXT NOT NULL,
  release_id TEXT NOT NULL,
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS http_cache (
  url TEXT PRIMARY KEY,
  etag TEXT NOT NULL,
  body TEXT NOT NULL,
  fetched_at REAL NOT NULL
);
