import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { DatabaseSync } from 'node:sqlite';
import { Engine, RequestLimit, menuCommands } from '../engine.mjs';
import { Store } from '../store.mjs';
import worker, { ReplyTyping, Telegram } from '../worker.mjs';
import { AIQueue, erpKeyboard, inputKeyboard } from '../ai.mjs';
import content from '../../telegram_bot/content.json' with { type: 'json' };

class DB {
  constructor() { this.db = new DatabaseSync(':memory:'); this.db.exec(readFileSync(new URL('../schema.sql', import.meta.url), 'utf8')); }
  prepare(query) {
    const db = this.db;
    const wrap = values => ({
      bind: (...v) => wrap(v),
      first: async () => db.prepare(query).get(...values) || null,
      all: async () => ({ results: db.prepare(query).all(...values) }),
      run: async () => ({ meta: db.prepare(query).run(...values) }),
      execute: () => ({
        results: db.prepare(query).all(...values),
        meta: { changes: db.prepare('SELECT changes() AS n').get().n }
      })
    });
    return wrap([]);
  }
  async batch(statements) {
    this.db.exec('BEGIN');
    try {
      const result = statements.map(statement => statement.execute());
      this.db.exec('COMMIT'); return result;
    } catch (error) { this.db.exec('ROLLBACK'); throw error; }
  }
}
const fresh = () => new Store(new DB(), content, -456);
function aiFixture() {
  const bot = fresh();
  bot.db.db.exec(readFileSync(new URL('../migrations/0003_ai_pilot.sql', import.meta.url), 'utf8'));
  const ai = new AIQueue(bot, { TELEGRAM_STAFF_CHAT_ID: '123', AI_PILOT_ENABLED: 'true' }, () => 10000);
  return { bot, ai };
}
test('AI pilot accepts only owner private commands; unavailable relay or disabled pilot never queues a prompt', async () => {
  const { bot, ai } = aiFixture();
  await ai.process(message(123, '/ai test'));
  assert.equal((await bot.sql('SELECT COUNT(*) AS n FROM ai_jobs').first()).n, 0);
  await ai.bridge({ action: 'claim', ready: true });
  assert.equal(await ai.process(message(124, '/ai secret')), false);
  for (const event of [message(123, '/ai secret', { chat: { id: 123, type: 'group' } }),
    message(123, '/ai secret', { from: { id: 124 } }), message(123, '/ai secret', { from: { id: 123, is_bot: true } })]) {
    assert.equal(await ai.process(event), true);
  }
  assert.equal((await bot.sql('SELECT COUNT(*) AS n FROM ai_jobs').first()).n, 0);
  ai.env.AI_PILOT_ENABLED = 'false';
  await ai.process(message(123, '/ai disabled'));
  assert.equal((await bot.sql('SELECT COUNT(*) AS n FROM ai_jobs').first()).n, 0);
  assert.equal(await ai.process(message(123, '/menu')), false);
});
test('AI jobs are deduplicated, claimed once and complete into durable outbox exactly once', async () => {
  const { bot, ai } = aiFixture();
  await ai.bridge({ action: 'claim', ready: true });
  const event = message(123, '/ai Привіт');
  await ai.process(event); await ai.process(event);
  await ai.process(message(123, '/ai concurrent'));
  assert.equal((await bot.sql('SELECT COUNT(*) AS n FROM ai_jobs').first()).n, 1);
  const { job } = await ai.bridge({ action: 'claim', ready: true });
  assert.equal(job.prompt, 'Привіт');
  assert.equal((await ai.bridge({ action: 'claim', ready: true })).job, null);
  const complete = { action: 'complete', id: job.id, claim_id: job.claim_id, text: 'Вітаю!' };
  await ai.bridge({ ...complete, claim_id: 'wrong' });
  assert.equal((await bot.sql('SELECT state FROM ai_jobs WHERE id=?', job.id).first()).state, 'running');
  assert.equal((await ai.bridge(complete)).queuedAnswer, true);
  assert.equal((await ai.bridge(complete)).queuedAnswer, false);
  const row = await bot.sql('SELECT * FROM ai_jobs WHERE id=?', job.id).first();
  assert.equal(row.state, 'complete'); assert.equal(row.prompt, '');
  const replies = (await bot.sql("SELECT payload FROM outbox WHERE method='sendMessage'").all()).results;
  assert.equal(replies.filter(r => JSON.parse(r.payload).text === 'Вітаю!').length, 1);
});
test('AI stale heartbeat and oversized questions fail closed; abandoned jobs expire without replay', async () => {
  const { bot, ai } = aiFixture();
  await ai.bridge({ action: 'claim', ready: true });
  ai.clock = () => 10046;
  await ai.process(message(123, '/ai stale'));
  await ai.bridge({ action: 'claim', ready: true });
  await ai.process(message(123, '/ai ' + 'a'.repeat(2001)));
  assert.equal((await bot.sql('SELECT COUNT(*) AS n FROM ai_jobs').first()).n, 0);
  await ai.process(message(123, '/ai valid'));
  const { job } = await ai.bridge({ action: 'claim', ready: true });
  ai.clock = () => 10347;
  await ai.expire(); await ai.expire();
  assert.equal((await bot.sql('SELECT state FROM ai_jobs WHERE id=?', job.id).first()).state, 'expired');
  const before = (await bot.sql('SELECT COUNT(*) AS n FROM outbox').first()).n;
  await ai.bridge({ action: 'complete', id: job.id, claim_id: job.claim_id, text: 'late' });
  assert.equal((await bot.sql('SELECT COUNT(*) AS n FROM outbox').first()).n, before);
});
test('AI bridge HTTP endpoint requires its own bearer secret', async () => {
  const { bot } = aiFixture();
  const env = { DB: bot.db, AI_BRIDGE_TOKEN: 'x'.repeat(40), BOT_ENABLED: 'true', QUOTA_GUARD_ENABLED: 'false' };
  const response = await worker.fetch(new Request('https://bot/ai-bridge', { method: 'POST', body: '{}' }), env, { waitUntil() {} });
  assert.equal(response.status, 401);
});

test('/usage requests a fresh, coalesced owner report and respects maintenance and access', async () => {
  const { bot } = aiFixture();
  const env = { DB: bot.db, TELEGRAM_STAFF_CHAT_ID: '123', AI_PILOT_ENABLED: 'true',
    BOT_ENABLED: 'true', WEBHOOK_SECRET: 'secret', QUOTA_GUARD_ENABLED: 'false',
    QUOTA_GUARD: { fetch: async () => Response.json({ text: 'Cloudflare snapshot' }) } };
  const run = async event => worker.fetch(new Request('https://bot/webhook', { method: 'POST',
    headers: { 'X-Telegram-Bot-Api-Secret-Token': 'secret' }, body: JSON.stringify(event) }), env, { waitUntil() {} });
  await new AIQueue(bot, env).bridge({ action: 'claim', ready: true, usage: 'private API figures' });
  await run(message(123, '/usage@mtech_customer_support_bot'));
  await run(message(123, '/usage'));
  const queue = new AIQueue(bot, env);
  await queue.process(message(123, '/ai stock'));
  const { job } = await queue.bridge({action:'claim', ready:true});
  assert.equal(job.prompt, '/usage');
  assert.equal((await bot.sql("SELECT COUNT(*) AS n FROM ai_jobs WHERE prompt='/usage'").first()).n, 1);
  env.MAINTENANCE_MODE = 'true';
  env.DB = null;
  assert.match((await (await run(message(123, '/usage'))).json()).text, /Cloudflare snapshot/);
  for (const event of [message(999, '/usage'), message(123, '/usage', {from:{id:999}}),
    message(123, '/usage', {chat:{id:123,type:'group'}})]) {
    assert.doesNotMatch(await (await run(event)).text(), /Cloudflare snapshot|private API figures/);
  }
});
test('the cloud only serializes pending work and leaves the shared allowance to ERP', async () => {
  const { bot, ai } = aiFixture();
  await ai.bridge({action:'claim', ready:true});
  for (let i = 0; i < 101; i++) {
    await ai.process(message(123, '/ai question'));
    const { job } = await ai.bridge({ action: 'claim', ready: true });
    assert.ok(job);
    await ai.bridge({ action:'complete', id:job.id, claim_id:job.claim_id, text:'answer' });
  }
  assert.equal((await bot.sql('SELECT COUNT(*) AS n FROM ai_jobs').first()).n, 101);
});
let updateId = 0;
const erpClick = (action, chatId = 123, extra = {}) => ({ update_id: ++updateId,
  callback_query: { id: 'erp-cb-' + updateId, from: { id: chatId },
    message: { chat: { id: chatId, type: 'private' } }, data: 'erp:' + action, ...extra } });
test('clarification choices use the pending item once and survive relay completion retries', async () => {
  const item = 'НАВЧАННЯ: шпиндельний картридж 2,2 кВт';
  for (const choice of ['stock', 'specs']) {
    const { ai, bot } = aiFixture();
    await ai.bridge({action: 'claim', ready: true});
    await ai.process(message(123, '/ai ' + item));
    const { job } = await ai.bridge({action: 'claim', ready: true});
    const done = {action: 'complete', id: job.id, claim_id: job.claim_id, text: 'Перевірити залишок чи знайти характеристики?', clarify: true};
    await ai.bridge(done);
    await ai.bridge(done);
    const session = async () => JSON.parse((await bot.sql("SELECT value FROM meta WHERE key='erp_input:123'").first()).value);
    assert.equal((await session()).subject, item);
    ai.clock = () => 10011;
    await ai.process(choice === 'stock' ? erpClick('choose:stock:' + job.id) : message(123, 'Характеристики'));
    const next = await ai.bridge({action: 'claim', ready: true});
    assert.equal(next.job.prompt, choice === 'stock' ? 'залишки: ' + item : 'знання: ' + item + ' — характеристики');
    await ai.bridge(done);
    assert.equal((await session()).subject, undefined);
  }
});
test('ERP buttons separate direct stock, literal product search and explicit AI input', async () => {
  for (const [action, input, expected] of [
    ['stock', null, 'залишки'], ['search', 'знання: зміни правила', 'залишки: знання: зміни правила'],
    ['search', 'залишки', 'залишки'], ['search', 'насос', 'залишки: насос'],
    ['ai', 'залишки', 'auto: залишки'],
    ['ai', 'Які деталі вільні?', 'auto: Які деталі вільні?'], ['knowledge', 'Гарантія', 'знання: Гарантія']
  ]) {
    const { bot, ai } = aiFixture();
    await ai.bridge({ action: 'claim', ready: true });
    const click = erpClick(action);
    assert.equal(await ai.process(click), true);
    await ai.process(click);
    assert.equal((await bot.sql("SELECT COUNT(*) AS n FROM outbox WHERE method='answerCallbackQuery'").first()).n, 1);
    if (input) {
      assert.equal((await bot.sql('SELECT COUNT(*) AS n FROM ai_jobs').first()).n, 0);
      const event = message(123, input);
      // A Worker restart retains the pending input, without touching customer sessions.
      const restarted = new AIQueue(bot, ai.env, ai.clock);
      await restarted.process(event); await restarted.process(event);
    }
    const jobs = (await bot.sql('SELECT * FROM ai_jobs').all()).results;
    assert.equal(jobs.length, 1); assert.equal(jobs[0].prompt, expected);
    const { job } = await ai.bridge({ action: 'claim', ready: true });
    await ai.bridge({ action: 'complete', id: job.id, claim_id: job.claim_id, text: 'Answer' });
    ai.clock = () => 10011;
    assert.equal(await ai.process(message(123, 'SERVO-2')), true);
    const followUp = await ai.bridge({ action: 'claim', ready: true });
    const expectedFollowUp = { stock: 'SERVO-2', search: 'залишки: SERVO-2', ai: 'auto: SERVO-2', knowledge: 'знання: SERVO-2' };
    assert.equal(followUp.job.prompt, expectedFollowUp[action]);
  }
});
test('every shared quick input queues its exact question once for an active owner session', async () => {
  const entries = JSON.parse(readFileSync(new URL('../../../apps/warehouse_core/warehouse_core/assistant_content/quick_inputs.json', import.meta.url)));
  const { bot, ai } = aiFixture();
  let now = Date.now() / 1000;
  ai.clock = () => now;
  await ai.process(message(123, '/ai'));
  const latestMenu = async () => JSON.parse((await bot.sql("SELECT payload FROM outbox WHERE method='sendMessage' ORDER BY id DESC LIMIT 1").first()).payload).reply_markup.inline_keyboard.flat();
  const sections = (await latestMenu()).filter(button => button.callback_data.startsWith('erp:list:'));
  assert.deepEqual(sections.map(button => button.text), ['Залишки та база знань', 'Чернетка відповіді клієнту']);
  assert.equal(new Set(entries.map(item => item.id)).size, 40);
  assert.equal(new Set(entries.map(item => item.question)).size, 40);
  const transcript = [];
  const api = { call: async (method, payload) => {
    if (method === 'sendMessage') transcript.push(payload.text);
    if (method === 'deleteMessage') transcript.push(payload.message_id);
  } };
  await bot.dispatch(api);
  for (const section of sections) {
    const selected = entries.filter(item => (item.kind === 'draft') === section.callback_data.includes(':draft:'));
    assert.equal(selected.length, 20);
    await ai.process(erpClick(section.callback_data.slice(4)));
    const first = await latestMenu();
    await ai.process(erpClick(first.find(button => button.text === 'Наступні ›').callback_data.slice(4)));
    const second = await latestMenu();
    assert.equal(second.find(button => button.text === '‹ Попередні').callback_data, section.callback_data);
    const buttons = [...first, ...second].filter(button => button.callback_data.startsWith('erp:q:'));
    assert.deepEqual(buttons.map(button => button.text), selected.map(item => item.label));
    await bot.dispatch(api);
    for (const [i, entry] of selected.entries()) {
      await ai.bridge({ action: 'claim', ready: true });
      const select = erpClick(buttons[i].callback_data.slice(4));
      await ai.process(erpClick(buttons[i].callback_data.slice(4), 124));
      await ai.process(select); await ai.process(select);
      const delivered = [];
      await bot.dispatch({ call: async (method, payload) => {
        delivered.push([method, payload]);
        return { message_id: delivered.length };
      } });
      assert.deepEqual(delivered.map(([method]) => method), ['answerCallbackQuery', 'sendMessage', 'sendMessage', 'sendChatAction']);
      assert.equal(delivered[1][1].text, entry.question);
      assert.equal(delivered[1][1].reply_markup, undefined);
      assert.equal(delivered[2][1].text, '🤖 Готую відповідь…');
      const { job } = await ai.bridge({ action: 'claim', ready: true });
      assert.equal(job.id, select.update_id);
      const prefix = { draft: 'чернетка: ', knowledge: 'знання: ' }[entry.kind] || '';
      assert.equal(job.prompt, prefix + entry.question);
      await ai.bridge({ action: 'complete', id: job.id, claim_id: job.claim_id, text: 'Answer' });
      const before = transcript.length;
      await bot.dispatch(api);
      assert.deepEqual(transcript.slice(before), ['Answer', 3]); // Only the progress message is removed.
      now += 11;
    }
  }
  now += 601;
  await ai.process(message(123, entries[0].question));
  const expired = JSON.parse((await bot.sql("SELECT payload FROM outbox WHERE method='sendMessage' ORDER BY id DESC LIMIT 1").first()).payload);
  assert.match(expired.text, /Час очікування завершився/);
  await ai.process(message(123, '/ai'));
  await ai.process(erpClick('close'));
  assert.equal(await bot.sql("SELECT value FROM meta WHERE key='erp_input:123'").first(), null);
  assert.equal((await bot.sql('SELECT COUNT(*) AS n FROM ai_jobs').first()).n, entries.length);
});

test('unified assistant stays open across questions and exits without capturing customer input', async () => {
  const { bot, ai } = aiFixture(); let now = 10000; ai.clock = () => now;
  await ai.process(message(123, '/ai'));
  for (const question of ['Покажи залишки', 'Чи є сервопривід і яка гарантія?']) {
    await ai.bridge({ action: 'claim', ready: true });
    await ai.process(message(123, question));
    const { job } = await ai.bridge({ action: 'claim', ready: true });
    assert.equal(job.prompt, question);
    await ai.bridge({ action: 'complete', id: job.id, claim_id: job.claim_id, text: 'Answer' });
    const input = JSON.parse((await bot.sql("SELECT value FROM meta WHERE key='erp_input:123'").first()).value);
    assert.equal(input.mode, 'assistant');
    now += 11;
  }
  await ai.process(erpClick('close'));
  assert.equal(await ai.process(message(123, 'Customer form input')), false);
  await ai.process(erpClick('assistant'));
  assert.equal(await ai.process(message(123, '/menu')), false);
  assert.equal(await ai.process(message(123, 'Customer form input')), false);
  await ai.process(erpClick('assistant')); now += 601;
  await ai.process(message(123, 'Expired question'));
  assert.equal((await bot.sql('SELECT COUNT(*) AS n FROM ai_jobs').first()).n, 2);
});

test('accepted requests show progress once, then send one new answer and remove the placeholder', async () => {
  for (const [question, status] of [['залишки: насос', '🔎 Шукаю у Вектор ERP…'], ['Які деталі є?', '🤖 Готую відповідь…']]) {
    const { bot, ai } = aiFixture(); ai.clock = () => Date.now() / 1000;
    const calls = [], api = { call: async (method, payload) => { calls.push({ method, payload }); return { message_id: 777 }; } };
    await ai.bridge({ action: 'claim', ready: true });
    const event = message(123, '/ai ' + question);
    await ai.process(event); await ai.process(event);
    await bot.dispatch(api);
    assert.deepEqual(calls.map(c => c.method), ['sendMessage', 'sendChatAction']);
    assert.equal(calls[0].payload.text, status);
    assert.equal(calls[0].payload.disable_notification, true);
    const { job } = await ai.bridge({ action: 'claim', ready: true });
    await bot.dispatch(api);
    assert.equal(calls.at(-1).method, 'sendChatAction');
    const done = { action: 'complete', id: job.id, claim_id: job.claim_id, text: 'Answer' };
    await ai.bridge(done); await ai.bridge(done);
    await bot.dispatch(api);
    assert.deepEqual(calls.slice(-2).map(c => c.method), ['sendMessage', 'deleteMessage']);
    assert.equal(calls.at(-1).payload.message_id, 777);
    assert.equal(calls.at(-2).payload.text, 'Answer');
    assert.deepEqual(calls.at(-2).payload.reply_markup, inputKeyboard);
    assert.equal(calls.filter(c => c.payload.text === 'Answer').length, 1);
    assert.ok(calls.every(c => !('_erp_progress' in c.payload) && !('_erp_result' in c.payload)));
    assert.equal(await bot.sql('SELECT value FROM meta WHERE key=?', `erp_progress:${job.id}`).first(), null);
    ai.clock = () => Date.now() / 1000 + 11;
    await ai.process(message(123, 'SERVO-2'));
    assert.equal((await ai.bridge({ action: 'claim', ready: true })).job.prompt, 'SERVO-2');
  }
});
test('an answer delivered after /menu offers to reopen the assistant', async () => {
  const { bot, ai } = aiFixture();
  ai.clock = () => Date.now() / 1000;
  await ai.bridge({ action: 'claim', ready: true });
  await ai.process(message(123, '/ai test'));
  const { job } = await ai.bridge({ action: 'claim', ready: true });
  await ai.bridge({ action: 'complete', id: job.id, claim_id: job.claim_id, text: 'Answer' });
  assert.equal(await ai.process(message(123, '/menu')), false);
  const calls = [];
  await bot.dispatch({ call: async (method, payload) => calls.push(payload) });
  assert.equal(calls.at(-1).text, 'Answer');
  assert.deepEqual(calls.at(-1).reply_markup, erpKeyboard);
  assert.equal(await ai.process(message(123, 'Outside AI mode')), false);
});
test('typing refreshes across the queue, slow processing and delayed delivery, then stops', async t => {
  const { bot, ai } = aiFixture();
  t.mock.timers.enable({ apis: ['Date'], now: 10000000 });
  ai.clock = () => Date.now() / 1000;
  let next = null;
  const values = new Map();
  const storage = { getAlarm: async () => next, setAlarm: async time => { next = time; },
    get: async key => values.get(key), put: async (key, value) => values.set(key, value) };
  const env = { ...ai.env, BOT_ENABLED: 'true', DB: bot.db, TELEGRAM_BOT_TOKEN: 'offline' };
  let gate = Promise.resolve();
  const timer = new ReplyTyping({ storage, blockConcurrencyWhile: action => (gate = gate.then(action)) }, env);
  env.REPLY_TYPING = { getByName: name => {
    assert.equal(name, '123');
    return { fetch: (url, init) => timer.fetch(new Request(url, init)) };
  } };
  const api = new Telegram(env);
  const pulses = [];
  const deliveryStarted = Promise.withResolvers(), deliveryConfirmed = Promise.withResolvers();
  t.mock.method(globalThis, 'fetch', async (url, init) => {
    if (url.endsWith('/sendChatAction')) {
      assert.deepEqual(JSON.parse(init.body), { chat_id: 123, action: 'typing' });
      pulses.push(Date.now());
    }
    if (url.endsWith('/sendMessage') && JSON.parse(init.body).text === 'Answer') {
      deliveryStarted.resolve();
      await deliveryConfirmed.promise;
    }
    return Response.json({ ok: true, result: { message_id: 777 } });
  });
  async function tick(lateBy = 0) {
    assert.notEqual(next, null);
    t.mock.timers.tick(next - Date.now() + lateBy);
    next = null;
    await timer.alarm();
  }
  await ai.bridge({ action: 'claim', ready: true });
  await ai.process(message(123, '/ai'));
  const event = erpClick('q:lens');
  const starts = [];
  const start = chatId => starts.push(api.startTyping(chatId));
  await ai.process(event, start);
  await ai.process(event, start);
  await ai.process(message(123, '/ai concurrent'), start);
  assert.equal(starts.length, 1);
  await Promise.all(starts);
  assert.equal(next, Date.now());
  for (let i = 0; i < 6; i++) await tick();
  // Telegram clears typing when the selected question arrives. The next outbox
  // action must refresh it immediately and rearm the same timer.
  t.mock.timers.tick(700);
  await bot.dispatch(api);
  assert.equal(pulses.at(-1), Date.now());
  assert.equal(next, Date.now() + 1500);
  const afterQuestion = pulses.length;
  const { job } = await ai.bridge({ action: 'claim', ready: true });
  await api.startTyping(123); // Claiming the job must preserve the existing refresh cadence.
  for (let i = 0; i < 60; i++) await tick();
  await ai.bridge({ action: 'complete', id: job.id, claim_id: job.claim_id, text: 'Answer' });
  await tick();
  assert.equal(pulses.length, afterQuestion + 61);
  assert.ok(pulses.slice(afterQuestion).every((time, index) => time - pulses[afterQuestion + index - 1] === 1500));
  await tick(2300); // A late refresh still fits inside Telegram's typing window.
  assert.ok(pulses.at(-1) - pulses.at(-2) < 5000);
  const dispatch = bot.dispatch(api);
  await deliveryStarted.promise;
  const racingAlarm = tick();
  deliveryConfirmed.resolve();
  await Promise.all([racingAlarm, dispatch]);
  assert.equal(pulses.length, afterQuestion + 62);
  assert.equal(next, null);
});
test('finished jobs suppress delayed progress; feedback failures never block answers', async () => {
  for (const failFeedback of [false, true]) {
    const { bot, ai } = aiFixture(); ai.clock = () => Date.now() / 1000;
    const calls = [], api = { call: async (method, payload) => {
      calls.push({ method, payload });
      if (payload.disable_notification || method === 'sendChatAction') throw { code: 429, retryAfter: 60 };
      return { message_id: 777 };
    } };
    await ai.bridge({ action: 'claim', ready: true });
    await ai.process(message(123, '/ai question'));
    if (failFeedback) await bot.dispatch(api);
    const { job } = await ai.bridge({ action: 'claim', ready: true });
    await ai.bridge({ action: 'complete', id: job.id, claim_id: job.claim_id, text: 'Answer' });
    await bot.dispatch(api);
    assert.equal(calls.at(-1).payload.text, 'Answer');
    assert.equal(calls.at(-1).method, 'sendMessage');
    if (!failFeedback) assert.equal(calls.length, 1);
    assert.equal((await bot.sql("SELECT COUNT(*) AS n FROM outbox WHERE status='pending'").first()).n, 0);
  }
});
test('expired requests send one final message even if the owner already deleted the placeholder', async () => {
  for (const alreadyDeleted of [false, true]) {
    const { bot, ai } = aiFixture(); let now = Date.now() / 1000; ai.clock = () => now;
    const calls = [], api = { call: async (method, payload) => {
      calls.push({ method, payload });
      if (method === 'deleteMessage' && alreadyDeleted) throw { code: 400 };
      return { message_id: 777 };
    } };
    await ai.bridge({ action: 'claim', ready: true });
    await ai.process(message(123, '/ai question')); await bot.dispatch(api);
    now += 301; await ai.expire(); await ai.expire(); await bot.dispatch(api);
    assert.match(calls.at(-2).payload.text, /недоступне/);
    assert.deepEqual(calls.slice(-2).map(c => c.method), ['sendMessage', 'deleteMessage']);
    assert.equal((await bot.sql("SELECT COUNT(*) AS n FROM outbox WHERE status='pending'").first()).n, 0);
  }
});
test('ERP input expires or cancels without consuming customer drafts or invoking AI', async () => {
  const { bot, ai } = aiFixture();
  const draft = JSON.stringify({ draft: { key: 'preserve-this', flow: 'repair' } });
  await bot.sql('INSERT INTO sessions VALUES(?,?,?)',123,draft,10000).run();
  await ai.process(erpClick('ai'));
  ai.clock = () => 10601;
  await ai.process(message(123, 'Too late'));
  assert.equal((await bot.sql('SELECT COUNT(*) AS n FROM ai_jobs').first()).n, 0);
  await ai.process(erpClick('search'));
  assert.equal(await ai.process(message(123, '/menu')), false);
  assert.equal(await bot.sql("SELECT value FROM meta WHERE key='erp_input:123'").first(), null);
  assert.equal((await bot.sql('SELECT state FROM sessions WHERE chat_id=123').first()).state, draft);
  await ai.process(erpClick('ai'));
  await ai.process(erpClick('close'));
  assert.equal(await ai.process(message(123, 'Customer form input')), false);
  assert.equal((await bot.sql('SELECT COUNT(*) AS n FROM ai_jobs').first()).n, 0);
});
test('ERP buttons and awaiting-input state are private to the verified owner', async () => {
  const { bot, ai } = aiFixture();
  await ai.process(erpClick('ai'));
  const before = (await bot.sql("SELECT value FROM meta WHERE key='erp_input:123'").first()).value;
  for (const update of [erpClick('stock', 999), erpClick('ai', 123, { from: { id: 999 } }),
    erpClick('stock',123,{ message: { chat: { id:123,type:'group' } } })]) assert.equal(await ai.process(update),true);
  assert.equal(await ai.process(message(999,'My product')),false);
  assert.equal((await bot.sql("SELECT value FROM meta WHERE key='erp_input:123'").first()).value,before);
  assert.equal((await bot.sql('SELECT COUNT(*) AS n FROM ai_jobs').first()).n,0);
});
test('help explains the current menus and shows AI guidance only to the enabled owner without queuing AI work', async () => {
  const { bot, ai } = aiFixture(); bot.aiOwner = 123;
  const helpText = async chatId => JSON.parse((await bot.sql(
    "SELECT payload FROM outbox WHERE method='sendMessage' AND chat_id=? ORDER BY id DESC LIMIT 1", chatId
  ).first()).payload).text;
  await start(bot, 'parts');
  const draft = (await state(bot)).draft;
  await ai.process(message(123, '/ai'));
  const help = message(123, '/help');
  assert.equal(await ai.process(help), false);
  await bot.process(help);
  const ownerHelp = await helpText(123);
  assert.ok(ownerHelp.includes(content.texts.help_ai));
  assert.ok(ownerHelp.length <= 3500);
  assert.deepEqual((await state(bot)).draft, draft);
  assert.equal(await bot.sql("SELECT value FROM meta WHERE key='erp_input:123'").first(), null);
  assert.equal((await bot.sql('SELECT COUNT(*) AS n FROM ai_jobs').first()).n, 0);
  await bot.process(message(999, '/help'));
  assert.equal(await helpText(999), content.texts.help);
  bot.aiOwner = null;
  await bot.process(message(123, '/help'));
  assert.equal(await helpText(123), content.texts.help);
  bot.staff = 123;
  await bot.process(message(123, '/help'));
  assert.equal(await helpText(123), content.texts.help_staff);
});

test('owner AI entry remains in commands but is absent from the main menu', async () => {
  const { bot, ai } = aiFixture(); bot.aiOwner = 123;
  await bot.process(message(123,'/menu'));
  const ownerState = await state(bot,123);
  assert.equal(ownerState.buttons['erp:home'],undefined);
  assert.deepEqual(Object.keys(ownerState.buttons), [
    'menu:equipment', 'menu:parts', 'menu:support', 'menu:delivery',
    'info:contacts', 'menu:news', 'menu:status'
  ]);
  const home = erpClick('home',123,{data:`${ownerState.revision}|erp:home`});
  assert.equal(await ai.process(home),true);
  assert.equal(await bot.sql("SELECT value FROM meta WHERE key='erp_input:123'").first(),null);
  const ownerMenus=(await bot.sql("SELECT payload FROM outbox WHERE method='setMyCommands' AND chat_id=123").all()).results;
  assert.equal(ownerMenus.length, 2);
  for (const row of ownerMenus) {
    const { commands, language_code } = JSON.parse(row.payload);
    assert.deepEqual(commands.map(c => c.command), ['menu','ai','usage','help']);
    assert.equal(commands[1].description, language_code === 'uk' ? 'Векторія ✦ ШІ-помічник' : 'Vectoria ✦ AI-assistant');
  }
  assert.equal(erpKeyboard.inline_keyboard[0][0].text, 'Векторія ✦ ШІ-помічник');
  await bot.process(message(999,'/menu'));
  assert.equal((await state(bot,999)).buttons['erp:home'],undefined);
  const customerMenus=(await bot.sql("SELECT payload FROM outbox WHERE method='setMyCommands' AND chat_id=999").all()).results;
  for (const row of customerMenus) assert.deepEqual(JSON.parse(row.payload).commands,menuCommands());
  await ai.process(message(123,'/ai'));
  assert.equal(JSON.parse((await bot.sql("SELECT value FROM meta WHERE key='erp_input:123'").first()).value).mode,'assistant');
  await ai.process(message(123,'/erp'));
  assert.equal(JSON.parse((await bot.sql("SELECT value FROM meta WHERE key='erp_input:123'").first()).value).mode,'assistant');
  await bot.process(message(123, '', { contact: { user_id: 123, phone_number: '380630000000', first_name: 'Олена' } }));
  const ownerWithProfile = await state(bot,123);
  assert.deepEqual(Object.keys(ownerWithProfile.buttons), [
    'menu:equipment', 'menu:parts', 'menu:support', 'menu:delivery',
    'info:contacts', 'menu:news', 'menu:status', 'profile:open'
  ]);
});
const message = (chatId, text, extra = {}) => ({ update_id: ++updateId,
  message: { chat: { id: chatId, type: 'private' }, from: { id: chatId }, text, ...extra } });
const state = async (store, chatId = 123) => JSON.parse((await store.sql('SELECT state FROM sessions WHERE chat_id=?', chatId).first())?.state || '{}');
async function click(store, action, chatId = 123) {
  const s = await state(store, chatId); assert.ok(Object.hasOwn(s.buttons, action), action);
  const event = { update_id: ++updateId, callback_query: { id: 'cb' + updateId, from: { id: chatId },
    message: { chat: { id: chatId, type: 'private' } }, data: `${s.revision}|${action}` } };
  await store.process(event); return event;
}
async function start(store, flow = 'callback', chatId = 123) {
  await store.process(message(chatId, '/start'));
  const paths = { equipment: ['menu:equipment', 'category:metal-lasers'], parts: ['menu:parts'], selection: ['menu:equipment'],
    repair: ['menu:support'], training: ['menu:support'], warranty: ['menu:support', 'info:warranty'],
    returns: ['menu:support', 'info:returns'], order: ['menu:support'], delivery: ['menu:delivery'],
    callback: ['info:contacts'], written: ['info:contacts'], general: [], status_request: ['menu:status'] };
  for (const action of paths[flow]) await click(store, action, chatId);
  if (flow === 'general') {
    await store.process(message(chatId, 'Маю запитання'));
    const legacy = await state(store, chatId);
    legacy.buttons['flow:general'] = content.labels.general;
    await store.sql('UPDATE sessions SET state=? WHERE chat_id=?', JSON.stringify(legacy), chatId).run();
  }
  await click(store, `flow:${flow}`, chatId);
}
async function complete(store, chatId = 123) {
  while ((await state(store, chatId)).screen === 'field') {
    const s = await state(store, chatId), key = [...content.flows[s.draft.flow].fields, ...content.contact_fields][s.draft.step];
    const field = content.fields[key];
    if (field.optional) await click(store, 'skip', chatId);
    else if (field.unknown_prompt) await store.process(message(chatId, 'Деталь до верстата'));
    else if (field.options) await click(store, 'option:0', chatId);
    else await store.process(message(chatId, key === 'phone' ? '+380630000000' : key === 'time' ? '09:30' : 'Тестове значення'));
  }
}

test('all Python engine behavior is preserved, including existing saved drafts and callbacks', () => {
  const run = spawnSync('python', [fileURLToPath(new URL('./python_cases.py', import.meta.url))], { encoding: 'utf8', maxBuffer: 20 * 1024 * 1024 });
  assert.equal(run.status, 0, run.stderr);
  const cases = JSON.parse(run.stdout); assert.ok(cases.length > 200);
  for (const [index, c] of cases.entries()) {
    const s = structuredClone(c.state);
    const e = new Engine(content, s, c.chatId, (draft, summary) => {
      if (c.limited) throw new RequestLimit();
      assert.deepEqual(draft, c.submission.draft); assert.equal(summary, c.submission.summary); return c.submission.number;
    }, structuredClone(c.customer));
    const actions = e.handle(c.message, c.callback);
    // Only newly generated random IDs may differ.
    const revision = s.revision;
    if (revision !== c.state.revision) {
      for (const [, p] of actions) for (const row of p.reply_markup?.inline_keyboard || []) for (const b of row)
        if (b.callback_data) b.callback_data = b.callback_data.replace(revision + '|', c.expectedState.revision + '|');
      s.revision = c.expectedState.revision;
    }
    if (s.draft && s.draft.key !== c.state.draft?.key) s.draft.key = c.expectedState.draft.key;
    assert.deepEqual(s, c.expectedState, `state case ${index}`);
    assert.deepEqual(actions, c.expectedActions, `actions case ${index}`);
  }
});

test('every flow creates one lead, staff notification, confirmation and current command menu', async () => {
  const store = fresh();
  let chatId = 1000;
  for (const flow of Object.keys(content.flows)) {
    chatId++; await start(store, flow, chatId); await complete(store, chatId);
    const event = await click(store, 'submit', chatId);
    assert.equal(await store.process(event), false);
    const record = await store.sql('SELECT * FROM requests WHERE chat_id=?', chatId).first();
    assert.equal(record.flow, flow); assert.equal(record.status, 'new');
    if (flow === 'written') assert.deepEqual(Object.keys(JSON.parse(record.payload).answers), ['message']);
    else assert.equal(JSON.parse(record.payload).answers.phone, '+380630000000');
    assert.equal((await state(store, chatId)).draft, undefined);
    const menus = await store.sql("SELECT payload FROM outbox WHERE method='setMyCommands' AND chat_id=?", chatId).all();
    assert.equal(menus.results.length, 2);
    for (const row of menus.results) assert.deepEqual(JSON.parse(row.payload).commands, menuCommands());
    assert.ok(await store.sql('SELECT id FROM outbox WHERE request_id=? AND chat_id=-456', record.id).first());
  }
});

test('private customer AI attempt offers handoff without AI; only authorized staff can queue a written reply', async () => {
  const { bot, ai } = aiFixture();
  const question = message(789, '/ai Яка гарантія?', { date: Date.parse('2026-11-06T15:00:00Z') / 1000 });
  assert.equal(await ai.process(question), false);
  await bot.process(question);
  assert.equal((await bot.sql('SELECT count(*) AS n FROM ai_jobs').first()).n, 0);
  await click(bot, 'flow:written', 789);
  const draft = (await state(bot, 789)).draft;
  assert.deepEqual(draft.answers, { message: 'Яка гарантія?' });
  assert.equal((await bot.sql('SELECT count(*) AS n FROM requests').first()).n, 0);
  await click(bot, 'submit', 789);
  const group = { chat: { type: 'supergroup', id: bot.staff } };
  await bot.process(message(777, '/reply 1 forged'));
  await bot.process(message(777, '/reply 1 forged', { ...group, from: { id: 777, is_bot: true } }));
  await bot.process(message(777, '/reply 999 missing', group));
  const event = message(777, '/reply 1 Текст менеджера', group);
  await Promise.all([bot.process(event), bot.process(event)]);
  const customerReplies = await bot.sql("SELECT payload FROM outbox WHERE request_id=1 AND chat_id=789").all();
  assert.deepEqual(customerReplies.results.map(row => JSON.parse(row.payload).text), ['Менеджер MTech · заявка №1\n\nТекст менеджера']);
});

test('concurrent duplicate updates and simultaneous submissions do not lose data', async () => {
  const store = fresh(), event = message(123, '/start');
  await Promise.all([store.process(event), store.process(event)]);
  assert.equal((await store.sql('SELECT COUNT(*) AS n FROM updates').first()).n, 1);
  for (const id of [123, 456]) { await start(store, 'callback', id); await complete(store, id); }
  await Promise.all([click(store, 'submit', 123), click(store, 'submit', 456)]);
  assert.equal((await store.sql('SELECT COUNT(*) AS n FROM requests').first()).n, 2);
  assert.equal((await state(store, 123)).draft, undefined); assert.equal((await state(store, 456)).draft, undefined);
});

test('failed transaction rolls back state, update deduplication, lead and outbox', async () => {
  const store = fresh(); await start(store); await complete(store);
  const before = await state(store), count = (await store.sql('SELECT COUNT(*) AS n FROM outbox').first()).n;
  store.db.db.exec("CREATE TRIGGER fail_outbox BEFORE INSERT ON outbox BEGIN SELECT RAISE(ABORT,'test write failure'); END;");
  await assert.rejects(click(store, 'submit'));
  assert.deepEqual(await state(store), before);
  assert.equal((await store.sql('SELECT COUNT(*) AS n FROM requests').first()).n, 0);
  assert.equal((await store.sql('SELECT COUNT(*) AS n FROM outbox').first()).n, count);
  assert.equal(await store.sql('SELECT id FROM updates WHERE id=?', updateId).first(), null);
});

test('retry queue respects rate limits, serializes dispatch and retains failed destinations', async () => {
  const store = fresh(); await store.db.batch([store.enqueue('sendMessage', { chat_id: 123, text: 'test' })]);
  let calls = 0;
  await Promise.all([1, 2].map(() => store.dispatch({ call: async () => { calls++; throw { code: 429, retryAfter: 60 }; } })));
  assert.equal(calls, 1);
  let row = await store.sql('SELECT * FROM outbox').first();
  assert.equal(row.status, 'pending'); assert.ok(row.available_at > Date.now() / 1000 + 55);
  await store.sql('UPDATE outbox SET available_at=0').run();
  await store.dispatch({ call: async () => { throw { code: 403 }; } });
  row = await store.sql('SELECT * FROM outbox').first(); assert.equal(row.status, 'failed');
  assert.ok(row.payload.includes('test'));
});

test('expired drafts clear menus while completed requests remain', async () => {
  const store = fresh(); await start(store); await complete(store); await click(store, 'submit');
  await store.sql('DELETE FROM updates').run(); // Isolate expiry behavior from the per-user throttle.
  const saved = await state(store); delete saved.throttle;
  await store.sql('UPDATE sessions SET state=?', JSON.stringify(saved)).run();
  await start(store);
  await store.sql('UPDATE sessions SET updated_at=0').run(); await store.maintenance();
  assert.deepEqual(await state(store), {});
  assert.equal((await store.sql('SELECT COUNT(*) AS n FROM requests').first()).n, 1);
  const menus = await store.sql("SELECT payload FROM outbox WHERE method='setMyCommands'").all();
  for (const row of menus.results) assert.deepEqual(JSON.parse(row.payload).commands, menuCommands());
});

test('status lists only the current customer, uses fresh statuses, and limits history to ten', async () => {
  const store = fresh();
  for (let i = 1; i <= 12; i++) await store.sql(`INSERT INTO requests
    (draft_key,chat_id,flow,payload,summary,status,created_at,created_ts) VALUES(?,?,?,?,?,?,?,?)`,
    `draft-${i}`, i === 12 ? 456 : 123, 'repair', '{}', 'Private customer details', 'new', '2026-10-31T00:00:00+00:00', i).run();
  const lastText = async () => JSON.parse((await store.sql("SELECT payload FROM outbox WHERE method='sendMessage' ORDER BY id DESC LIMIT 1").first()).payload).text;
  await store.process(message(123, '/status'));
  let text = await lastText();
  assert.ok(text.includes('№11')); assert.ok(!text.includes('№12')); assert.ok(!text.includes('№1 ·'));
  assert.ok(!text.includes('Private customer details'));
  await store.sql("UPDATE requests SET status='closed' WHERE id=11").run();
  await click(store, 'menu:status'); assert.ok((await lastText()).includes('Закрито'));
  await store.process(message(789, '/status'));
  assert.ok((await lastText()).includes(content.texts.status_empty));
});

test('subscriptions require explicit consent, survive expiry and restart, and reject old buttons', async () => {
  let store = fresh();
  await store.process(message(123, '/start')); await click(store, 'menu:news');
  assert.equal(await store.sql('SELECT * FROM subscriptions').first(), null);
  const event = await click(store, 'news:on');
  assert.equal(await store.process(event), false);
  let row = await store.sql('SELECT * FROM subscriptions WHERE chat_id=123').first();
  assert.equal(row.subscribed, 1); assert.equal(row.consent_version, 'mtech-news-v1'); assert.ok(row.updated_at > 0);
  await store.sql('UPDATE sessions SET updated_at=0').run(); await store.maintenance();
  store = new Store(store.db, content, -456);
  await store.process(message(123, '/news')); assert.ok((await state(store)).buttons['news:off']);
  await click(store, 'news:off');
  await store.process({ ...event, update_id: ++updateId });
  assert.equal((await store.sql('SELECT subscribed FROM subscriptions WHERE chat_id=123').first()).subscribed, 0);
  await store.process(message(456, '/news')); assert.ok((await state(store, 456)).buttons['news:on']);
  assert.equal((await store.sql('SELECT COUNT(*) AS n FROM subscriptions').first()).n, 1);
});

test('commands stay visible while news content follows subscription through drafts, expiry and opt out', async () => {
  const store = fresh(), title = content.news_items[0].title;
  const lastText = async () => JSON.parse((await store.sql("SELECT payload FROM outbox WHERE method='sendMessage' ORDER BY id DESC LIMIT 1").first()).payload).text;
  const check = async (subscribed, draft = false) => {
    const menus = await store.sql("SELECT payload FROM outbox WHERE method='setMyCommands' AND chat_id=123 AND status='pending'").all();
    assert.equal(menus.results.length, 2);
    for (const row of menus.results) assert.deepEqual(JSON.parse(row.payload).commands, menuCommands(draft, subscribed));
  };
  assert.deepEqual(menuCommands().map(c => c.command), ['menu', 'help']);
  assert.deepEqual(menuCommands(false, false), menuCommands(false, true));
  await store.process(message(123, '/news'));
  assert.ok(!(await lastText()).includes(title)); await check(false);
  await click(store, 'news:on');
  assert.ok((await lastText()).includes(title)); await check(true);
  await start(store); await check(true, true);
  await store.sql('UPDATE sessions SET updated_at=0').run(); await store.maintenance(); await check(true);
  await store.process(message(123, '/news'));
  await store.sql("UPDATE outbox SET status='failed' WHERE method='setMyCommands'").run();
  await click(store, 'news:off');
  assert.ok(!(await lastText()).includes(title)); await check(false);
  assert.equal((await store.sql("SELECT COUNT(*) AS n FROM outbox WHERE method='setMyCommands' AND status='failed'").first()).n, 0);
  await store.process(message(456, '/news'));
  assert.ok(!(await lastText()).includes(title));
});

test('subscription writes and confirmations commit together and forged callbacks cannot opt in', async () => {
  const store = fresh(); await store.process(message(123, '/news'));
  const before = await state(store);
  const forged = { update_id: ++updateId, callback_query: { id: 'forged', from: { id: 456 },
    message: { chat: { id: 123, type: 'private' } }, data: `${before.revision}|news:on` } };
  await store.process(forged); assert.equal(await store.sql('SELECT * FROM subscriptions').first(), null);
  store.db.db.exec("CREATE TRIGGER fail_outbox BEFORE INSERT ON outbox BEGIN SELECT RAISE(ABORT,'test write failure'); END;");
  await assert.rejects(click(store, 'news:on'));
  assert.equal(await store.sql('SELECT * FROM subscriptions').first(), null);
  assert.deepEqual(await state(store), before);
  assert.equal(await store.sql('SELECT * FROM updates WHERE id=?', updateId).first(), null);
});

test('subscription migration is repeatable and preserves existing records', () => {
  const db = new DB().db;
  db.exec("DROP TABLE subscriptions; INSERT INTO requests(draft_key,chat_id,flow,payload,summary,created_at,created_ts) VALUES('existing',123,'order','{}','Existing request','2026-10-30',1)");
  const migration = readFileSync(new URL('../migrations/0001_subscriptions.sql', import.meta.url), 'utf8');
  db.exec(migration); db.exec(migration);
  assert.equal(db.prepare('SELECT summary FROM requests').get().summary, 'Existing request');
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM subscriptions').get().n, 0);
});

test('webhook rejects unauthenticated, malformed and oversized requests without database writes', async () => {
  const env = { DB: new DB(), WEBHOOK_SECRET: 'test-secret', BOT_ENABLED: 'true' }, ctx = { waitUntil() {} };
  const req = (body, secret) => new Request('https://example.test/webhook', { method: 'POST', body,
    headers: secret ? { 'X-Telegram-Bot-Api-Secret-Token': secret } : {} });
  assert.equal((await worker.fetch(req('{}'), env, ctx)).status, 401);
  assert.equal((await worker.fetch(req('{', 'test-secret'), env, ctx)).status, 400);
  assert.equal((await worker.fetch(req('a'.repeat(65537), 'test-secret'), env, ctx)).status, 413);
  assert.equal((await env.DB.prepare('SELECT COUNT(*) AS n FROM updates').first()).n, 0);
});

test('contact profiles survive session expiry and restart and update without rewriting old requests', async () => {
  let store = fresh(); await start(store); await complete(store); await click(store, 'submit');
  const original = (await store.sql('SELECT payload FROM requests WHERE id=1').first()).payload;
  const profile = JSON.parse((await store.sql('SELECT data FROM customer_profiles WHERE chat_id=123').first()).data);
  assert.equal(profile.phone, '+380630000000'); assert.equal(profile.time, '09:30');
  await store.sql('UPDATE sessions SET updated_at=0').run(); await store.maintenance();
  store = new Store(store.db, content, -456);
  await start(store); await store.process(message(123, 'Інша тема'));
  assert.equal((await state(store)).screen, 'review');
  await click(store, 'profile:open'); await click(store, 'profile:phone');
  await store.process(message(123, '', { contact: { user_id: 999, phone_number: '380999999999' } }));
  assert.equal((await state(store)).screen, 'profile_field');
  await store.process(message(123, '', { contact: { user_id: 123, phone_number: '380671111111' } }));
  await click(store, 'profile:done'); await click(store, 'submit');
  assert.equal((await store.sql('SELECT payload FROM requests WHERE id=1').first()).payload, original);
  assert.equal(JSON.parse((await store.sql('SELECT payload FROM requests WHERE id=2').first()).payload).answers.phone, '+380671111111');
  await start(store, 'callback', 456); await store.process(message(456, 'Питання'));
  assert.equal((await state(store, 456)).draft.step, 1);
  assert.equal(await store.sql('SELECT data FROM customer_profiles WHERE chat_id=456').first(), null);
});

test('standalone contact changes are atomic and stale or forged profile buttons cannot change data', async () => {
  const store = fresh();
  await store.process(message(123, '', { contact: { user_id: 123, phone_number: '380630000000', first_name: 'Олена' } }));
  await click(store, 'profile:open');
  const old = await click(store, 'profile:name');
  await store.process(message(123, 'Марія'));
  await store.process({ ...old, update_id: ++updateId });
  assert.equal((await state(store)).screen, 'profile');
  const s = await state(store);
  await store.process({ update_id: ++updateId, callback_query: { id: 'forged', from: { id: 456 },
    message: { chat: { id: 123, type: 'private' } }, data: `${s.revision}|profile:phone` } });
  assert.deepEqual(await state(store), s);
  await click(store, 'profile:phone');
  const before = await state(store), profile = await store.sql('SELECT data FROM customer_profiles').first();
  store.db.db.exec("CREATE TRIGGER fail_outbox BEFORE INSERT ON outbox BEGIN SELECT RAISE(ABORT,'test write failure'); END;");
  await assert.rejects(store.process(message(123, '+380671111111')));
  assert.deepEqual(await store.sql('SELECT data FROM customer_profiles').first(), profile);
  assert.deepEqual(await state(store), before);
  assert.equal(await store.sql('SELECT id FROM updates WHERE id=?', updateId).first(), null);
});

test('contact migration recovers newest individual fields, preserves null, isolates users and is repeatable', () => {
  const db = new DB().db;
  const migration = readFileSync(new URL('../migrations/0002_customer_profiles.sql', import.meta.url), 'utf8');
  assert.equal(migration, readFileSync(new URL('../../telegram_bot/profiles.sql', import.meta.url), 'utf8'));
  const add = db.prepare(`INSERT INTO requests(draft_key,chat_id,flow,payload,summary,created_at,created_ts) VALUES(?,?,'callback',?,'Original','2026-10-31',?)`);
  add.run('old', 123, JSON.stringify({ answers: { name: 'Олена', phone: '+380630000000', time: 'Ранок', topic: 'not contact data' } }), 1);
  add.run('new', 123, JSON.stringify({ answers: { phone: '+380671111111' } }), 2);
  add.run('other', 456, JSON.stringify({ answers: { name: 'Other' } }), 3);
  db.prepare('INSERT INTO sessions VALUES(?,?,?)').run(123, JSON.stringify({ draft: { answers: { name: 'Марія', time: null } } }), 4);
  const requests = db.prepare('SELECT * FROM requests').all(), sessions = db.prepare('SELECT * FROM sessions').all();
  db.exec(migration); db.exec(migration);
  assert.deepEqual(JSON.parse(db.prepare('SELECT data FROM customer_profiles WHERE chat_id=123').get().data),
    { name: 'Марія', phone: '+380671111111', time: null });
  assert.deepEqual(JSON.parse(db.prepare('SELECT data FROM customer_profiles WHERE chat_id=456').get().data), { name: 'Other' });
  db.prepare('UPDATE customer_profiles SET data=? WHERE chat_id=123').run(JSON.stringify({ name: 'Updated' }));
  db.exec(migration);
  assert.deepEqual(JSON.parse(db.prepare('SELECT data FROM customer_profiles WHERE chat_id=123').get().data), { name: 'Updated' });
  assert.deepEqual(db.prepare('SELECT * FROM requests').all(), requests);
  assert.deepEqual(db.prepare('SELECT * FROM sessions').all(), sessions);
});
