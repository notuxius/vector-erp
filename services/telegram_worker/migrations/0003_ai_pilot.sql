CREATE TABLE IF NOT EXISTS ai_jobs (
  id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL, prompt TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('pending','running','complete','expired')),
  created_at REAL NOT NULL, claim_id TEXT
);
CREATE INDEX IF NOT EXISTS ai_job_state ON ai_jobs(state,created_at);
CREATE INDEX IF NOT EXISTS ai_job_owner ON ai_jobs(chat_id,created_at);
