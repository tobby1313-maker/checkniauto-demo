PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY,
 status TEXT NOT NULL CHECK(status IN ('PREPARING','WAITING_FOR_AI','PROCESSING','DONE','FAILED')),
 source_url TEXT NOT NULL, language TEXT NOT NULL DEFAULT 'sk', title TEXT NOT NULL DEFAULT '',
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
 manifest TEXT, manifest_hash TEXT, report TEXT, report_hash TEXT,
 lease_token TEXT, lease_until INTEGER, error TEXT
);
CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status,created_at);
CREATE INDEX IF NOT EXISTS jobs_created ON jobs(created_at);
CREATE TABLE IF NOT EXISTS assets (
 job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
 name TEXT NOT NULL, bytes INTEGER NOT NULL, sha256 TEXT NOT NULL,
 ready INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(job_id,name)
);
CREATE TABLE IF NOT EXISTS counters (bucket TEXT PRIMARY KEY, used INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS oauth_clients (
 id TEXT PRIMARY KEY, redirect_uris TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_codes (
 hash TEXT PRIMARY KEY, client_id TEXT NOT NULL, redirect_uri TEXT NOT NULL,
 challenge TEXT NOT NULL, resource TEXT NOT NULL, scope TEXT NOT NULL,
 expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_tokens (
 hash TEXT PRIMARY KEY, client_id TEXT NOT NULL, kind TEXT NOT NULL,
 resource TEXT NOT NULL, scope TEXT NOT NULL, expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS oauth_expiry ON oauth_tokens(expires_at);
