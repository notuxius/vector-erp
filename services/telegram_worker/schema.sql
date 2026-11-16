CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS updates (id INTEGER PRIMARY KEY, received_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (chat_id INTEGER PRIMARY KEY, state TEXT NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS customer_profiles (
  chat_id INTEGER PRIMARY KEY, data TEXT NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT, draft_key TEXT UNIQUE NOT NULL,
  chat_id INTEGER NOT NULL, flow TEXT NOT NULL, payload TEXT NOT NULL,
  summary TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new','in_progress','closed')),
  created_at TEXT NOT NULL, created_ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS request_customer ON requests(chat_id,created_ts);
CREATE TABLE IF NOT EXISTS subscriptions (
  chat_id INTEGER PRIMARY KEY, subscribed INTEGER NOT NULL CHECK(subscribed IN (0,1)),
  updated_at REAL NOT NULL, consent_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT, method TEXT NOT NULL, payload TEXT NOT NULL,
  chat_id INTEGER, request_id INTEGER REFERENCES requests(id),
  status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
  available_at REAL NOT NULL DEFAULT 0, error TEXT, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS outbound_pending ON outbox(status,available_at,id);
CREATE INDEX IF NOT EXISTS outbound_chat ON outbox(chat_id,status,id);
CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(updated_at);
CREATE INDEX IF NOT EXISTS updates_expiry ON updates(received_at);
CREATE TABLE IF NOT EXISTS commit_guard (id INTEGER PRIMARY KEY, valid INTEGER NOT NULL CHECK(valid=1));
CREATE TABLE IF NOT EXISTS leases (name TEXT PRIMARY KEY, owner TEXT NOT NULL, expires_at REAL NOT NULL);
