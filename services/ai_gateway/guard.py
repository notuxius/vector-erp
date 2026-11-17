"""Persistent, conservative spending gate. Currency is integer billionths of USD.

Only this service holds the pilot API key. No retries, arbitrary models or tools.
See docs/ai-spending-control.uk.md for the budget/activation procedure.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import re
import sqlite3
import time
import uuid

USD = 1_000_000_000
MODEL = "gpt-6-luna"
MAX_INPUT = 1_050_000
MAX_OUTPUT = 2048
# Worst published long-context input (including cache writes) and output prices.
# Reverified 2026-11-16: https://developers.openai.com/api/docs/models/gpt-6-luna
# Reservations use the worst case. Settlement uses validated token details when
# available; missing details retain the conservative estimate, never a discount.
INPUT_RATE = 250
OUTPUT_RATE = 750
PRICE_VALID_UNTIL = datetime(2026, 11, 20, tzinfo=timezone.utc).timestamp()
THRESHOLDS = (75, 95)


class Blocked(Exception):
    pass


def integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError("Expected a non-negative integer")
    return value


class Ledger:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = path, clock
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS policy (id INTEGER PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS api_limits (
                    id INTEGER PRIMARY KEY CHECK (id=1), observed REAL NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS requests (
                    id TEXT PRIMARY KEY, period TEXT NOT NULL, reserved INTEGER NOT NULL,
                    actual INTEGER, state TEXT NOT NULL, created INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS request_incidents (
                    request_id TEXT PRIMARY KEY, evidence TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, period TEXT NOT NULL,
                    threshold INTEGER NOT NULL, created INTEGER NOT NULL,
                    UNIQUE(period, threshold));
            """)
            if 'payload' not in {row['name'] for row in db.execute('PRAGMA table_info(events)')}:
                db.execute('ALTER TABLE events ADD COLUMN payload TEXT')

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def policy(self, db):
        row = db.execute("SELECT data FROM policy WHERE id=1").fetchone()
        return json.loads(row[0]) if row else None

    def save(self, db, policy):
        db.execute("INSERT OR REPLACE INTO policy VALUES (1, ?)", (json.dumps(policy),))

    def held(self, db):
        return db.execute("SELECT COALESCE(SUM(reserved),0) FROM requests WHERE state != 'settled'").fetchone()[0]

    def events(self, db, p):
        if p['allowance'] <= 0:
            return
        for threshold in THRESHOLDS:
            if p['spent'] * 100 >= p['allowance'] * threshold:
                payload = json.dumps({'spent': p['spent'], 'allowance': p['allowance']})
                db.execute("INSERT OR IGNORE INTO events(period,threshold,created,payload) VALUES(?,?,?,?)",
                           (p['period'], threshold, int(self.clock()), payload))

    def snapshot(self, data):
        """Set the local budget. Older snapshot files may also carry opening usage."""
        budget_fields = {'period', 'allowance'}
        legacy_fields = budget_fields | {'remaining', 'observed_at', 'expires_at', 'source', 'exclusive'}
        if not isinstance(data, dict) or set(data) not in (budget_fields, legacy_fields):
            raise ValueError("Supply a period and allowance, or a legacy snapshot")
        period = data['period']
        if not isinstance(period, str) or not period.strip():
            raise ValueError("A budget period is required")
        allowance = integer(data['allowance'])
        remaining = integer(data.get('remaining', allowance))
        if remaining > allowance:
            raise ValueError("Remaining amount exceeds the budget")
        with self.transaction() as db:
            old = self.policy(db)
            same = old and old['period'] == period
            if old and not same and (old['enabled'] or self.held(db)):
                raise Blocked("Disable AI and reconcile all holds before changing periods")
            p = dict(data, spent=allowance - remaining, enabled=False)
            if same:
                p['spent'] = max(old['spent'], p['spent'])
                p['enabled'] = old['enabled']
            self.save(db, p)
            self.events(db, p)

    def reason(self, db, p, require_enabled=True):
        if not p or p['allowance'] <= 0:
            return 'usage_budget_not_configured'
        if require_enabled and not p['enabled']:
            return 'ai_disabled'
        if self.clock() >= PRICE_VALID_UNTIL:
            return 'pricing_needs_verification'
        if db.execute("SELECT 1 FROM requests WHERE state='uncertain' LIMIT 1").fetchone():
            return 'unresolved_request'
        if (p['spent'] + self.held(db)) * 100 >= p['allowance'] * 95:
            return 'cutoff_95_percent'
        return None

    def enable(self, keep_reserved=()):
        with self.transaction() as db:
            p = self.policy(db)
            for request_id in keep_reserved:
                changed = db.execute("""UPDATE requests SET state='deferred' WHERE id=?
                    AND state IN ('uncertain','deferred') AND actual IS NULL AND period=?""",
                    (request_id, p['period'] if p else None))
                if changed.rowcount != 1:
                    raise Blocked('request_is_not_an_unresolved_hold')
            reason = self.reason(db, p, require_enabled=False)
            if reason:
                raise Blocked(reason)
            p['enabled'] = True
            self.save(db, p)

    def disable(self):
        with self.transaction() as db:
            p = self.policy(db)
            if p:
                p['enabled'] = False
                self.save(db, p)

    def recover(self):
        # A process restart cannot establish whether an interrupted call was billed.
        with self.transaction() as db:
            db.execute("UPDATE requests SET state='uncertain' WHERE state='pending'")
            p = self.policy(db)
            if p and db.execute("SELECT 1 FROM requests WHERE state NOT IN ('settled','deferred') LIMIT 1").fetchone():
                p['enabled'] = False
                self.save(db, p)

    def reserve(self, output_tokens):
        integer(output_tokens, 1)
        if output_tokens > MAX_OUTPUT:
            raise ValueError("Output limit exceeds the approved maximum")
        amount = MAX_INPUT * INPUT_RATE + output_tokens * OUTPUT_RATE
        with self.transaction() as db:
            p = self.policy(db)
            reason = self.reason(db, p)
            if reason:
                raise Blocked(reason)
            if (p['spent'] + self.held(db) + amount) * 100 > p['allowance'] * 95:
                raise Blocked('next_request_would_cross_95_percent')
            request_id = uuid.uuid4().hex
            db.execute("INSERT INTO requests VALUES(?,?,?,?,?,?)",
                       (request_id, p['period'], amount, None, 'pending', int(self.clock())))
            return request_id

    def settle(self, request_id, input_tokens, output_tokens, output_limit, input_details=None):
        integer(input_tokens)
        integer(output_tokens)
        if input_tokens > MAX_INPUT or output_tokens > output_limit:
            raise ValueError("Provider usage exceeds approved bounds")
        amount = input_tokens * INPUT_RATE + output_tokens * OUTPUT_RATE
        if input_details is not None:
            cached = integer(input_details['cached_tokens'])
            written = integer(input_details['cache_write_tokens'])
            if cached + written > input_tokens:
                raise ValueError('Invalid cache accounting')
            lifetimes = input_details.get('cache_write_tokens_details')
            if lifetimes is not None:
                short = integer(lifetimes['ttl_30m_tokens'])
                long = integer(lifetimes['ttl_12h_tokens'])
                if long or short != written:
                    raise ValueError('Unapproved cache lifetime')
            input_cost = (input_tokens - cached - written) * 100 + cached * 10 + written * 125
            long_context = input_tokens > 272_000
            amount = input_cost * (2 if long_context else 1) + output_tokens * (750 if long_context else 500)
        with self.transaction() as db:
            row = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
            if not row or row['state'] != 'pending' or amount > row['reserved']:
                raise ValueError("Invalid or already reconciled reservation")
            p = self.policy(db)
            if p['period'] != row['period']:
                raise ValueError("Reservation period changed")
            p['spent'] += amount
            db.execute("UPDATE requests SET state='settled', actual=? WHERE id=?", (amount, request_id))
            self.save(db, p)
            self.events(db, p)

    def uncertain(self, request_id, evidence=None):
        with self.transaction() as db:
            db.execute("UPDATE requests SET state='uncertain' WHERE id=? AND state='pending'", (request_id,))
            if evidence:
                db.execute("INSERT OR IGNORE INTO request_incidents VALUES (?,?)", (request_id, json.dumps(evidence)))
            p = self.policy(db)
            if p:
                p['enabled'] = False
                self.save(db, p)

    def observe_api_limits(self, headers):
        observed = self.clock()
        limits = {}
        for resource in ('requests', 'tokens', 'project-tokens'):
            values = {}
            for field in ('limit', 'remaining', 'reset'):
                raw = headers.get(f'x-ratelimit-{field}-{resource}', '')
                pattern = r'(?:[0-9]+(?:\.[0-9]+)?(?:ms|[smhd]))+' if field == 'reset' else r'[0-9]+'
                if len(raw) <= 32 and re.fullmatch(pattern, raw):
                    values[field] = raw
            if values:
                limits[resource] = values
        if limits:
            with self.transaction() as db:
                db.execute("""INSERT INTO api_limits VALUES (1, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET observed=excluded.observed, data=excluded.data
                    WHERE excluded.observed >= api_limits.observed""", (observed, json.dumps(limits)))

    def status(self, include_events=True):
        with self.transaction() as db:
            p = self.policy(db)
            reason = self.reason(db, p)
            if reason is None and (p['spent'] + self.held(db) + MAX_INPUT * INPUT_RATE + OUTPUT_RATE) * 100 > p['allowance'] * 95:
                reason = 'next_request_would_cross_95_percent'
            observation = db.execute("SELECT observed, data FROM api_limits WHERE id=1").fetchone()
            return {
                'enabled': bool(p and p['enabled']), 'can_request': reason is None,
                'block_reason': reason, 'cutoff_percent': 95, 'model': MODEL,
                'alert_thresholds': list(THRESHOLDS),
                'allowance_usd': p['allowance'] / USD if p else None,
                'accounted_usage_usd': p['spent'] / USD if p else 0,
                'reserved_usd': self.held(db) / USD,
                'cutoff_usd': p['allowance'] * 95 // 100 / USD if p else None,
                'period': p['period'] if p else None,
                'pricing_valid_until': PRICE_VALID_UNTIL,
                'balance_refresh_required': False,
                'api_limits': json.loads(observation['data']) if observation else {},
                'api_limits_observed_at': observation['observed'] if observation else None,
                **({'events': [dict(row) for row in db.execute("SELECT * FROM events ORDER BY id")]}
                   if include_events else {}),
            }
