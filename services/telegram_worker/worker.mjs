import content from '../telegram_bot/content.json' with { type: 'json' };
import { Store } from './store.mjs';
import { AIQueue, cloudUsage } from './ai.mjs';
import { quotaStatus, maintenanceText } from '../quota_guard/client.mjs';

export class Telegram {
  constructor(env) { this.env = env; }
  async startTyping(chatId) {
    const timer = this.env.REPLY_TYPING.getByName(String(chatId));
    const response = await timer.fetch('https://typing.internal/start');
    if (!response.ok) throw new Error('Could not start typing indicator');
  }
  async call(method, payload, answerRowId) {
    if (method === 'sendChatAction') {
      const timer = this.env.REPLY_TYPING.getByName(String(payload.chat_id));
      const response = await timer.fetch('https://typing.internal/refresh');
      if (!response.ok) throw new Error('Could not refresh typing indicator');
      return;
    }
    if (answerRowId !== undefined) {
      const timer = this.env.REPLY_TYPING.getByName(String(payload.chat_id));
      const response = await timer.fetch('https://typing.internal/answer', {
        method: 'POST', body: JSON.stringify({ rowId: answerRowId, payload })
      });
      if (!response.ok) throw Object.assign(new Error('Telegram answer delivery failed'), await response.json());
      return;
    }
    return this.send(method, payload);
  }
  async send(method, payload) {
    let result;
    try {
      const response = await fetch(`https://api.telegram.org/bot${this.env.TELEGRAM_BOT_TOKEN}/${method}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        signal: AbortSignal.timeout(7000)
      });
      result = await response.json();
    } catch { throw Object.assign(new Error('Telegram network error'), { code: 0 }); }
    if (!result.ok) throw Object.assign(new Error('Telegram API error'), { code: result.error_code, retryAfter: result.parameters?.retry_after });
    return result.result;
  }
}
// One alarm per owner chat survives the webhook ending and the relay picking up the job.
export class ReplyTyping {
  constructor(ctx, env) { this.ctx = ctx; this.storage = ctx.storage; this.env = env; }
  async fetch(request) {
    if (new URL(request.url).pathname === '/refresh') {
      // Posting the question or progress message clears Telegram's current indicator.
      // Restart its refresh cadence here, serialized with answer delivery.
      return this.ctx.blockConcurrencyWhile(async () => {
        await this.refreshTyping();
        return new Response(null, { status: 204 });
      });
    }
    if (new URL(request.url).pathname === '/answer') {
      const { rowId, payload } = await request.json();
      if (!Number.isSafeInteger(rowId) || rowId <= 0 || payload?.chat_id !== Number(this.env.TELEGRAM_STAFF_CHAT_ID))
        return Response.json({ code: 400 }, { status: 400 });
      // Finish any in-flight typing action before sending the message that clears it.
      return this.ctx.blockConcurrencyWhile(async () => {
        if (rowId > (await this.storage.get('lastAnswerRow') ?? 0)) {
          try { await new Telegram(this.env).call('sendMessage', payload); }
          catch (error) { return Response.json({ code: error.code, retryAfter: error.retryAfter }, { status: 502 }); }
          // Per-chat outbox order is stable, including when dispatch retries after a restart.
          await this.storage.put('lastAnswerRow', rowId);
        }
        return new Response(null, { status: 204 });
      });
    }
    if (await this.storage.getAlarm() === null) await this.storage.setAlarm(Date.now());
    return new Response(null, { status: 204 });
  }
  async alarm() {
    return this.ctx.blockConcurrencyWhile(() => this.refreshTyping());
  }
  async refreshTyping() {
    const env = this.env, chatId = Number(env.TELEGRAM_STAFF_CHAT_ID);
    // Leave headroom for alarm jitter and network latency within Telegram's five-second window.
    const due = Date.now() + 1500;
    if (env.BOT_ENABLED !== 'true' || env.AI_PILOT_ENABLED !== 'true' || (await quotaStatus(env)).blocked) return;
    // Completion queues the answer atomically. Keep typing through an outbox delivery retry.
    const waiting = await env.DB.prepare(`SELECT 1 FROM ai_jobs
      WHERE chat_id=? AND created_at>=? AND state IN ('pending','running')
      UNION ALL SELECT 1 FROM outbox WHERE chat_id=? AND status='pending' AND id>?
        AND json_type(payload,'$._erp_result')='integer' LIMIT 1`).bind(chatId, Date.now() / 1000 - 300, chatId,
          await this.storage.get('lastAnswerRow') ?? 0).first();
    if (!waiting) return;
    const retryAt = await this.storage.get('typingRetryAt');
    if (retryAt > Date.now()) {
      await this.storage.setAlarm(retryAt);
      return;
    }
    try {
      await new Telegram(env).send('sendChatAction', { chat_id: chatId, action: 'typing' });
    } catch (error) {
      if ([400, 401, 403, 404].includes(error.code)) return;
      if (error.retryAfter > 0) {
        const retryAt = Date.now() + error.retryAfter * 1000;
        await this.storage.put('typingRetryAt', retryAt);
        await this.storage.setAlarm(retryAt);
        return;
      }
    }
    await this.storage.setAlarm(Math.max(Date.now(), due));
  }
}
function store(env) {
  // Primary reads are required for optimistic concurrency; do not use read replicas here.
  const owner = Number(env.TELEGRAM_STAFF_CHAT_ID);
  return new Store(env.DB, content, owner, env.AI_PILOT_ENABLED === 'true' ? owner : null);
}
function startTypingTimer(ctx, api, chatId) {
  ctx.waitUntil(api.startTyping(chatId).catch(() => console.error('Typing timer unavailable')));
}
async function authorized(request, secret, header = 'X-Telegram-Bot-Api-Secret-Token') {
  if (!secret) return false;
  const supplied = request.headers.get(header) || '';
  // Fixed-length hashes avoid comparisons with token-dependent work.
  const hash = async value => new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value)));
  const [a, b] = await Promise.all([hash(supplied), hash(secret)]);
  let difference = 0; for (let i = 0; i < a.length; i++) difference |= a[i] ^ b[i];
  return difference === 0;
}
export default {
  async fetch(request, env, ctx) {
    const path = new URL(request.url).pathname;
    if (path === '/ai-bridge') {
      if (request.method !== 'POST') return new Response('Method not allowed', { status: 405 });
      if (!env.AI_BRIDGE_TOKEN || env.AI_BRIDGE_TOKEN.length < 32 || !await authorized(request, 'Bearer ' + env.AI_BRIDGE_TOKEN, 'Authorization'))
        return new Response('Unauthorized', { status: 401 });
      if (env.BOT_ENABLED !== 'true') return new Response('Unavailable', { status: 503 });
      try {
        const reader = request.body?.getReader(); if (!reader) throw new Error();
        const parts = []; let size = 0;
        while (true) {
          const { value, done } = await reader.read(); if (done) break;
          size += value.length; if (size > 20000) { await reader.cancel(); return new Response('Too large', { status: 413 }); }
          parts.push(value);
        }
        const bytes = new Uint8Array(size); let offset = 0;
        for (const part of parts) { bytes.set(part, offset); offset += part.length; }
        const bot = store(env);
        const payload = JSON.parse(new TextDecoder().decode(bytes));
        if (payload?.action === 'cloud_usage') return Response.json({ text: await cloudUsage(env) });
        if ((await quotaStatus(env)).blocked) return new Response('Unavailable', { status: 503 });
        const result = await new AIQueue(bot, env).bridge(payload);
        const api = new Telegram(env);
        if (result.job || result.queuedAnswer) startTypingTimer(ctx, api, bot.staff);
        if (payload.action === 'complete' || result.job) ctx.waitUntil(bot.dispatch(api).catch(() => console.error('AI reply retained in outbox')));
        return Response.json(result);
      } catch { return new Response('Bridge unavailable', { status: 503 }); }
    }
    if (path === '/health' && request.method === 'GET') {
      if ((await quotaStatus(env)).blocked) return Response.json({ service: 'mtech-telegram-bot', ready: false, maintenance: true }, { status: 503 });
      try {
        const identity = await env.DB.prepare("SELECT value FROM meta WHERE key='bot_id'").first();
        const configured = !!identity && !!env.TELEGRAM_BOT_TOKEN && !!env.WEBHOOK_SECRET && !!env.TELEGRAM_STAFF_CHAT_ID;
        return Response.json({ service: 'mtech-telegram-bot', ready: configured, enabled: env.BOT_ENABLED === 'true' }, { status: configured ? 200 : 503 });
      } catch { return Response.json({ ready: false }, { status: 503 }); }
    }
    if (path !== '/webhook') return new Response('Not found', { status: 404 });
    if (request.method !== 'POST') return new Response('Method not allowed', { status: 405 });
    if (!await authorized(request, env.WEBHOOK_SECRET)) return new Response('Unauthorized', { status: 401 });
    if (env.BOT_ENABLED !== 'true') return new Response('Not enabled', { status: 503 });
    if (Number(request.headers.get('Content-Length') || 0) > 65536) return new Response('Too large', { status: 413 });
    // Bound streamed bodies too, even when Content-Length is absent.
    const reader = request.body?.getReader(); if (!reader) return new Response('Bad request', { status: 400 });
    const parts = []; let size = 0;
    while (true) {
      const { value, done } = await reader.read(); if (done) break;
      size += value.length; if (size > 65536) { await reader.cancel(); return new Response('Too large', { status: 413 }); }
      parts.push(value);
    }
    let update;
    try {
      const bytes = new Uint8Array(size); let offset = 0;
      for (const part of parts) { bytes.set(part, offset); offset += part.length; }
      update = JSON.parse(new TextDecoder().decode(bytes));
      if (!Number.isSafeInteger(update?.update_id) || update.update_id < 0) throw new Error();
    } catch { return new Response('Bad request', { status: 400 }); }
    const { blocked } = await quotaStatus(env);
    const msg = update.message, owner = Number(env.TELEGRAM_STAFF_CHAT_ID);
    if (env.AI_PILOT_ENABLED === 'true' && Number.isSafeInteger(owner) && owner > 0 &&
        msg?.chat?.type === 'private' && msg.chat.id === owner && msg.from?.id === owner && !msg.from.is_bot &&
        /^\/usage(?:@mtech_customer_support_bot)?$/i.test(msg.text?.trim())) {
      if (!blocked) {
        const now = Date.now() / 1000;
        try {
          const beat = await env.DB.prepare("SELECT value FROM meta WHERE key='ai_bridge'").first();
          const snapshot = JSON.parse(beat?.value || '{}');
          const fresh = snapshot.ready && now >= snapshot.at && now - snapshot.at <= 45;
          // A long-running question can occupy the relay between heartbeats.
          const working = !fresh && await env.DB.prepare(
            "SELECT 1 FROM ai_jobs WHERE chat_id=? AND state='running' AND created_at>? LIMIT 1"
          ).bind(owner, now - 240).first();
          if (fresh || working) {
            // Repeated /usage while waiting shares the pending report.
            await env.DB.prepare(`INSERT OR IGNORE INTO ai_jobs(id,chat_id,prompt,state,created_at)
              SELECT ?,?,'/usage','pending',? WHERE NOT EXISTS (
                SELECT 1 FROM ai_jobs WHERE chat_id=? AND prompt='/usage' AND state IN ('pending','running'))`)
              .bind(update.update_id, owner, now, owner).run();
            return new Response('OK');
          }
        } catch { /* Fall back to the available cloud section. */ }
      }
      const cloud = await cloudUsage(env);
      return Response.json({ method: 'sendMessage', chat_id: owner,
        text: ['Ліміт запитань і бюджет ШІ · зв’язок з ERP недоступний.', cloud].join('\n\n') });
    }
    if (blocked) {
      // Telegram executes a method returned in the webhook response. Acknowledge
      // rejected input with 200 so it is not replayed as a new lead after recovery.
      // No D1 access, outbox writes or additional Telegram fetch are needed here.
      const callback = update.callback_query;
      if (callback?.id) return Response.json({ method: 'answerCallbackQuery', callback_query_id: callback.id,
        text: maintenanceText, show_alert: true });
      const message = update.message;
      if (message?.chat?.type === 'private' && message.from?.id === message.chat.id && !message.from?.is_bot)
        return Response.json({ method: 'sendMessage', chat_id: message.chat.id, text: maintenanceText,
          reply_markup: { remove_keyboard: true } });
      return new Response('OK');
    }
    try {
      const bot = store(env);
      const api = new Telegram(env);
      // Start even if an older outgoing message is waiting for a delivery retry.
      const showTyping = chatId => startTypingTimer(ctx, api, chatId);
      if (!await new AIQueue(bot, env).process(update, showTyping)) await bot.process(update);
      ctx.waitUntil(bot.dispatch(api).catch(() => console.error('Outbound dispatch failed; queued items retained')));
      return new Response('OK');
    } catch {
      console.error('Webhook transaction failed; Telegram may retry');
      return new Response('Retry later', { status: 503 });
    }
  },
  async scheduled(event, env, ctx) {
    if (env.BOT_ENABLED !== 'true') return;
    if ((await quotaStatus(env)).blocked) return;
    const bot = store(env);
    // Cron retries durable pending notifications even when no customer is chatting.
    ctx.waitUntil((async () => {
      await bot.dispatch(new Telegram(env));
      if (env.AI_PILOT_ENABLED === 'true') await new AIQueue(bot, env).expire();
      await bot.maintenance();
    })().catch(() => console.error('Scheduled processing failed; queued items retained')));
  }
};
