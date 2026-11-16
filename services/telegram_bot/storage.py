"""Transactional sessions, request queue, update cursor and retryable outbound queue."""

import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from .engine import Engine, RequestLimit, chunks
from .commands import menu_commands


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class Store:
    def __init__(self, path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS updates (id INTEGER PRIMARY KEY, received_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (
                chat_id INTEGER PRIMARY KEY, state TEXT NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT, draft_key TEXT UNIQUE NOT NULL,
                chat_id INTEGER NOT NULL, flow TEXT NOT NULL, payload TEXT NOT NULL,
                summary TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new'
                    CHECK(status IN ('new','in_progress','closed')),
                created_at TEXT NOT NULL, created_ts REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS request_customer ON requests(chat_id,created_ts);
            CREATE TABLE IF NOT EXISTS subscriptions (
                chat_id INTEGER PRIMARY KEY, subscribed INTEGER NOT NULL CHECK(subscribed IN (0,1)),
                updated_at REAL NOT NULL, consent_version TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT, method TEXT NOT NULL,
                payload TEXT NOT NULL, chat_id INTEGER,
                request_id INTEGER REFERENCES requests(id),
                status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                available_at REAL NOT NULL DEFAULT 0, error TEXT, created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS outbound_pending ON outbox(status,available_at,id);
        """)
        self.db.executescript(Path(__file__).with_name("profiles.sql").read_text(encoding="utf-8"))

    def close(self):
        self.db.close()

    def state(self, chat_id):
        row = self.db.execute("SELECT state FROM sessions WHERE chat_id=?", (chat_id,)).fetchone()
        return json.loads(row[0]) if row else {}

    def offset(self):
        row = self.db.execute("SELECT value FROM meta WHERE key='offset'").fetchone()
        return int(row[0]) if row else 0

    def enqueue(self, method, payload, request_id=None):
        chat_id = payload.get("chat_id", payload.get("scope", {}).get("chat_id"))
        self.db.execute("INSERT INTO outbox(method,payload,chat_id,request_id,created_at) VALUES(?,?,?,?,?)",
                        (method, encode(payload), chat_id, request_id, time.time()))

    def queue_command_menu(self, chat_id, has_draft):
        """Called inside a transaction; obsolete retries must not restore an old menu."""
        subscription = self.db.execute("SELECT subscribed FROM subscriptions WHERE chat_id=?", (chat_id,)).fetchone()
        subscribed = bool(subscription and subscription[0])
        self.db.execute("""DELETE FROM outbox WHERE chat_id=?
            AND method IN ('setMyCommands','deleteMyCommands') AND status IN ('pending','failed')""",
            (chat_id,))
        for language in ("", "uk"):
            # Replace the complete list in the same scope, including after cancellation.
            # Avoid deleting the chat menu and making the client switch to a fallback.
            payload = {"scope": {"type": "chat", "chat_id": chat_id}, "language_code": language,
                       "commands": menu_commands(has_draft, subscribed)}
            self.enqueue("setMyCommands", payload)

    def reconcile_command_menus(self):
        """Restore existing customers' menus after startup or a deployment."""
        with self.db:
            for row in self.db.execute("SELECT chat_id,state FROM sessions").fetchall():
                self.queue_command_menu(row["chat_id"], bool(json.loads(row["state"]).get("draft")))

    def queue_staff_reply(self, text, texts, staff_chat_id):
        match = re.fullmatch(r"/reply(?:@mtech_customer_support_bot)?\s+([1-9][0-9]{0,14})\s+(.+)", text.strip(), re.I | re.S)
        body = match[2].strip() if match else ""
        feedback = texts["reply_usage"]
        if body and len(body) <= 1500:
            number = int(match[1])
            request = self.db.execute("SELECT chat_id FROM requests WHERE id=? AND flow='written'", (number,)).fetchone()
            feedback = texts["reply_missing"]
            if request:
                self.enqueue("sendMessage", {"chat_id": request["chat_id"],
                             "text": texts["staff_reply"].format(number=number, body=body),
                             "link_preview_options": {"is_disabled": True}}, number)
                feedback = texts["reply_queued"].format(number=number)
        self.enqueue("sendMessage", {"chat_id": staff_chat_id, "text": feedback})

    def process(self, update, content, staff_chat_id):
        """Commit response/state/request/update together; no API calls inside transaction."""
        now = time.time()
        with self.db:
            if self.db.execute("SELECT 1 FROM updates WHERE id=?", (update["update_id"],)).fetchone():
                return False
            self.db.execute("INSERT INTO updates VALUES(?,?)", (update["update_id"], now))
            # Store the last processed update, not a permanent high water mark. Telegram
            # can choose a random update ID following a week without updates.
            self.db.execute("INSERT OR REPLACE INTO meta VALUES('offset',?)", (str(update["update_id"] + 1),))
            callback = update.get("callback_query")
            message = callback.get("message", {}) if callback else update.get("message", {})
            sender = callback.get("from", {}) if callback else message.get("from", {})
            chat = message.get("chat", {})
            if not callback and re.match(r"^/reply(?:@mtech_customer_support_bot)?(?:\s|$)", message.get("text", ""), re.I):
                if (chat.get("id") == staff_chat_id and sender.get("id", 0) > 0 and not sender.get("is_bot")
                        and not message.get("forward_origin") and not message.get("sender_chat") and (chat.get("type") in {"group", "supergroup"}
                        or (chat.get("type") == "private" and sender["id"] == chat["id"]))):
                    self.queue_staff_reply(message["text"], content["texts"], staff_chat_id)
                return True
            if chat.get("type") != "private" or sender.get("id") != chat.get("id") or sender.get("is_bot"):
                # Ignore group/staff messages and inaccessible/foreign inline messages.
                if callback and callback.get("id"):
                    self.enqueue("answerCallbackQuery", {"callback_query_id": callback["id"]})
                return True
            chat_id = chat["id"]
            state = self.state(chat_id)
            had_draft = bool(state.get("draft"))
            subscription_changed = False
            # Bound per-user processing and stored text. Content of ignored messages is never persisted.
            throttle = state.get("throttle", {"start": now, "count": 0})
            if now - throttle["start"] >= 60:
                throttle = {"start": now, "count": 0}
            throttle["count"] += 1
            state["throttle"] = throttle
            if throttle["count"] > 30:
                if callback:
                    self.enqueue("answerCallbackQuery", {"callback_query_id": callback["id"],
                                                         "text": content["texts"]["rate_limit"]})
                elif throttle["count"] == 31:
                    self.enqueue("sendMessage", {"chat_id": chat_id, "text": content["texts"]["rate_limit"]})
            else:
                def submit(draft, summary):
                    existing = self.db.execute("SELECT id FROM requests WHERE draft_key=?", (draft["key"],)).fetchone()
                    if existing:
                        return str(existing[0])
                    count = self.db.execute("SELECT COUNT(*) FROM requests WHERE chat_id=? AND created_ts>?",
                                            (chat_id, now - 3600)).fetchone()[0]
                    if count >= 5:
                        raise RequestLimit()
                    created = datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds")
                    payload = {"flow": draft["flow"], "answers": draft["answers"],
                               "category": draft.get("category"), "category_url": draft.get("category_url"),
                               "origin": draft.get("origin"), "content_reviewed_at": content["reviewed_at"]}
                    row = self.db.execute("""INSERT INTO requests
                        (draft_key,chat_id,flow,payload,summary,created_at,created_ts) VALUES(?,?,?,?,?,?,?)""",
                        (draft["key"], chat_id, draft["flow"], encode(payload), summary, created, now))
                    request_id = row.lastrowid
                    if staff_chat_id is not None:
                        notice = content["texts"]["staff_notice"].format(number=request_id, summary=summary, created_at=created)
                        if draft["flow"] == "written":
                            notice += "\n\n" + content["texts"]["reply_hint"].format(number=request_id)
                        for part in chunks(notice):
                            self.enqueue("sendMessage", {"chat_id": staff_chat_id, "text": part,
                                "link_preview_options": {"is_disabled": True}}, request_id)
                        for photo in draft["answers"].get("photos") or []:
                            self.enqueue("sendPhoto", {"chat_id": staff_chat_id, "photo": photo["file_id"],
                                "caption": content["labels"]["request"] + " №" + str(request_id)}, request_id)
                    return str(request_id)

                customer = {"staff": chat_id == staff_chat_id, "requests": [dict(row) for row in self.db.execute(
                    "SELECT id,flow,status,created_at FROM requests WHERE chat_id=? ORDER BY created_ts DESC,id DESC LIMIT 10",
                    (chat_id,))], "subscribed": False}
                subscription = self.db.execute("SELECT subscribed FROM subscriptions WHERE chat_id=?", (chat_id,)).fetchone()
                customer["subscribed"] = bool(subscription and subscription[0])
                profile = self.db.execute("SELECT data FROM customer_profiles WHERE chat_id=?", (chat_id,)).fetchone()
                customer["profile"] = json.loads(profile[0]) if profile else {}

                def save_profile(data):
                    self.db.execute("""INSERT INTO customer_profiles(chat_id,data,updated_at) VALUES(?,?,?)
                        ON CONFLICT(chat_id) DO UPDATE SET data=excluded.data,updated_at=excluded.updated_at""",
                        (chat_id, encode(data), now))

                def subscribe(enabled):
                    nonlocal subscription_changed
                    subscription_changed = True
                    self.db.execute("""INSERT INTO subscriptions(chat_id,subscribed,updated_at,consent_version)
                        VALUES(?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET subscribed=excluded.subscribed,
                        updated_at=excluded.updated_at,consent_version=excluded.consent_version""",
                        (chat_id, int(enabled), now, "mtech-news-v1"))

                engine = Engine(content, state, chat_id, submit, customer, subscribe, save_profile)
                for method, payload in engine.handle(message, callback):
                    self.enqueue(method, payload)
            if subscription_changed or had_draft != bool(state.get("draft")) or state.get("command_menu_version") != 7:
                self.queue_command_menu(chat_id, bool(state.get("draft")))
                state["command_menu_version"] = 7
            self.db.execute("INSERT OR REPLACE INTO sessions VALUES(?,?,?)", (chat_id, encode(state), now))
        return True

    def due(self, limit=50):
        # Preserve order within each destination, but a failing staff destination must
        # not block customer confirmations. A failed item is visible to the operator.
        return self.db.execute("""SELECT o.* FROM outbox o
            WHERE o.status='pending' AND o.available_at<=?
            AND (o.chat_id IS NULL OR NOT EXISTS (
                SELECT 1 FROM outbox p WHERE p.chat_id=o.chat_id
                AND p.status='pending' AND p.id<o.id))
            ORDER BY CASE WHEN o.method='answerCallbackQuery' THEN 0 ELSE 1 END, o.id LIMIT ?""",
            (time.time(), limit)).fetchall()

    def delivered(self, row_id):
        with self.db:
            # Remove delivered message bodies; requests remain the canonical record.
            self.db.execute("UPDATE outbox SET status='sent',payload='{}',error=NULL WHERE id=?", (row_id,))

    def failed(self, row, error, retry_after=None, permanent=False):
        attempts = row["attempts"] + 1
        terminal = permanent or attempts >= 12
        delay = max(retry_after or 0, min(300, 2 ** min(attempts, 8)))
        with self.db:
            self.db.execute("UPDATE outbox SET status=?,attempts=?,available_at=?,error=? WHERE id=?",
                            ("failed" if terminal else "pending", attempts, time.time() + delay, error, row["id"]))
        return terminal

    def retry_failed(self):
        with self.db:
            return self.db.execute("""UPDATE outbox SET status='pending',attempts=0,available_at=0,error=NULL
                WHERE status='failed' AND method!='answerCallbackQuery'""").rowcount

    def maintenance(self):
        now = time.time()
        with self.db:
            # Unsubmitted drafts expire after seven days; submitted requests do not.
            for row in self.db.execute("SELECT chat_id FROM sessions WHERE updated_at<?",
                                       (now - 7 * 86400,)).fetchall():
                self.queue_command_menu(row["chat_id"], False)
            self.db.execute("DELETE FROM sessions WHERE updated_at<?", (now - 7 * 86400,))
            self.db.execute("DELETE FROM updates WHERE received_at<?", (now - 7 * 86400,))
            self.db.execute("DELETE FROM outbox WHERE status='sent' AND created_at<?", (now - 7 * 86400,))

    def requests(self, limit=50):
        return [dict(row) for row in self.db.execute(
            "SELECT id,flow,status,created_at FROM requests ORDER BY id DESC LIMIT ?", (limit,))]

    def request(self, number):
        row = self.db.execute("SELECT * FROM requests WHERE id=?", (number,)).fetchone()
        if row is None:
            raise ValueError("Request not found")
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        result["notifications"] = [dict(r) for r in self.db.execute(
            "SELECT id,method,status,attempts,error FROM outbox WHERE request_id=?", (number,))]
        return result

    def set_status(self, number, status):
        if status not in {"new", "in_progress", "closed"}:
            raise ValueError("Invalid request status")
        with self.db:
            if not self.db.execute("UPDATE requests SET status=? WHERE id=?", (status, number)).rowcount:
                raise ValueError("Request not found")

    def health(self):
        return {"offset": self.offset(), "requests": self.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0],
                "outbox": {r[0]: r[1] for r in self.db.execute("SELECT status,COUNT(*) FROM outbox GROUP BY status")}}
