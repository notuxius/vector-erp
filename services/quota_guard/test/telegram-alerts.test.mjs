import test from 'node:test';
import assert from 'node:assert/strict';
import { evaluate, LIMITS, Monitor } from '../core.mjs';
import { planTelegramAlerts, dispatchTelegramAlerts, telegramDeliveryStatus, formatTelegramAlert } from '../telegram-alerts.mjs';
const now = Date.parse('2026-10-31T12:00:00Z');
const env = { TELEGRAM_ALERTS_ENABLED: 'true', ALERT_TELEGRAM_CHAT_ID: '123', ALERT_TELEGRAM_BOT_TOKEN: 'test-token', ALERTS_ENABLED: 'false' };
const usage = requests => ({ requests, rowsRead: 0, rowsWritten: 0, databases: [], pagesDeployments: 0, pagesCheckedAt: now });
const snapshot = (requests, previous = {}, time = now) => evaluate(usage(requests), previous, time);
const plan = (state, snap, extra = {}, time = now) => planTelegramAlerts(state, snap, LIMITS, { ...env, ...extra }, time);
test('90%, 95%, and UTC recovery produce one notification per transition, persisted across restart', () => {
  let state = {};
  plan(state, snapshot(89999)); assert.equal(state.telegramAlerts.pending.length, 0);
  plan(state, snapshot(90000)); assert.equal(state.telegramAlerts.pending.length, 1);
  state = structuredClone(state);
  plan(state, snapshot(91000)); assert.equal(state.telegramAlerts.pending.length, 1);
  const stopped = snapshot(95000);
  plan(state, stopped); assert.equal(state.telegramAlerts.pending.length, 2);
  plan(state, snapshot(100, stopped)); assert.equal(state.telegramAlerts.pending.length, 2);
  plan(state, snapshot(0, stopped, now + 86400000), {}, now + 86400000);
  assert.equal(state.telegramAlerts.pending.length, 1); // stale yesterday messages expire
  assert.equal(state.telegramAlerts.pending[0].kind, 'recovery');
});
test('monitoring outage does not claim quota recovery; one outage and recovery notification', () => {
  const state = {}; plan(state, snapshot(95000)); plan(state, null); plan(state, null);
  assert.equal(state.telegramAlerts.pending.length, 2);
  plan(state, snapshot(95000));
  const recovery = state.telegramAlerts.pending.at(-1);
  assert.equal(recovery.metric, 'monitoring'); assert.equal(recovery.kind, 'recovery');
  assert.match(formatTelegramAlert(recovery), /інші обмеження/);
});
test('private-only destination, disabling and changing recipient discard old queue', () => {
  const state = {}; plan(state, snapshot(95000));
  plan(state, snapshot(0), { ALERT_TELEGRAM_CHAT_ID: '456' }); assert.equal(state.telegramAlerts.pending.length, 0);
  plan(state, snapshot(95000), { ALERT_TELEGRAM_CHAT_ID: '-100123' }); assert.equal(state.telegramAlerts.enabled, false);
  plan(state, snapshot(95000), { TELEGRAM_ALERTS_ENABLED: 'false' }); assert.equal(state.telegramAlerts.pending.length, 0);
});
test('storage dropping below 95% sends recovery, without waiting for below 90%', () => {
  const state = {};
  plan(state, evaluate({ ...usage(0), databases: [{ bytes: 480000000 }] }, {}, now));
  plan(state, evaluate({ ...usage(0), databases: [{ bytes: 460000000 }] }, {}, now));
  assert.equal(state.telegramAlerts.pending.at(-1).kind, 'recovery');
  assert.equal(state.telegramAlerts.pending.at(-1).level, 90);
});
test('Telegram rate limit persists retry delay, preserves order, then records delivery', async () => {
  const state = {}; plan(state, snapshot(90000)); plan(state, snapshot(95000));
  let saved = structuredClone(state), calls = 0;
  const storage = { put: async (_, value) => { saved = structuredClone(value); } };
  await dispatchTelegramAlerts(state, storage, env, async () => {
    calls++; return Response.json({ ok: false, error_code: 429, parameters: { retry_after: 120 } }, { status: 429 });
  }, now);
  assert.equal(calls, 1); assert.equal(saved.telegramAlerts.pending[0].nextAttemptAt, now + 120000);
  await dispatchTelegramAlerts(saved, storage, env, () => assert.fail('Retry too early'), now + 60000);
  const restart = structuredClone(saved);
  await dispatchTelegramAlerts(restart, storage, env, async (url, options) => {
    const payload = JSON.parse(options.body); assert.equal(payload.chat_id, 123);
    assert.ok(!payload.text.includes('test-token'));
    return Response.json({ ok: true, result: { message_id: ++calls } });
  }, now + 120000);
  assert.equal(telegramDeliveryStatus(saved).queued, 0);
  assert.equal(telegramDeliveryStatus(saved).lastFailure, null);
});
test('test message queues once and gets an explicit delivery receipt', async () => {
  const state = {}, extra = { TELEGRAM_ALERT_TEST_ID: 'setup-test' };
  plan(state, snapshot(0), extra); plan(state, snapshot(0), extra);
  assert.equal(state.telegramAlerts.pending.length, 1);
  await dispatchTelegramAlerts(state, { put: async () => {} }, env, async (_, options) => {
    assert.match(JSON.parse(options.body).text, /ТЕСТ/);
    return Response.json({ ok: true, result: { message_id: 42 } });
  }, now);
  plan(state, snapshot(0), extra);
  assert.equal(state.telegramAlerts.pending.length, 0);
  assert.equal(telegramDeliveryStatus(state).lastTest.messageId, 42);
});
test('email-off production mode still sends Telegram after cutoff is durably committed', async () => {
  let data, messages = 0;
  const storage = { get: async () => structuredClone(data), put: async (_, value) => { data = structuredClone(value); } };
  const fetcher = async (url) => {
    if (url.includes('api.telegram.org')) {
      assert.equal(data.snapshot.blocked, true); messages++;
      return Response.json({ ok: true, result: { message_id: 1 } });
    }
    if (url.endsWith('/graphql')) return Response.json({ data: { viewer: { accounts: [{
      workersInvocationsAdaptive: [{ sum: { requests: 95000 } }], d1AnalyticsAdaptiveGroups: [{ sum: { rowsRead: 0, rowsWritten: 0 } }]
    }] } } });
    return Response.json({ success: true, result: [] });
  };
  const monitor = new Monitor(storage, { ...env, CF_ACCOUNT_ID: 'a'.repeat(32), CF_ANALYTICS_TOKEN: 'test' }, fetcher, () => now);
  const result = await monitor.refresh();
  assert.equal(result.blocked, true); assert.equal(messages, 1); assert.deepEqual(data.pending, []);
});
