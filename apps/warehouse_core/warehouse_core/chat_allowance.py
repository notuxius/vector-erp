"""Rolling question allowance shared by ERP processes on the site's persistent volume."""
import math
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

LIMIT = 100
WINDOW = 3600
COOLDOWN = 10
# Longer than the maximum two sequential gateway calls. Recovers an interrupted worker.
LEASE = 300


class Limited(Exception):
    pass


class Allowance:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = path, clock

    @contextmanager
    def transaction(self):
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS questions (
                id TEXT PRIMARY KEY, person TEXT NOT NULL, started REAL NOT NULL, busy_until REAL NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS questions_started ON questions(started)")
            yield db

    def state(self, db, person, now):
        used, oldest, latest, busy = db.execute("""SELECT COUNT(*), MIN(started), MAX(started), MAX(busy_until)
            FROM questions WHERE person=? AND started>?""", (person, now - WINDOW)).fetchone()
        retry = max((latest or 0) + COOLDOWN if used else 0, oldest + WINDOW if used >= LIMIT else 0)
        return {"remaining": max(0, LIMIT - used), "busy": (busy or 0) > now,
                "retry_at": math.ceil(retry) if retry > now else None}

    def status(self, person):
        with self.transaction() as db:
            return self.state(db, person, self.clock())

    @contextmanager
    def question(self, person):
        ticket = uuid.uuid4().hex
        with self.transaction() as db:
            now = self.clock()
            db.execute("DELETE FROM questions WHERE started<=?", (now - WINDOW,))
            state = self.state(db, person, now)
            if state['busy'] or state['retry_at']:
                raise Limited(describe(state))
            db.execute("INSERT INTO questions VALUES(?,?,?,?)", (ticket, person, now, now + LEASE))
        try:
            yield
        finally:
            with self.transaction() as db:
                db.execute("UPDATE questions SET busy_until=0 WHERE id=?", (ticket,))


def describe(state):
    lines = [f"Залишилося запитань: {state['remaining']} зі {LIMIT} за останні 60 хв. Пауза між запитаннями — {COOLDOWN} с."]
    if state['busy']:
        lines.append("Дочекайтеся завершення поточного запитання.")
    if state['retry_at']:
        moment = datetime.fromtimestamp(state['retry_at'], ZoneInfo("Europe/Kyiv"))
        lines.append(f"Наступне запитання — з {moment:%H:%M:%S} за Києвом.")
    return " ".join(lines)
