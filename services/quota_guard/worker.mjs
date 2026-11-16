import { LIMITS, Monitor, publicStatus } from './core.mjs';
import { telegramDeliveryStatus } from './telegram-alerts.mjs';

function usageText(snapshot) {
  if (publicStatus(snapshot).monitoring !== 'ok') return 'Cloudflare · свіжі дані недоступні.';
  const { usage } = snapshot;
  const sizes = usage.databases.map(db => db.bytes);
  const stamp = time => new Date(time).toISOString().replace('T', ' ').slice(0, 19) + ' UTC';
  const count = n => n.toLocaleString('uk-UA');
  const row = (name, used, limit) => name + ' — ' + count(used) + ' / ' + count(limit);
  return [
    'Cloudflare · ' + stamp(snapshot.checkedAt),
    row('Запити Workers за добу UTC', usage.requests, LIMITS.requests),
    row('Прочитано рядків D1 за добу UTC', usage.rowsRead, LIMITS.rowsRead),
    row('Записано рядків D1 за добу UTC', usage.rowsWritten, LIMITS.rowsWritten),
    row('Найбільша база D1, МБ', Math.max(0, ...sizes) / 1e6, LIMITS.databaseBytes / 1e6),
    row('Разом D1, МБ', sizes.reduce((a, b) => a + b, 0) / 1e6, LIMITS.totalDatabaseBytes / 1e6),
    row('Розгортання Pages за місяць', usage.pagesDeployments, LIMITS.pagesDeployments) + ' · ' + stamp(usage.pagesCheckedAt),
    'Захист трафіку ' + (snapshot.blocked ? 'блокує запити.' : 'дозволяє запити.')
  ].join('\n');
}

// Separate SQLite Durable Object storage keeps the gate usable when D1 is exhausted.
export class QuotaState {
  constructor(ctx, env) { this.ctx = ctx; this.monitor = new Monitor(ctx.storage, env); this.refreshing = null; }
  async fetch(request) {
    if (new URL(request.url).pathname === '/usage') {
      const state = await this.ctx.storage.get('state');
      return Response.json({ text: usageText(state?.snapshot) });
    }
    if (new URL(request.url).pathname === '/notification-status') {
      return Response.json(telegramDeliveryStatus(await this.ctx.storage.get('state')));
    }
    if (new URL(request.url).pathname === '/refresh' && request.method === 'POST') {
      if (!this.refreshing) this.refreshing = this.monitor.refresh().finally(() => { this.refreshing = null; });
      return Response.json(await this.refreshing);
    }
    const state = await this.ctx.storage.get('state');
    // Fresh deployments can wait up to 15 minutes for Cron propagation. Also
    // refresh on demand, coalescing concurrent readers into one analytics poll.
    if (!state?.attemptedAt || Date.now() - state.attemptedAt > 60000) {
      if (!this.refreshing) this.refreshing = this.monitor.refresh().finally(() => { this.refreshing = null; });
      return Response.json(await this.refreshing);
    }
    return Response.json(publicStatus(state?.snapshot));
  }
}
function state(env) { return env.STATE.get(env.STATE.idFromName('account-quotas')); }
export default {
  fetch(request, env) {
    const path = new URL(request.url).pathname;
    if (!['/status', '/usage', '/notification-status'].includes(path) || request.method !== 'GET') return new Response('Not found', { status: 404 });
    return state(env).fetch('https://quota.internal' + path);
  },
  scheduled(event, env, ctx) {
    ctx.waitUntil(state(env).fetch('https://quota.internal/refresh', { method: 'POST' }));
  }
};
