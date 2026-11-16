-- Additive migration; existing sessions, requests and role assignments are untouched.
CREATE TABLE IF NOT EXISTS subscriptions (
  chat_id INTEGER PRIMARY KEY, subscribed INTEGER NOT NULL CHECK(subscribed IN (0,1)),
  updated_at REAL NOT NULL, consent_version TEXT NOT NULL
);
