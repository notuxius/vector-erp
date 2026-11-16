const LABELS = {
  requests: 'Запити Workers / Pages Functions', rowsRead: 'Прочитані рядки D1',
  rowsWritten: 'Записані рядки D1', databaseBytes: 'Розмір найбільшої бази D1',
  totalDatabaseBytes: 'Загальний розмір D1', pagesDeployments: 'Розгортання Pages',
  monitoring: 'Перевірка використання Cloudflare'
};

export function alertLevels(snapshot, limits) {
  if (!snapshot) return { monitoring: { level: 95 } };
  const values = { ...snapshot.usage,
    databaseBytes: Math.max(0, ...snapshot.usage.databases.map(db => db.bytes)),
    totalDatabaseBytes: snapshot.usage.databases.reduce((n, db) => n + db.bytes, 0) };
  return Object.fromEntries(Object.keys(limits).map(metric => {
    const used = values[metric], limit = limits[metric];
    const latched = snapshot.daily.includes(metric) || snapshot.storage.includes(metric) ||
      (metric === 'pagesDeployments' && snapshot.buildsBlocked);
    return [metric, { used, limit, level: latched || used >= limit * .95 ? 95 : used >= limit * .9 ? 90 : 0 }];
  }));
}

export function formatTelegramAlert(event) {
  if (event.kind === 'test') return '🧪 ТЕСТ · MTech: сповіщення про квоти\n\n' +
    'Особисті сповіщення підключено.\n90% — попередження.\n95% — повідомлення про зупинку обробки.\n' +
    'Після відновлення — повідомлення про доступність.\n\n' +
    'Це тест доставки, він не вмикає технічне обслуговування. Email-сповіщення вимкнені.';
  const title = event.kind === 'recovery' ? '✅ MTech · Відновлення' :
    event.metric === 'monitoring' ? '⚠️ MTech · Моніторинг недоступний' :
    event.level === 95 ? '⛔ MTech · Досягнуто порога 95%' : '⚠️ MTech · Використано 90% квоти';
  let text = `${title}\n\n${LABELS[event.metric] || event.metric}`;
  if (Number.isFinite(event.used) && event.limit) text += `: ${event.used.toLocaleString('uk-UA')} / ${event.limit.toLocaleString('uk-UA')} (${(event.used / event.limit * 100).toFixed(1)}%).`;
  if (event.metric === 'pagesDeployments') {
    text += event.kind === 'recovery' ? '\nБюджет розгортань знову доступний.' : event.level === 95 ?
      '\nНові розгортання слід призупинити. Чинний сайт не вимикається лише через цей бюджет.' : '\nНаближається поріг зупинки нових розгортань.';
  } else if (event.kind === 'recovery') {
    text += event.blocked ? '\nЦей показник відновився, але інші обмеження ще утримують технічне обслуговування.' : event.fromLevel === 90 ?
      '\nВикористання знову нижче порога попередження. Робота триває.' :
      '\nСайти й бот можуть знову працювати. Оновлення стану може тривати до 15 секунд.';
  } else if (event.level === 95) {
    text += '\nУвімкнено технічне обслуговування: нові звернення та завдання бази даних зупинено.';
  } else text += '\nРобота триває. Поріг зупинки — 95%.';
  return text + '\n\nДобові квоти скидаються о 00:00 UTC. Дискові квоти потребують звільнення місця. Статистика може надходити із затримкою.';
}

export function planTelegramAlerts(state, snapshot, limits, env, now) {
  const previous = state.telegramAlerts || { levels: {}, pending: [], sequence: 0 };
  const target = String(env.ALERT_TELEGRAM_CHAT_ID || '');
  if (env.TELEGRAM_ALERTS_ENABLED !== 'true' || !/^[1-9]\d*$/.test(target)) {
    state.telegramAlerts = { levels: {}, pending: [], sequence: previous.sequence, enabled: false };
    return;
  }
  // A changed destination cannot inherit someone else's queued notifications.
  const alerts = previous.target === target ? previous : { levels: {}, pending: [], sequence: 0 };
  alerts.enabled = true; alerts.target = target;
  alerts.pending = alerts.pending.filter(event => now - event.at < 86400000);
  const enqueue = event => alerts.pending.push({ ...event, id: ++alerts.sequence, at: now, attempts: 0, nextAttemptAt: now });
  const levels = alertLevels(snapshot, limits);
  const before = alerts.levels;
  // A missing snapshot does not mean that previously high quotas have recovered.
  if (!snapshot) {
    if (!before.monitoring) enqueue({ kind: 'threshold', metric: 'monitoring', level: 95, blocked: true });
    alerts.levels = { ...before, monitoring: 95 };
  } else {
    if (before.monitoring) enqueue({ kind: 'recovery', metric: 'monitoring', level: 0, blocked: snapshot.blocked });
    for (const [metric, current] of Object.entries(levels)) {
      const prior = before[metric] || 0;
      if (current.level > prior) enqueue({ kind: 'threshold', metric, ...current, blocked: snapshot.blocked });
      else if (prior > current.level && (prior === 95 || current.level === 0)) enqueue({ kind: 'recovery', metric, fromLevel: prior, ...current, blocked: snapshot.blocked });
    }
    alerts.levels = Object.fromEntries(Object.entries(levels).map(([metric, value]) => [metric, value.level]));
  }
  const testId = env.TELEGRAM_ALERT_TEST_ID;
  if (testId && testId !== alerts.testId) {
    enqueue({ kind: 'test', testId }); alerts.testId = testId;
  }
  // Bounded backlog during prolonged Telegram outages; keep the newest state changes.
  alerts.pending = alerts.pending.slice(-100);
  state.telegramAlerts = alerts;
}

export async function dispatchTelegramAlerts(state, storage, env, fetcher, now) {
  const alerts = state.telegramAlerts;
  if (!alerts?.enabled || !env.ALERT_TELEGRAM_BOT_TOKEN) return;
  for (const event of alerts.pending.slice(0, 3)) {
    if (event.nextAttemptAt > now) break;
    let result;
    try {
      const response = await fetcher(`https://api.telegram.org/bot${env.ALERT_TELEGRAM_BOT_TOKEN}/sendMessage`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: Number(alerts.target), text: formatTelegramAlert(event),
          link_preview_options: { is_disabled: true } }), signal: AbortSignal.timeout(7000)
      });
      result = await response.json();
      if (!response.ok || !result.ok || !Number.isSafeInteger(result.result?.message_id)) throw new Error('Telegram rejected alert');
      const receipt = { kind: event.kind, at: now, messageId: result.result.message_id };
      alerts.lastDelivery = receipt;
      alerts.lastFailure = null;
      if (event.kind === 'test') alerts.lastTest = { ...receipt, testId: event.testId };
      alerts.pending = alerts.pending.filter(item => item.id !== event.id);
    } catch {
      event.attempts++;
      const retryAfter = Number(result?.parameters?.retry_after) || 0;
      event.nextAttemptAt = now + Math.max(retryAfter, Math.min(3600, 60 * 2 ** Math.min(event.attempts - 1, 6))) * 1000;
      // Invalid credentials or a blocked bot require operator intervention, not a retry storm.
      if ([400, 401, 403, 404].includes(result?.error_code)) event.nextAttemptAt = now + 3600000;
      alerts.lastFailure = { at: now, code: Number(result?.error_code) || 0 };
    }
    await storage.put('state', state);
    // Preserve notification order when a delivery is pending.
    if (alerts.pending.some(item => item.id === event.id)) break;
  }
}

export function telegramDeliveryStatus(state) {
  const alerts = state?.telegramAlerts;
  return { enabled: alerts?.enabled === true, queued: alerts?.pending?.length || 0,
    lastTest: alerts?.lastTest || null, lastDelivery: alerts?.lastDelivery || null,
    lastFailure: alerts?.lastFailure || null };
}
