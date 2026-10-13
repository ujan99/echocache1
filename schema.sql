CREATE TABLE IF NOT EXISTS access_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  item_id TEXT NOT NULL,
  ts INTEGER NOT NULL,
  cache_hit INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ts ON access_log(ts);
CREATE INDEX IF NOT EXISTS idx_session ON access_log(session_id, ts);