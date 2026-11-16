from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from guard import Blocked, Ledger, MAX_INPUT, MAX_OUTPUT, INPUT_RATE, OUTPUT_RATE, USD, PRICE_VALID_UNTIL
from server import generate, usage_text


class SpendingGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / 'ledger.sqlite3')
        self.now = PRICE_VALID_UNTIL - 86400
        self.ledger = Ledger(self.path, lambda: self.now)

    def tearDown(self):
        self.temp.cleanup()

    def fund(self, allowance=100 * USD, spent=0, period='pilot-grant', exclusive=True):
        self.ledger.snapshot({'period': period, 'allowance': allowance,
                              'remaining': allowance - spent, 'observed_at': int(self.now),
                              'expires_at': int(self.now + 86400),
                              'source': 'test-only verified grant', 'exclusive': exclusive})

    def enable(self, **kwargs):
        self.fund(**kwargs)
        self.ledger.enable()

    def test_empty_or_disabled_never_calls_provider(self):
        provider = Mock()
        with self.assertRaisesRegex(Blocked, 'usage_budget_not_configured'):
            generate(self.ledger, {'prompt': 'test'}, provider)
        self.fund()
        with self.assertRaisesRegex(Blocked, 'ai_disabled'):
            generate(self.ledger, {'prompt': 'test'}, provider)
        provider.assert_not_called()

    def test_budget_only_setup_and_legacy_policy_need_no_funding_confirmation(self):
        self.ledger.snapshot({'period': 'pilot-grant', 'allowance': 100 * USD})
        self.ledger.enable()
        self.fund(spent=USD, exclusive=False)
        self.assertTrue(self.ledger.status()['can_request'])
        self.ledger.snapshot({'period': 'pilot-grant', 'allowance': 90 * USD})
        self.assertEqual(self.ledger.status()['accounted_usage_usd'], 1)
        self.assertTrue(self.ledger.status()['can_request'])

    def test_zero_budget_stops_an_enabled_gateway(self):
        self.enable()
        self.fund(allowance=0)
        self.assertFalse(self.ledger.status()['can_request'])
        with self.assertRaisesRegex(Blocked, 'usage_budget_not_configured'):
            self.ledger.reserve(100)

    def test_read_only_usage_survives_restart_and_only_reports_observed_limits(self):
        self.enable(allowance=10 * USD, spent=USD)
        self.assertIn('ще не отримав', usage_text(self.ledger.status()))
        self.ledger.observe_api_limits({'Authorization': 'private', 'x-ratelimit-limit-tokens': 'invalid',
                                       'x-ratelimit-remaining-requests': '0', 'x-ratelimit-limit-requests': '50',
                                       'x-ratelimit-reset-requests': '2m1.5s'})
        report = usage_text(Ledger(self.path).status(include_events=False))
        self.assertIn('залишок 0, ліміт 50', report)
        self.assertIn('2m1.5s', report)
        self.assertIn('UTC', report)
        self.assertNotIn('private', report)
        self.assertIn('$8.500000', report)
        self.assertEqual(self.ledger.status()['reserved_usd'], 0)

    def test_reserves_before_crossing_cutoff_without_provider_call(self):
        self.enable(spent=int(94.9 * USD))
        self.assertEqual(self.ledger.status()['block_reason'], 'next_request_would_cross_95_percent')
        self.assertFalse(self.ledger.status()['can_request'])
        provider = Mock()
        with self.assertRaisesRegex(Blocked, 'next_request_would_cross'):
            generate(self.ledger, {'prompt': 'test'}, provider)
        provider.assert_not_called()

    def test_boundary_exactly_95_then_stops(self):
        reserve = MAX_INPUT * INPUT_RATE + MAX_OUTPUT * OUTPUT_RATE
        self.enable(spent=95 * USD - reserve)
        request = self.ledger.reserve(MAX_OUTPUT)
        self.assertEqual(self.ledger.status()['block_reason'], 'cutoff_95_percent')
        self.ledger.settle(request, MAX_INPUT, MAX_OUTPUT, MAX_OUTPUT)
        with self.assertRaisesRegex(Blocked, 'cutoff_95_percent'):
            self.ledger.reserve(1)

    def test_concurrent_reservations_cannot_overspend(self):
        self.enable(allowance=USD)
        def reserve(_):
            try:
                return self.ledger.reserve(MAX_OUTPUT)
            except Blocked:
                return None
        with ThreadPoolExecutor(max_workers=12) as pool:
            accepted = [x for x in pool.map(reserve, range(30)) if x]
        self.assertEqual(len(accepted), 3)
        self.assertLessEqual(self.ledger.status()['reserved_usd'], 0.95)

    def test_success_settles_usage_including_output_and_releases_difference(self):
        self.enable()
        provider = Mock(return_value={'model': 'gpt-6-luna', 'service_tier': 'default',
                                     'status': 'completed', 'usage': {'input_tokens': 200, 'output_tokens': 30},
                                     'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'hello'}]}]})
        self.assertEqual(generate(self.ledger, {'prompt': 'test'}, provider)['text'], 'hello')
        self.assertEqual(self.ledger.status()['reserved_usd'], 0)
        self.assertEqual(self.ledger.status()['accounted_usage_usd'], (200 * INPUT_RATE + 30 * OUTPUT_RATE) / USD)
        self.assertFalse(provider.call_args.args[0]['store'])
        self.assertEqual(provider.call_args.args[0]['service_tier'], 'default')

    def test_provider_error_keeps_hold_and_only_diagnostic_ids(self):
        import json
        self.enable()
        with patch.dict('os.environ', OPENAI_API_KEY='test'), patch('server.build_opener') as opener:
            error = HTTPError('https://api.openai.com', 429, 'private details', {'x-request-id': 'req_test'}, None)
            opener.return_value.open.side_effect = error
            with self.assertRaises(Blocked):
                generate(self.ledger, {'prompt': 'private prompt'})
        sent = opener.return_value.open.call_args.args[0]
        with self.ledger.transaction() as db:
            row = db.execute('SELECT request_id, evidence, state FROM request_incidents JOIN requests ON id=request_id').fetchone()
        self.assertEqual(sent.get_header('X-client-request-id'), row['request_id'])
        self.assertEqual(row['state'], 'uncertain')
        self.assertEqual(json.loads(row['evidence']), {
            'http_status': 429, 'provider_request_id': 'req_test', 'error_type': 'HTTPError'})
        self.assertFalse(self.ledger.status()['can_request'])

    def test_precise_usage_prices_reads_writes_and_output(self):
        self.enable()
        request = self.ledger.reserve(100)
        self.ledger.settle(request, 1000, 100, 100,
                           {'cached_tokens': 200, 'cache_write_tokens': 300})
        self.assertEqual(self.ledger.status()['accounted_usage_usd'],
                         (500 * 100 + 200 * 10 + 300 * 125 + 100 * 500) / USD)

    def test_long_context_applies_both_price_multipliers(self):
        self.enable()
        request = self.ledger.reserve(100)
        self.ledger.settle(request, 300_000, 100, 100,
                           {'cached_tokens': 0, 'cache_write_tokens': 0})
        self.assertEqual(self.ledger.status()['accounted_usage_usd'],
                         (300_000 * 200 + 100 * 750) / USD)

    def test_invalid_cache_accounting_is_not_released(self):
        self.enable()
        request = self.ledger.reserve(100)
        with self.assertRaises(ValueError):
            self.ledger.settle(request, 100, 10, 100,
                               {'cached_tokens': 200, 'cache_write_tokens': 0})
        self.assertGreater(self.ledger.status()['reserved_usd'], 0)

    def test_timeout_retains_reservation_disables_and_never_retries(self):
        self.enable()
        provider = Mock(side_effect=TimeoutError)
        with self.assertRaisesRegex(Blocked, 'outcome_uncertain'):
            generate(self.ledger, {'prompt': 'test'}, provider)
        self.assertEqual(provider.call_count, 1)
        self.assertFalse(self.ledger.status()['enabled'])
        self.assertGreater(self.ledger.status()['reserved_usd'], 0)
        with self.assertRaisesRegex(Blocked, 'unresolved_request'):
            self.ledger.enable()

    def test_malformed_usage_keeps_full_hold(self):
        self.enable()
        provider = Mock(return_value={'model': 'gpt-6-luna', 'service_tier': 'default',
                                     'status': 'completed', 'usage': {}, 'output': []})
        with self.assertRaises(Blocked):
            generate(self.ledger, {'prompt': 'test'}, provider)
        self.assertGreater(self.ledger.status()['reserved_usd'], 0)

    def test_no_model_tools_or_price_tier_override(self):
        self.enable()
        provider = Mock()
        for extra in ({'model': 'other'}, {'tools': []}, {'service_tier': 'priority'}):
            with self.assertRaises(ValueError):
                generate(self.ledger, dict(prompt='test', **extra), provider)
        for limit in (True, -1, 2049, '100'):
            with self.assertRaises(ValueError):
                generate(self.ledger, {'prompt': 'test', 'max_output_tokens': limit}, provider)
        provider.assert_not_called()

    def test_restart_retains_completed_usage(self):
        self.enable()
        request = self.ledger.reserve(100)
        self.ledger.settle(request, 1000, 100, 100)
        second = Ledger(self.path, lambda: self.now)
        second.recover()
        self.assertEqual(second.status()['accounted_usage_usd'], self.ledger.status()['accounted_usage_usd'])
        self.assertTrue(second.status()['can_request'])

    def test_crash_with_inflight_request_keeps_hold_and_disables(self):
        self.enable()
        self.ledger.reserve(100)
        before = self.ledger.status()['reserved_usd']
        second = Ledger(self.path, lambda: self.now)
        second.recover()
        self.assertEqual(second.status()['reserved_usd'], before)
        self.assertFalse(second.status()['enabled'])

    def test_old_snapshot_allows_requests_without_refresh_and_preserves_accounting(self):
        self.enable()
        self.now += 3601
        request = self.ledger.reserve(100)
        self.ledger.settle(request, 1000, 20, 100)
        self.ledger = Ledger(self.path, lambda: self.now)
        self.ledger.recover()
        self.now += 3601
        self.ledger.disable()
        self.ledger.enable()
        status = self.ledger.status()
        self.assertTrue(status['can_request'])
        self.assertFalse(status['balance_refresh_required'])
        self.assertEqual(status['accounted_usage_usd'], (1000 * INPUT_RATE + 20 * OUTPUT_RATE) / USD)

    def test_old_snapshot_still_enforces_cutoff(self):
        self.enable(spent=95 * USD - 1)
        self.now += 3601
        with self.assertRaisesRegex(Blocked, 'next_request_would_cross_95_percent'):
            self.ledger.reserve(100)
        self.fund(spent=95 * USD)
        self.now += 3601
        with self.assertRaisesRegex(Blocked, 'cutoff_95_percent'):
            self.ledger.reserve(100)
    def test_expired_pricing_blocks(self):
        self.enable()
        self.now = PRICE_VALID_UNTIL + 1
        self.fund()
        with self.assertRaisesRegex(Blocked, 'pricing_needs_verification'):
            self.ledger.reserve(100)

    def test_allowance_scales_without_resetting_spend(self):
        self.enable(spent=19 * USD)
        self.fund(allowance=20 * USD, spent=19 * USD)
        self.assertEqual(self.ledger.status()['cutoff_usd'], 19)
        self.assertEqual(self.ledger.status()['block_reason'], 'cutoff_95_percent')
        self.fund(allowance=200 * USD)
        self.assertEqual(self.ledger.status()['cutoff_usd'], 190)
        self.assertEqual(self.ledger.status()['accounted_usage_usd'], 19)

    def test_snapshot_does_not_forget_outstanding_requests(self):
        self.enable()
        self.ledger.reserve(100)
        held = self.ledger.status()['reserved_usd']
        self.fund(allowance=200 * USD)
        self.assertEqual(self.ledger.status()['reserved_usd'], held)

    def test_new_period_requires_disabled_and_no_holds(self):
        self.enable()
        with self.assertRaises(Blocked):
            self.fund(period='new-grant')
        self.ledger.reserve(100)
        self.ledger.disable()
        with self.assertRaises(Blocked):
            self.fund(period='new-grant')

    def test_new_period_stays_disabled(self):
        self.fund(spent=95 * USD)
        with self.assertRaisesRegex(Blocked, 'cutoff_95_percent'):
            self.ledger.enable()
        self.fund(period='new-grant')
        self.assertFalse(self.ledger.status()['enabled'])
        self.assertEqual(self.ledger.status()['accounted_usage_usd'], 0)

    def test_alerts_deduplicated_and_persisted(self):
        self.fund(spent=95 * USD)
        self.fund(spent=95 * USD)
        self.assertEqual([e['threshold'] for e in self.ledger.status()['events']], [75, 95])
        second = Ledger(self.path, lambda: self.now)
        self.assertEqual(len(second.status()['events']), 2)

    def test_legacy_credit_dates_do_not_gate_import_enable_or_requests(self):
        self.ledger.snapshot({'period': 'legacy', 'allowance': 7 * USD, 'remaining': 6 * USD,
                              'observed_at': 1, 'expires_at': 2, 'source': '', 'exclusive': False})
        self.ledger.enable()
        request = self.ledger.reserve(100)
        self.ledger.settle(request, 100, 10, 100)
        self.assertTrue(self.ledger.status()['can_request'])
        self.assertGreater(self.ledger.status()['accounted_usage_usd'], 1)

    def test_database_failure_never_calls_provider(self):
        provider = Mock()
        ledger = Mock()
        ledger.reserve.side_effect = OSError('disk unavailable')
        with self.assertRaises(OSError):
            generate(ledger, {'prompt': 'test'}, provider)
        provider.assert_not_called()


if __name__ == '__main__':
    unittest.main()
