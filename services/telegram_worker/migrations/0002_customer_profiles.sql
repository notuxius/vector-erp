-- Contact profiles outlive seven-day sessions. Existing requests remain immutable.
CREATE TABLE IF NOT EXISTS customer_profiles (
  chat_id INTEGER PRIMARY KEY, data TEXT NOT NULL, updated_at REAL NOT NULL
);
-- Recover the most recent supplied value of each contact field, including a
-- deliberately skipped time (JSON null). Never overwrite an existing profile.
WITH sources AS (
  SELECT chat_id, json_extract(payload,'$.answers') AS answers, created_ts AS ts, id AS sequence
    FROM requests
  UNION ALL
  SELECT chat_id, json_extract(state,'$.draft.answers'), updated_at, 9223372036854775807
    FROM sessions
), ranked AS (
  SELECT chat_id, j.key, j.value, ts,
    ROW_NUMBER() OVER (PARTITION BY chat_id,j.key ORDER BY ts DESC,sequence DESC) AS rank
  FROM sources, json_each(sources.answers) AS j
  WHERE (j.key IN ('name','phone') AND j.type='text' AND length(trim(j.value))>0)
     OR (j.key='time' AND j.type IN ('text','null'))
)
INSERT OR IGNORE INTO customer_profiles(chat_id,data,updated_at)
SELECT chat_id,json_group_object(key,value),MAX(ts) FROM ranked WHERE rank=1 GROUP BY chat_id;
