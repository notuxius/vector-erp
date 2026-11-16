"""Telegram-only usage alerts, delivered without AI calls or Codex scheduling."""
from datetime import datetime, timezone
import hashlib
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPRedirectHandler

from guard import USD, THRESHOLDS


class DeliveryError(Exception):
    def __init__(self, code='network', retry_after=60):
        self.code = str(code)
        self.retry_after = retry_after if isinstance(retry_after, (int, float)) else 60
        super().__init__(self.code)  # Never include tokens, URLs or response bodies.


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Telegram:
    def __init__(self, token):
        self.token = token

    def call(self, method, payload=None):
        request = Request(f'https://api.telegram.org/bot{self.token}/{method}',
                          data=json.dumps(payload or {}).encode(), method='POST',
                          headers={'Content-Type': 'application/json'})
        try:
            with build_opener(NoRedirect()).open(request, timeout=15) as response:
                result = json.load(response)
        except HTTPError as error:
            try:
                result = json.loads(error.read())
            except Exception:
                result = {}
            raise DeliveryError(error.code, result.get('parameters', {}).get('retry_after', 60)) from None
        except Exception:
            raise DeliveryError() from None
        if not result.get('ok'):
            raise DeliveryError(result.get('error_code', 'invalid_response'),
                                result.get('parameters', {}).get('retry_after', 60))
        return result['result']


def alert_text(level, spent=None, allowance=None, cutoff=False, created=None):
    title = ('⛔ Вектор ERP · Захисна зупинка ШІ на межі 95%'
             if cutoff else f'⚠️ Вектор ERP · Використання ШІ: поріг {level}%')
    lines = [title]
    if spent is not None and allowance and allowance > 0:
        lines.append(f'Облік шлюзу: ${spent / USD:.4f} із ${allowance / USD:.2f} '
                     f'({spent / allowance * 100:.2f}%).')
        lines.append(f'Ліміт витрат 95%: ${allowance * .95 / USD:.2f}.')
    if cutoff:
        lines.append('Нові запити зупинено: наступний запит разом із захисним резервом '
                     'може перевищити ліміт 95%. Резерв не означає фактичне списання.')
    else:
        lines.append('Автоматичний ліміт зупинки — 95%.')
    lines.append('Це локальний облік Вектор ERP, а не рахунок OpenAI. '
                 'Оцінка може бути консервативною; витрати інших програм сюди не входять.')
    if created:
        lines.append('Подія: ' + datetime.fromtimestamp(created, timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))
    return '\n\n'.join(lines)


class Notifier:
    def __init__(self, ledger, env, api=None):
        self.ledger = ledger
        self.target = str(env.get('TELEGRAM_STAFF_CHAT_ID', ''))
        self.expected_user = env.get('AI_ALERT_TELEGRAM_USER', 'notuxius')
        self.expected_bot = env.get('AI_ALERT_TELEGRAM_BOT', 'mtech_customer_support_bot')
        self.enabled = env.get('AI_TELEGRAM_ALERTS_ENABLED') == 'true'
        self.tests_enabled = env.get('AI_TELEGRAM_TESTS_ENABLED') == 'true'
        token = env.get('TELEGRAM_BOT_TOKEN', '')
        self.configured = bool(token and self.target.isdigit() and int(self.target) > 0)
        # Old queued messages must never follow a changed destination or bot.
        self.destination = hashlib.sha256(f'{self.expected_bot}:{self.target}:{self.expected_user}'.encode()).hexdigest()
        self.api = api or Telegram(token)
        self.stop = threading.Event()
        self.loop_error = None
        with ledger.transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS telegram_alerts (
                event_key TEXT PRIMARY KEY, destination TEXT NOT NULL, body TEXT NOT NULL,
                created INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0, next_at INTEGER NOT NULL DEFAULT 0,
                lease_until INTEGER NOT NULL DEFAULT 0, receipt INTEGER, last_error TEXT)''')
            # Preserve history while retiring queued alerts removed by the owner.
            for threshold in (50, 85):
                db.execute("""UPDATE telegram_alerts SET state='suppressed',lease_until=0
                    WHERE event_key LIKE ? AND state IN ('pending','sending')""",
                           (f'threshold:%:{threshold}',))
            db.execute("""UPDATE telegram_alerts SET state='suppressed',lease_until=0
                WHERE (event_key LIKE 'blocked:%' OR event_key LIKE 'unresolved:%')
                AND state IN ('pending','sending')""")
            self.suppress_tests(db)

    def suppress_tests(self, db):
        if not self.tests_enabled:
            db.execute("""UPDATE telegram_alerts SET state='suppressed',lease_until=0
                WHERE event_key LIKE 'test:%' AND state IN ('pending','sending')""")

    def enqueue(self, db, event_key, body):
        db.execute('INSERT OR IGNORE INTO telegram_alerts(event_key,destination,body,created) VALUES(?,?,?,?)',
                   (event_key, self.destination, body, int(self.ledger.clock())))

    def sync(self):
        if not self.enabled or not self.configured:
            return
        with self.ledger.transaction() as db:
            for event in db.execute('SELECT * FROM events ORDER BY id').fetchall():
                if event['threshold'] not in THRESHOLDS:
                    continue
                payload = json.loads(event['payload']) if event['payload'] else {}
                key = f"threshold:{event['period']}:{event['threshold']}"
                self.enqueue(db, key, alert_text(event['threshold'], payload.get('spent'),
                             payload.get('allowance'), cutoff=event['threshold'] == 95, created=event['created']))
            p = self.ledger.policy(db)
            # Operational blocks remain visible in gateway status, without
            # Telegram notices. Only the owner's 75/95% usage alerts are automatic.
            if p and p['enabled']:
                from guard import MAX_INPUT, INPUT_RATE, OUTPUT_RATE
                exposure = p['spent'] + self.ledger.held(db) + MAX_INPUT * INPUT_RATE + OUTPUT_RATE
                if p['allowance'] > 0 and exposure * 100 > p['allowance'] * 95:
                    # Same key as threshold 95: early protective stops do not send
                    # a second 95% alert when billing later catches up.
                    self.enqueue(db, f"threshold:{p['period']}:95",
                                 alert_text(95, p['spent'], p['allowance'], cutoff=True,
                                            created=self.ledger.clock()))

    def queue_test(self, test_id):
        if not self.tests_enabled:
            raise ValueError('Telegram delivery tests are disabled by owner preference')
        if not self.enabled or not self.configured:
            raise ValueError('Telegram alerts are not configured')
        if not test_id or len(test_id) > 100:
            raise ValueError('Supply a stable test identifier')
        with self.ledger.transaction() as db:
            self.enqueue(db, f'test:{test_id}',
                         '✅ Вектор ERP · Тест сповіщень про ШІ\n\n'
                         'Telegram-сповіщення налаштовано для порогів 75% і 95%. '
                         'Захисний ліміт 95% працює незалежно від доставки повідомлень.\n\n'
                         'Це лише тест доставки: він не вмикає ШІ, не змінює баланс '
                         'і не викликає OpenAI. Email-сповіщення не налаштовано. '
                         'Зараз облік охоплює лише шлюз Вектор ERP; автоматичний облік '
                         'усіх проєктів OpenAI ще не підключено.')

    def dispatch_one(self):
        if not self.enabled or not self.configured:
            return False
        now = int(self.ledger.clock())
        with self.ledger.transaction() as db:
            self.suppress_tests(db)
            row = db.execute("""SELECT * FROM telegram_alerts WHERE destination=? AND state IN ('pending','sending')
                ORDER BY created,rowid LIMIT 1""", (self.destination,)).fetchone()
            if not row or row['next_at'] > now or (row['state'] == 'sending' and row['lease_until'] > now):
                return False
            db.execute("UPDATE telegram_alerts SET state='sending',lease_until=?,attempts=attempts+1 WHERE event_key=?",
                       (now + 120, row['event_key']))
        try:
            identity = self.api.call('getMe')
            chat = self.api.call('getChat', {'chat_id': int(self.target)})
            if (identity.get('username') != self.expected_bot or chat.get('type') != 'private'
                    or str(chat.get('id')) != self.target or chat.get('username') != self.expected_user):
                raise DeliveryError('recipient_mismatch', 3600)
            result = self.api.call('sendMessage', {'chat_id': int(self.target), 'text': row['body'],
                                   'link_preview_options': {'is_disabled': True}})
            receipt = result.get('message_id')
            if type(receipt) is not int or receipt <= 0:
                raise DeliveryError('invalid_receipt')
        except Exception as error:
            failure = error if isinstance(error, DeliveryError) else DeliveryError('delivery_failed')
            delay = max(60, failure.retry_after, min(3600, 60 * 2 ** min(row['attempts'], 6)))
            if failure.code in ('400', '401', '403', '404', 'recipient_mismatch'):
                delay = max(delay, 3600)
            with self.ledger.transaction() as db:
                db.execute("UPDATE telegram_alerts SET state='pending',lease_until=0,next_at=?,last_error=? WHERE event_key=?",
                           (int(self.ledger.clock() + delay), failure.code, row['event_key']))
            return False
        with self.ledger.transaction() as db:
            db.execute("UPDATE telegram_alerts SET state='delivered',lease_until=0,receipt=?,last_error=NULL WHERE event_key=?",
                       (receipt, row['event_key']))
        return True

    def status(self):
        with self.ledger.transaction() as db:
            counts = {row['state']: row['n'] for row in db.execute(
                'SELECT state,COUNT(*) n FROM telegram_alerts WHERE destination=? GROUP BY state', (self.destination,))}
            last = db.execute('SELECT receipt,last_error FROM telegram_alerts WHERE destination=? ORDER BY created DESC,rowid DESC LIMIT 1',
                              (self.destination,)).fetchone()
            held = db.execute("SELECT COUNT(*) FROM telegram_alerts WHERE destination!=? AND state IN ('pending','sending')", (self.destination,)).fetchone()[0]
            return {'enabled': self.enabled, 'configured': self.configured, 'channel': 'telegram',
                    'tests_enabled': self.tests_enabled,
                    'pending': counts.get('pending', 0) + counts.get('sending', 0),
                    'delivered': counts.get('delivered', 0), 'held_for_other_destination': held,
                    'suppressed': counts.get('suppressed', 0), 'thresholds': list(THRESHOLDS),
                    'last_receipt': last['receipt'] if last else None,
                    'last_error': last['last_error'] if last else None, 'worker_error': self.loop_error}

    def run(self):
        while not self.stop.is_set():
            try:
                self.sync()
                self.dispatch_one()
                self.loop_error = None
            except Exception:
                # Delivery/storage problems must not disable the spending gate.
                self.loop_error = 'notification_worker_unavailable'
            self.stop.wait(5)
