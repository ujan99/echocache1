-- EchoCache schema v2
-- Adds: experiment arm + hit provenance on the access log,
--       an items table that acts as the slow origin store,
--       a log of every pre-warm write (needed for precision).
--
-- Safe to run on an existing database. The two ALTER statements will
-- error if you have already run this file once; that error is harmless.

CREATE TABLE IF NOT EXISTS access_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  item_id TEXT NOT NULL,
  ts INTEGER NOT NULL,
  cache_hit INTEGER NOT NULL
);

ALTER TABLE access_log ADD COLUMN arm TEXT NOT NULL DEFAULT 'on';
ALTER TABLE access_log ADD COLUMN source TEXT NOT NULL DEFAULT 'MISS';

CREATE INDEX IF NOT EXISTS idx_ts ON access_log(ts);
CREATE INDEX IF NOT EXISTS idx_session ON access_log(session_id, ts);
CREATE INDEX IF NOT EXISTS idx_arm_ts ON access_log(arm, ts);

-- the "slow backend" the cache sits in front of
CREATE TABLE IF NOT EXISTS items (
  item_id TEXT PRIMARY KEY,
  payload TEXT NOT NULL
);

-- every pre-warm write, so prediction precision can be computed
CREATE TABLE IF NOT EXISTS prewarm_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id TEXT NOT NULL,
  ts INTEGER NOT NULL,
  arm TEXT NOT NULL,
  reason TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_prewarm_ts ON prewarm_log(arm, ts);
