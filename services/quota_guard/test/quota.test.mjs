import test from 'node:test';
import assert from 'node:assert/strict';
import { evaluate, publicStatus, Monitor, collectUsage, countPagesDeployments } from '../core.mjs';
import { quotaStatus } from '../client.mjs';
import { QuotaState } from '../worker.mjs';
import bot from '../../telegram_worker/worker.mjs';
import site from '../../../sites/mtech-design-refresh/auth.mjs';

const now = Date.parse('2026-10-31T12:00:00Z');
const usage = extra => ({ requests: 10, rowsRead: 20, rowsWritten: 30, databases: [{ id: 'db', bytes: 1000 }], pagesDeployments: 5, pagesCheckedAt: now, ...extra });
test('private usage report reads fresh measurements and withholds stale counts and database IDs', async () => {
  let snapshot = evaluate(usage({databases: [{id:'private-id', bytes:12500000}]}));
  const object = new QuotaState({storage:{get: async () => ({snapshot})}}, {});
  const report = async () => (await (await object.fetch(new Request('https://quota.internal/usage'))).json()).text;
  assert.match(await report(), /Запити Workers за добу UTC — 10/);
  assert.match(await report(), /Розгортання Pages за місяць — 5/);
  assert.doesNotMatch(await report(), /private-id/);
  snapshot.checkedAt = 0;
  assert.match(await report(), /дані недоступні/);
});
test('on-demand checks back off after a failed poll and remain closed', async () => {
  const object = new QuotaState({ storage: { get: async () => ({ attemptedAt: Date.now(), snapshot: null }) } }, {});
  object.monitor.refresh = () => assert.fail('Must not retry every request during an API outage');
  assert.equal((await (await object.fetch(new Request('https://quota.internal/status'))).json()).blocked, true);
});
test('95% boundaries stop runtime; Pages deployments only stop future builds', () => {
  for (const [metric, limit] of [['requests', 95000], ['rowsRead', 4750000], ['rowsWritten', 95000]]) {
    assert.equal(evaluate(usage({ [metric]: limit - 1 }), {}, now).blocked, false);
    assert.equal(evaluate(usage({ [metric]: limit }), {}, now).blocked, true);
  }
  const builds = evaluate(usage({ pagesDeployments: 475 }), {}, now);
  assert.equal(builds.blocked, false); assert.equal(builds.buildsBlocked, true);
  assert.equal(evaluate(usage({ databases: [{ bytes: 475000000 }] }), {}, now).blocked, true);
  assert.equal(evaluate(usage({ databases: Array.from({ length: 20 }, () => ({ bytes: 240000000 })) }), {}, now).blocked, true);
});
test('daily latch survives revised analytics, resets at UTC; storage persists', () => {
  const stopped = evaluate(usage({ requests: 95000 }), {}, now);
  assert.equal(evaluate(usage(), stopped, now + 60000).blocked, true);
  assert.equal(evaluate(usage(), stopped, now + 86400000).blocked, false);
  const full = usage({ databases: [{ bytes: 475000000 }] });
  assert.equal(evaluate(full, evaluate(full, {}, now), now + 86400000).blocked, true);
  assert.equal(evaluate(usage(), evaluate(full, {}, now), now + 86400000).blocked, false);
});
test('missing, stale and yesterday snapshots fail closed', () => {
  assert.equal(publicStatus(null, now).blocked, true);
  assert.equal(publicStatus(evaluate(usage(), {}, now), now + 120001).blocked, true);
  const beforeMidnight = Date.parse('2026-10-31T23:59:59Z');
  assert.equal(publicStatus(evaluate(usage(), {}, beforeMidnight), beforeMidnight + 1001).blocked, true);
});
const env = { CF_ACCOUNT_ID: 'a'.repeat(32), CF_ANALYTICS_TOKEN: 'test-token', ALERT_FROM: 'alerts@example.com', ALERTS_ENABLED: 'true' };
function api(metric = 95000) {
  return async url => {
    if (url.endsWith('/graphql')) return Response.json({ data: { viewer: { accounts: [{
      workersInvocationsAdaptive: [{ sum: { requests: metric } }], d1AnalyticsAdaptiveGroups: [{ sum: { rowsRead: 1, rowsWritten: 2 } }]
    }] } } });
    if (url.includes('/d1/database')) return Response.json({ success: true, result: [{ uuid: 'db', file_size: 1000 }] });
    if (url.includes('/deployments')) return Response.json({ success: true, result: [{ created_on: '2026-10-31T01:00:00Z' }, { created_on: '2026-09-30T01:00:00Z' }] });
    return Response.json({ success: true, result: [{ name: 'project' }] });
  };
}
test('analytics failure cannot become zero usage; Pages counts current month only', async () => {
  assert.equal(await countPagesDeployments(env, now, api()), 1);
  const metrics = await collectUsage(env, null, now, api()); assert.equal(metrics.requests, 95000);
  await assert.rejects(collectUsage(env, null, now, async () => Response.json({ errors: [{ message: 'denied' }] })));
  await assert.rejects(collectUsage(env, null, now, async () => Response.json({ data: {} })));
});
test('emails deduplicate per recipient; failed recipient retries after restart; cutoff stored first', async () => {
  let data, fail = true; const sent = [];
  const storage = { get: async () => structuredClone(data), put: async (_, value) => { data = structuredClone(value); } };
  const configured = { ...env, ALERT_EMAIL: { send: async msg => {
    assert.equal(data.snapshot.blocked, true);
    assert.equal(msg.to, 'notuxius@gmail.com');
    if (fail) throw new Error('temporary');
    sent.push(msg.to);
  } } };
  await new Monitor(storage, configured, api(), () => now).refresh();
  assert.deepEqual(sent, []); assert.equal(data.pending.length, 1);
  fail = false;
  await new Monitor(storage, configured, api(), () => now + 60000).refresh();
  await new Monitor(storage, configured, api(), () => now + 120000).refresh();
  assert.deepEqual(sent, ['notuxius@gmail.com']); assert.equal(data.pending.length, 0);
});
test('monitor outage invalidates old permission and queues an alert', async () => {
  let data = { snapshot: evaluate(usage(), {}, now), delivered: {}, pending: [] };
  const storage = { get: async () => structuredClone(data), put: async (_, value) => { data = structuredClone(value); } };
  const result = await new Monitor(storage, env, async () => { throw new Error('offline'); }, () => now).refresh();
  assert.equal(result.blocked, true); assert.equal(data.pending.length, 1);
});
test('email-disabled mode still closes traffic without sending or retaining deferred alerts', async () => {
  let data = { delivered: {}, pending: [{ id: 'old', recipient: 'office@mtech.com.ua' }] };
  const storage = { get: async () => structuredClone(data), put: async (_, value) => { data = structuredClone(value); } };
  const result = await new Monitor(storage, { ...env, ALERTS_ENABLED: 'false',
    ALERT_EMAIL: { send() { assert.fail('Email is disabled'); } } }, api(), () => now).refresh();
  assert.equal(result.blocked, true); assert.equal(data.pending.length, 0);
});
test('client fails closed and coalesces concurrent checks; manual maintenance needs no binding', async () => {
  assert.equal((await quotaStatus({ QUOTA_GUARD_ENABLED: 'true' })).blocked, true);
  assert.equal((await quotaStatus({ MAINTENANCE_MODE: 'true' })).blocked, true);
  let calls = 0;
  const e = { QUOTA_GUARD_ENABLED: 'true', QUOTA_GUARD: { fetch: async () => { calls++; return Response.json({ blocked: true }); } } };
  assert.ok((await Promise.all(Array.from({ length: 10 }, () => quotaStatus(e)))).every(s => s.blocked));
  assert.equal(calls, 1);
  assert.equal((await quotaStatus({ ...e, QUOTA_GUARD: { fetch: async () => { throw new Error('down'); } } })).blocked, true);
});
test('bot rejects new work with Telegram maintenance reply and no D1 or dispatch', async () => {
  const e = { BOT_ENABLED: 'true', WEBHOOK_SECRET: 'test', MAINTENANCE_MODE: 'true',
    DB: { prepare() { assert.fail('D1 must not be accessed'); } } };
  const ctx = { waitUntil() { assert.fail('No dispatch while closed'); } };
  const request = update => new Request('https://bot.test/webhook', { method: 'POST',
    headers: { 'X-Telegram-Bot-Api-Secret-Token': 'test' }, body: JSON.stringify(update) });
  const reply = await bot.fetch(request({ update_id: 1, message: { chat: { id: 123, type: 'private' }, from: { id: 123 } } }), e, ctx);
  assert.equal(reply.status, 200); assert.equal((await reply.json()).method, 'sendMessage');
  const cb = await bot.fetch(request({ update_id: 2, callback_query: { id: 'cb' } }), e, ctx);
  assert.equal((await cb.json()).method, 'answerCallbackQuery');
  await bot.scheduled({}, e, ctx);
  assert.equal((await bot.fetch(new Request('https://bot.test/health'), e, ctx)).status, 503);
  assert.equal((await bot.fetch(new Request('https://bot.test/webhook', { method: 'POST', body: '{}' }), e, ctx)).status, 401);
});
test('site stops authentication and assets during maintenance; never cache maintenance', async () => {
  for (const path of ['/', '/login', '/assets/photo.png', '/auth/email']) {
    const response = await site.fetch(new Request('https://site.test' + path), {
      MAINTENANCE_MODE: 'true', ASSETS: { fetch() { assert.fail('Assets must not be served'); } }
    });
    assert.equal(response.status, 503); assert.equal(response.headers.get('Cache-Control'), 'no-store');
    assert.match(await response.text(), /Технічне обслуговування/);
  }
});
