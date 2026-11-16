import { planTelegramAlerts, dispatchTelegramAlerts } from './telegram-alerts.mjs';
export const RECIPIENTS = ['notuxius@gmail.com'];
export const LIMITS = { requests: 100000, rowsRead: 5000000, rowsWritten: 100000,
  databaseBytes: 500000000, totalDatabaseBytes: 5000000000, pagesDeployments: 500 };
export const THRESHOLD = 0.95;
const DAY = 86400000;
export const utcDay = now => new Date(now).toISOString().slice(0, 10);

export function evaluate(usage, previous = {}, now = Date.now()) {
  const day = utcDay(now), month = day.slice(0, 7);
  const daily = ['requests', 'rowsRead', 'rowsWritten'].filter(k => usage[k] >= LIMITS[k] * THRESHOLD);
  // Analytics may revise downward. Once tripped, latch daily limits until a new UTC day.
  if (previous.day === day) daily.push(...(previous.daily || []));
  const storage = [];
  if (usage.databases.some(db => db.bytes >= LIMITS.databaseBytes * THRESHOLD)) storage.push('databaseBytes');
  if (usage.databases.reduce((n, db) => n + db.bytes, 0) >= LIMITS.totalDatabaseBytes * THRESHOLD) storage.push('totalDatabaseBytes');
  const buildsBlocked = usage.pagesDeployments >= LIMITS.pagesDeployments * THRESHOLD ||
    (previous.month === month && previous.buildsBlocked === true);
  return { day, month, checkedAt: now, daily: [...new Set(daily)], storage, buildsBlocked,
    blocked: daily.length > 0 || storage.length > 0, usage };
}

async function jsonRequest(url, token, body, fetcher) {
  const response = await fetcher(url, { method: body ? 'POST' : 'GET',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    ...(body ? { body: JSON.stringify(body) } : {}), signal: AbortSignal.timeout(10000) });
  if (!response.ok) throw new Error(`Cloudflare API HTTP ${response.status}`);
  const data = await response.json();
  if (data.errors?.length || data.success === false) throw new Error('Cloudflare API rejected monitoring query');
  return data;
}
function count(value) {
  if (!Number.isFinite(value) || value < 0) throw new Error('Missing usage metric');
  return value;
}

export async function collectUsage(env, previous, now = Date.now(), fetcher = fetch) {
  if (!/^[a-f0-9]{32}$/.test(env.CF_ACCOUNT_ID || '') || !env.CF_ANALYTICS_TOKEN) throw new Error('Monitoring is not configured');
  const day = utcDay(now), base = `https://api.cloudflare.com/client/v4/accounts/${env.CF_ACCOUNT_ID}`;
  const query = `{viewer {accounts(filter:{accountTag:"${env.CF_ACCOUNT_ID}"}) {
    workersInvocationsAdaptive(limit:1,filter:{date_geq:"${day}",date_leq:"${day}"}) {sum {requests}}
    d1AnalyticsAdaptiveGroups(limit:1,filter:{date_geq:"${day}",date_leq:"${day}"}) {sum {rowsRead rowsWritten}}
  }}}`;
  const analytics = await jsonRequest('https://api.cloudflare.com/client/v4/graphql', env.CF_ANALYTICS_TOKEN, { query }, fetcher);
  const account = analytics.data?.viewer?.accounts?.[0];
  if (!account || !Array.isArray(account.workersInvocationsAdaptive) || !Array.isArray(account.d1AnalyticsAdaptiveGroups)) throw new Error('Missing account analytics');
  const requests = account.workersInvocationsAdaptive[0]?.sum?.requests ?? (account.workersInvocationsAdaptive.length === 0 ? 0 : NaN);
  const d1 = account.d1AnalyticsAdaptiveGroups[0]?.sum ?? (account.d1AnalyticsAdaptiveGroups.length === 0 ? { rowsRead: 0, rowsWritten: 0 } : {});
  // Free accounts allow at most 10 databases; pagination still avoids silent omissions.
  const databases = [];
  for (let page = 1; ; page++) {
    const data = await jsonRequest(`${base}/d1/database?per_page=100&page=${page}`, env.CF_ANALYTICS_TOKEN, null, fetcher);
    if (!Array.isArray(data.result)) throw new Error('Missing database inventory');
    for (const db of data.result) databases.push({ id: db.uuid, bytes: count(db.file_size) });
    if (data.result.length < 100) break;
    if (page >= 100) throw new Error('Database inventory truncated');
  }
  let pagesDeployments = previous?.usage?.pagesDeployments, pagesCheckedAt = previous?.usage?.pagesCheckedAt;
  if (pagesDeployments === undefined || previous.month !== day.slice(0, 7) || now - pagesCheckedAt >= 3600000) {
    pagesDeployments = await countPagesDeployments(env, now, fetcher);
    pagesCheckedAt = now;
  }
  return { requests: count(requests), rowsRead: count(d1.rowsRead), rowsWritten: count(d1.rowsWritten),
    databases, pagesDeployments: count(pagesDeployments), pagesCheckedAt };
}

// Conservative deployment budget: includes direct uploads, previews, failures and all
// account projects. It is not Cloudflare's internal billable-build counter.
export async function countPagesDeployments(env, now = Date.now(), fetcher = fetch) {
  const base = `https://api.cloudflare.com/client/v4/accounts/${env.CF_ACCOUNT_ID}/pages`;
  const monthStart = utcDay(now).slice(0, 7) + '-01T00:00:00.000Z';
  let count = 0;
  for (let p = 1; ; p++) {
    const projects = await jsonRequest(`${base}/projects?per_page=10&page=${p}`, env.CF_ANALYTICS_TOKEN, null, fetcher);
    if (!Array.isArray(projects.result)) throw new Error('Missing Pages inventory');
    for (const project of projects.result) {
      for (let page = 1; ; page++) {
        const deployments = await jsonRequest(`${base}/projects/${encodeURIComponent(project.name)}/deployments?per_page=25&page=${page}`, env.CF_ANALYTICS_TOKEN, null, fetcher);
        if (!Array.isArray(deployments.result)) throw new Error('Missing Pages deployment history');
        for (const d of deployments.result) {
          if (!Number.isFinite(Date.parse(d.created_on))) throw new Error('Missing deployment timestamp');
          if (Date.parse(d.created_on) >= Date.parse(monthStart)) count++;
        }
        // API returns newest first. Stop once the previous month is reached.
        if (deployments.result.length < 25 || deployments.result.some(d => Date.parse(d.created_on) < Date.parse(monthStart))) break;
        if (page >= 100) throw new Error('Deployment inventory truncated');
      }
    }
    if (projects.result.length < 10) break;
    if (p >= 100) throw new Error('Pages inventory truncated');
  }
  return count;
}

export function publicStatus(snapshot, now = Date.now()) {
  const fresh = snapshot && now >= snapshot.checkedAt && now - snapshot.checkedAt <= 120000 && snapshot.day === utcDay(now);
  return { blocked: !fresh || snapshot.blocked, buildsBlocked: !fresh || snapshot.buildsBlocked,
    monitoring: fresh ? 'ok' : 'unavailable' };
}

export class Monitor {
  constructor(storage, env, fetcher = fetch, clock = Date.now) {
    this.storage = storage; this.env = env; this.fetcher = fetcher; this.clock = clock;
  }
  async refresh() {
    const now = this.clock();
    const state = await this.storage.get('state') || { pending: [], delivered: {} };
    let snapshot;
    try { snapshot = evaluate(await collectUsage(this.env, state.snapshot, now, this.fetcher), state.snapshot, now); }
    catch { snapshot = null; }
    // Persist closure before sending: delivery failure can never reopen traffic.
    state.snapshot = snapshot || (state.snapshot ? { ...state.snapshot, checkedAt: 0 } : null);
    state.attemptedAt = now;
    planTelegramAlerts(state, snapshot, LIMITS, this.env, now);
    // Commit the gate and notification outbox together, before external delivery.
    await this.storage.put('state', state);
    await dispatchTelegramAlerts(state, this.storage, this.env, this.fetcher, now);
    // Explicitly deferred by the owner. Do not queue old alerts for later delivery.
    if (this.env.ALERTS_ENABLED !== 'true') {
      state.pending = [];
      await this.storage.put('state', state);
      return publicStatus(state.snapshot, now);
    }
    state.pending = state.pending.filter(item => RECIPIENTS.includes(item.recipient));
    const reasons = snapshot ? [...snapshot.daily, ...snapshot.storage, ...(snapshot.buildsBlocked ? ['pagesDeployments'] : [])] : ['monitoringUnavailable'];
    state.delivered = Object.fromEntries(Object.entries(state.delivered).filter(([, at]) => now - at < 40 * DAY));
    for (const reason of reasons) {
      const period = reason === 'pagesDeployments' ? utcDay(now).slice(0, 7) : utcDay(now);
      for (const recipient of RECIPIENTS) {
        const id = `${period}:${reason}:${recipient}`;
        if (!state.delivered[id] && !state.pending.some(item => item.id === id)) state.pending.push({ id, recipient, reason, at: now, usage: snapshot?.usage });
      }
    }
    await this.storage.put('state', state);
    for (const item of [...state.pending].slice(0, 10)) {
      try {
        if (!this.env.ALERT_EMAIL || !this.env.ALERT_FROM) throw new Error('Email is not configured');
        await this.env.ALERT_EMAIL.send({ to: item.recipient, from: this.env.ALERT_FROM,
          subject: `MTech: ${item.reason === 'monitoringUnavailable' ? 'quota monitoring unavailable' : '95% free quota reached'}`,
          text: `MTech quota alert\nTime: ${new Date(item.at).toISOString()}\nReason: ${item.reason}\n` +
            (item.reason === 'pagesDeployments' ? 'New deployments must stop. Existing pages remain available unless a runtime quota is also reached.\n' : 'Sites and bot enter maintenance; new submissions and database jobs stop.\n') +
            `Daily quotas reset at 00:00 UTC. Storage limits require space to be freed. Analytics may be delayed.\nUsage: ${JSON.stringify(item.usage || {})}` });
        state.delivered[item.id] = now;
        state.pending = state.pending.filter(p => p.id !== item.id);
        await this.storage.put('state', state);
      } catch { console.error('Quota email delivery failed; alert retained for retry'); }
    }
    return publicStatus(state.snapshot, now);
  }
}
