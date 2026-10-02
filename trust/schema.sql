CREATE TABLE IF NOT EXISTS trust_audits (
  id INTEGER PRIMARY KEY,
  repo TEXT NOT NULL,
  sha TEXT NOT NULL,
  status TEXT NOT NULL,          -- graded | provisional | unsupported | no_tools
  grade TEXT,
  score INTEGER,
  categories TEXT NOT NULL,
  findings TEXT NOT NULL,
  tools TEXT NOT NULL,
  needs_review TEXT NOT NULL,
  provenance TEXT NOT NULL,
  summary TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS trust_audits_repo ON trust_audits (repo, created_at);

CREATE TABLE IF NOT EXISTS trust_servers (
  repo TEXT PRIMARY KEY,
  sources TEXT NOT NULL,           -- sorted comma list: audit, github, radar, registry
  stars INTEGER NOT NULL DEFAULT 0,
  first_seen REAL NOT NULL,
  last_checked REAL,
  head_sha TEXT,
  tools_hash TEXT,
  tools_changed INTEGER NOT NULL DEFAULT 0
);
