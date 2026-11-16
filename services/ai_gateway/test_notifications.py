from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest

from guard import Ledger, USD, PRICE_VALID_UNTIL, Blocked
from notifications import Notifier, DeliveryError


class FakeTelegram:
    def __init__(self):
        self.messages = []
        self.fail = None
        self.private = True
        self.before_send = None

    def call(self, method, payload=None):
        if method == 'getMe':
            return {'username': 'mtech_customer_support_bot'}
        if method == 'getChat':
            return {'id': 12345, 'type': 'private' if self.private else 'group', 'username': 'notuxius'}
        if method == 'sendMessage':
            if self.before_send:
                self.before_send()
            if self.fail:
                raise self.fail
            self.messages.append(payload)
            return {'message_id': len(self.messages)}
        raise AssertionError('Unsupported Telegram method')


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = PRICE_VALID_UNTIL - 86400
        self.path = str(Path(self.temp.name) / 'ledger.sqlite3')
        self.ledger = Ledger(self.path, lambda: self.now)
        self.env = {'TELEGRAM_BOT_TOKEN': 'fake-test-token', 'TELEGRAM_STAFF_CHAT_ID': '12345',
                    'AI_TELEGRAM_ALERTS_ENABLED': 'true', 'AI_TELEGRAM_TESTS_ENABLED': 'true'}
        self.api = FakeTelegram()
        self.notifier = Notifier(self.ledger, self.env, self.api)

    def tearDown(self):
        self.temp.cleanup()

    def fund(self, spent=0, allowance=100 * USD):
        self.ledger.snapshot({'period': 'test-grant', 'allowance': allowance, 'remaining': allowance - spent,
                              'observed_at': int(self.now), 'expires_at': int(self.now + 86400),
                              'source': 'offline test', 'exclusive': True})

    def test_all_thresholds_once_with_original_amounts(self):
        for amount in (50, 75, 85, 95):
            self.fund(spent=amount * USD)
        self.notifier.sync()
        self.notifier.sync()
        self.assertEqual(self.notifier.status()['pending'], 2)
        for _ in range(2):
            self.assertTrue(self.notifier.dispatch_one())
        self.assertFalse(self.notifier.dispatch_one())
        self.notifier.sync()
        self.assertEqual(self.notifier.status()['pending'], 0)
        for index, amount in enumerate((75, 95)):
            self.assertIn(f'${amount:.4f}', self.api.messages[index]['text'])
            self.assertEqual(self.api.messages[index]['chat_id'], 12345)

    def test_unfunded_disabled_does_not_notify(self):
        self.notifier.sync()
        self.assertFalse(self.notifier.dispatch_one())
        self.assertEqual(self.api.messages, [])

    def test_old_balance_keeps_working_silently_across_restart_and_refresh(self):
        self.fund()
        self.ledger.enable()
        self.now += 3601
        for _ in range(2):
            self.notifier.sync()
        self.assertFalse(self.notifier.dispatch_one())
        restarted = Notifier(Ledger(self.path, lambda: self.now), self.env, self.api)
        restarted.sync()
        self.assertFalse(restarted.dispatch_one())
        self.assertTrue(restarted.ledger.status()['can_request'])
        self.fund()
        self.assertTrue(self.ledger.status()['can_request'])
        self.now += 3601
        restarted.sync()
        self.assertFalse(restarted.dispatch_one())
        self.assertTrue(self.ledger.status()['can_request'])
        self.assertEqual(self.api.messages, [])

    def test_expired_grant_allows_requests_without_notification(self):
        self.now -= 86400
        self.fund()
        self.ledger.enable()
        self.now += 86401
        self.notifier.sync()
        self.assertFalse(self.notifier.dispatch_one())
        self.notifier.sync()
        self.assertFalse(self.notifier.dispatch_one())
        self.assertTrue(self.ledger.status()['can_request'])

    def test_uncertain_request_silently_disables_gate_and_keeps_hold(self):
        self.fund()
        self.ledger.enable()
        request_id = self.ledger.reserve(100)
        self.ledger.uncertain(request_id)
        self.notifier.sync()
        self.assertFalse(self.notifier.dispatch_one())
        self.notifier.sync()
        self.assertFalse(self.notifier.dispatch_one())
        self.assertFalse(self.ledger.status()['enabled'])
        self.assertGreater(self.ledger.status()['reserved_usd'], 0)

    def test_unverified_pricing_blocks_without_notification(self):
        self.fund()
        self.ledger.enable()
        self.now = PRICE_VALID_UNTIL + 1
        self.fund()
        self.notifier.sync()
        self.assertFalse(self.notifier.dispatch_one())
        self.assertFalse(self.ledger.status()['can_request'])
        self.assertEqual(self.api.messages, [])

    def test_retired_operational_notices_preserve_history_but_never_deliver(self):
        keys = ('blocked:test-grant:snapshot:1', 'blocked:test-grant:expiry:2',
                'blocked:test-grant:pricing:3', 'unresolved:request-1')
        with self.ledger.transaction() as db:
            for key in keys:
                self.notifier.enqueue(db, key, 'retired notice')
            db.execute("UPDATE telegram_alerts SET state='sending',lease_until=? WHERE event_key=?",
                       (int(self.now + 120), keys[-1]))
            self.notifier.enqueue(db, 'blocked:old-grant:snapshot:0', 'delivered history')
            db.execute("UPDATE telegram_alerts SET state='delivered',receipt=42 WHERE event_key='blocked:old-grant:snapshot:0'")
        restarted = Notifier(self.ledger, self.env, self.api)
        restarted.sync()
        self.assertEqual(restarted.status()['suppressed'], 4)
        self.assertEqual(restarted.status()['delivered'], 1)
        self.assertFalse(restarted.dispatch_one())
        self.fund(spent=95 * USD)
        restarted.sync()
        self.assertTrue(restarted.dispatch_one())
        self.assertTrue(restarted.dispatch_one())
        self.assertFalse(restarted.dispatch_one())
        self.assertEqual(len(self.api.messages), 2)

    def test_reservations_are_not_reported_as_spend(self):
        self.fund()
        self.ledger.enable()
        self.ledger.reserve(100)
        self.notifier.sync()
        self.assertEqual(self.notifier.status()['pending'], 0)

    def test_early_cutoff_sends_95_notice_without_claiming_actual_95_usage(self):
        self.fund(spent=94_900_000_000)
        self.ledger.enable()
        self.notifier.sync()
        for _ in range(4):
            self.notifier.dispatch_one()
        notice = self.api.messages[-1]['text']
        self.assertIn('94.90%', notice)
        self.assertIn('резерв', notice)
        self.assertIn('Захисна зупинка', notice)
        self.fund(spent=95 * USD)
        self.notifier.sync()
        self.assertEqual(self.notifier.status()['pending'], 0)
        with self.assertRaises(Blocked):
            self.ledger.reserve(100)

    def test_delivery_and_deduplication_survive_restart(self):
        self.fund(spent=75 * USD)
        self.notifier.sync()
        fresh = Notifier(Ledger(self.path, lambda: self.now), self.env, self.api)
        fresh.sync()
        self.assertTrue(fresh.dispatch_one())
        restarted = Notifier(Ledger(self.path, lambda: self.now), self.env, self.api)
        restarted.sync()
        self.assertFalse(restarted.dispatch_one())
        self.assertEqual(len(self.api.messages), 1)

    def test_retired_thresholds_preserve_history_but_never_deliver(self):
        self.fund(spent=85 * USD)
        with self.ledger.transaction() as db:
            for threshold in (50, 85):
                db.execute('INSERT INTO events(period,threshold,created) VALUES(?,?,?)',
                           ('test-grant', threshold, int(self.now)))
                self.notifier.enqueue(db, f'threshold:test-grant:{threshold}', 'old alert')
            self.notifier.enqueue(db, 'threshold:old-grant:50', 'delivered history')
            db.execute("UPDATE telegram_alerts SET state='delivered',receipt=42 WHERE event_key='threshold:old-grant:50'")
        restarted = Notifier(self.ledger, self.env, self.api)
        restarted.sync()
        self.assertEqual(restarted.status()['suppressed'], 2)
        self.assertEqual(restarted.status()['delivered'], 1)
        self.assertTrue(restarted.dispatch_one())
        self.assertIn('поріг 75%', self.api.messages[-1]['text'])
        self.assertFalse(restarted.dispatch_one())
        self.assertEqual(len(self.ledger.status()['events']), 3)

    def test_removed_levels_do_not_create_events(self):
        self.fund(spent=50 * USD)
        self.notifier.sync()
        self.assertEqual(self.notifier.status()['pending'], 0)
        self.fund(spent=85 * USD)
        self.notifier.sync()
        self.assertEqual(self.notifier.status()['pending'], 1)
        self.assertEqual([e['threshold'] for e in self.ledger.status()['events']], [75])

    def test_failure_retries_after_backoff_and_does_not_affect_cutoff(self):
        self.fund(spent=95 * USD)
        self.notifier.sync()
        self.api.fail = DeliveryError(429, 180)
        self.assertFalse(self.notifier.dispatch_one())
        self.api.fail = None
        self.now += 179
        self.assertFalse(self.notifier.dispatch_one())
        self.now += 1
        self.assertTrue(self.notifier.dispatch_one())
        self.assertFalse(self.ledger.status()['can_request'])

    def test_group_or_wrong_recipient_is_never_sent(self):
        self.notifier.queue_test('private-only')
        self.api.private = False
        self.assertFalse(self.notifier.dispatch_one())
        self.assertEqual(self.api.messages, [])
        self.assertEqual(self.notifier.status()['last_error'], 'recipient_mismatch')

    def test_changed_destination_does_not_inherit_queue(self):
        self.notifier.queue_test('original')
        changed = Notifier(self.ledger, dict(self.env, TELEGRAM_STAFF_CHAT_ID='99999'), self.api)
        self.assertFalse(changed.dispatch_one())
        self.assertEqual(changed.status()['held_for_other_destination'], 1)

    def test_setup_test_is_idempotent_and_does_not_enable_ai(self):
        self.notifier.queue_test('setup')
        self.notifier.queue_test('setup')
        self.assertTrue(self.notifier.dispatch_one())
        self.notifier.queue_test('setup')
        self.assertFalse(self.notifier.dispatch_one())
        self.assertFalse(self.ledger.status()['enabled'])
        self.assertEqual(self.ledger.status()['accounted_usage_usd'], 0)

    def test_delivery_tests_default_off_but_thresholds_still_deliver(self):
        env = dict(self.env)
        del env['AI_TELEGRAM_TESTS_ENABLED']
        quiet = Notifier(self.ledger, env, self.api)
        self.assertFalse(quiet.status()['tests_enabled'])
        with self.assertRaisesRegex(ValueError, 'disabled by owner preference'):
            quiet.queue_test('new-deployment-id')
        self.fund(spent=95 * USD)
        quiet.sync()
        self.assertTrue(quiet.dispatch_one())
        self.assertTrue(quiet.dispatch_one())
        self.assertFalse(quiet.dispatch_one())
        self.assertFalse(self.ledger.status()['can_request'])
        self.assertEqual(len(self.api.messages), 2)

    def test_disabled_tests_suppress_queue_preserve_receipts_and_recheck_dispatch(self):
        self.notifier.queue_test('delivered')
        self.assertTrue(self.notifier.dispatch_one())
        self.notifier.queue_test('pending')
        self.notifier.queue_test('sending')
        with self.ledger.transaction() as db:
            db.execute("UPDATE telegram_alerts SET state='sending' WHERE event_key='test:sending'")
        quiet = Notifier(self.ledger, dict(self.env, AI_TELEGRAM_TESTS_ENABLED='false'), self.api)
        self.assertEqual(quiet.status()['suppressed'], 2)
        # A separate CLI with an override cannot bypass the running worker's policy.
        self.notifier.queue_test('late-cli')
        self.assertFalse(quiet.dispatch_one())
        self.assertEqual(quiet.status()['suppressed'], 3)
        self.assertEqual(quiet.status()['delivered'], 1)
        with self.ledger.transaction() as db:
            self.assertEqual(db.execute("SELECT receipt FROM telegram_alerts WHERE event_key='test:delivered'").fetchone()[0], 1)
        self.assertEqual(len(self.api.messages), 1)

    def test_concurrent_dispatch_does_not_duplicate_inflight_delivery(self):
        self.notifier.queue_test('concurrency')
        started, release = threading.Event(), threading.Event()
        def wait():
            started.set()
            self.assertTrue(release.wait(5))
        self.api.before_send = wait
        second = Notifier(self.ledger, self.env, self.api)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.notifier.dispatch_one)
            self.assertTrue(started.wait(5))
            self.assertFalse(second.dispatch_one())
            release.set()
            self.assertTrue(first.result())
        self.assertEqual(len(self.api.messages), 1)

    def test_abandoned_lease_can_be_recovered(self):
        self.notifier.queue_test('crash')
        with self.ledger.transaction() as db:
            db.execute("UPDATE telegram_alerts SET state='sending', lease_until=?", (int(self.now + 120),))
        self.assertFalse(self.notifier.dispatch_one())
        self.now += 121
        self.assertTrue(self.notifier.dispatch_one())

    def test_disabled_channel_and_missing_credentials_cannot_send(self):
        for change in ({'AI_TELEGRAM_ALERTS_ENABLED': 'false'}, {'TELEGRAM_BOT_TOKEN': ''}):
            notifier = Notifier(self.ledger, dict(self.env, **change), self.api)
            with self.assertRaises(ValueError):
                notifier.queue_test('disabled')
            self.assertFalse(notifier.dispatch_one())


if __name__ == '__main__':
    unittest.main()
