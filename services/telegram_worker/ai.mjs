// Owner-only, stateless pilot. The cloud never receives the OpenAI credential.
import quickInputs from '../../apps/warehouse_core/warehouse_core/assistant_content/quick_inputs.json' with { type: 'json' };

export async function cloudUsage(env) {
  try {
    const response = await env.QUOTA_GUARD.fetch('https://quota.internal/usage', { signal: AbortSignal.timeout(3000) });
    if (response.ok) {
      const { text } = await response.json();
      if (typeof text === 'string' && text.length <= 1200) return text;
    }
  } catch {}
  return 'Cloudflare · дані недоступні.';
}

export const unavailable = 'З’єднання з Вектор ERP зараз недоступне. Спробуйте ще раз, коли зв’язок із сервером ERP відновиться. Звичайне меню бота доступне.';
export const erpKeyboard = { inline_keyboard: [
  [{ text: 'Векторія ✦ ШІ-помічник', callback_data: 'erp:assistant' }]
] };
const panel = 'Вектор ERP\n\nВекторія знаходить товари й залишки та пояснює, як працювати у Вектор ERP. Відкрийте чат із Векторією й напишіть запитання.';
const inputPrompts = {
  assistant: 'Векторія ✦ ШІ-помічник · Вектор ERP\n\nОберіть розділ готових запитань або напишіть своє. Доступ лише для читання.',
  draft: 'Чернетка відповіді клієнту',
  search: '🔎 Без ШІ\n\nНапишіть назву товару або артикул одним повідомленням. Щоб переглянути всі залишки, напишіть «залишки». Перевірю актуальні дані в ERP без виклику моделі.',
  ai: '🤖 З ШІ\n\nЗапитайте про залишок, характеристики чи обидва разом. Векторія вибере потрібне джерело.',
  knowledge: '📚 База знань · ШІ\n\nНапишіть запитання. Відповім лише за доступними, опублікованими та дозволеними для ШІ статтями.'
};
const closeButton = '↩️ Завершити';
const closedText = 'Розмову з Векторією завершено. Щоб повернутися, відкрийте /ai.';
const sectionNames = { assistant: 'Залишки та база знань', draft: inputPrompts.draft };
const sectionMenu = { inline_keyboard: [
  ...Object.entries(sectionNames).map(([key, text]) => [{ text, callback_data: `erp:list:${key}:0` }]),
  [{ text: closeButton, callback_data: 'erp:close' }]
] };
function questionButton({ label, id }) {
  return { text: label, callback_data: `erp:q:${id}` };
}
function questionPrompt(text, kind) {
  if (kind === 'draft') return `чернетка: ${text}`;
  if (kind === 'knowledge') return `знання: ${text}`;
  return text;
}
function questionMenu(section, page) {
  const entries = quickInputs.filter(item => (item.kind === 'draft') === (section === 'draft'));
  const start = page * 10, buttons = entries.slice(start, start + 10).map(questionButton);
  const rows = [];
  for (let i = 0; i < buttons.length; i += 2) rows.push(buttons.slice(i, i + 2));
  const nav = [];
  if (page > 0) nav.push({ text: '‹ Попередні', callback_data: `erp:list:${section}:${page - 1}` });
  if (start + 10 < entries.length) nav.push({ text: 'Наступні ›', callback_data: `erp:list:${section}:${page + 1}` });
  if (nav.length) rows.push(nav);
  return { inline_keyboard: [...rows, ...inputKeyboard.inline_keyboard] };
}
export const inputKeyboard = { inline_keyboard: [[{ text: 'Готові запитання', callback_data: 'erp:assistant' }, { text: '↩️ Завершити', callback_data: 'erp:close' }]] };
export class AIQueue {
  constructor(bot, env, clock = () => Date.now() / 1000) { this.bot = bot; this.env = env; this.clock = clock; }
  sql(...args) { return this.bot.sql(...args); }
  async process(update, onAccepted) {
    const callback = update.callback_query, m = callback?.message || update.message;
    const sender = callback?.from || m?.from;
    const raw = typeof m?.text === 'string' && !callback ? m.text.trim() : '';
    const command = /^\/(ai|erp)(?:@mtech_customer_support_bot)?(?:\s+([\s\S]*))?$/i.exec(raw);
    const homeButton = typeof callback?.data === 'string' && /^[a-z0-9]{8}\|erp:home$/.test(callback.data);
    const erpCallback = typeof callback?.data === 'string' && (callback.data.startsWith('erp:') || homeButton);
    const owner = Number(this.env.TELEGRAM_STAFF_CHAT_ID);
    // A group or another customer's command never reaches the model or queue.
    if (m?.chat?.type !== 'private' || sender?.id !== m.chat.id || sender?.is_bot) return !!(command || erpCallback);
    // Private customer commands go to the regular handoff, without touching the AI tables.
    if (!Number.isSafeInteger(owner) || owner <= 0 || m.chat.id !== owner) return erpCallback;
    const key = `erp_input:${owner}`;
    for (let attempt = 0; attempt < 4; attempt++) {
      if (await this.sql('SELECT id FROM updates WHERE id=?', update.update_id).first()) return !!(command || erpCallback) || !callback;
      const row = await this.sql('SELECT value FROM meta WHERE key=?', key).first();
      const input = row ? JSON.parse(row.value) : null;
      if (!command && !erpCallback && (callback || raw.startsWith('/') || !input)) {
        // Returning to customer menus/forms cancels only the ERP prompt, not the draft.
        if (row) {
          const clear = this.sql('DELETE FROM meta WHERE key=? AND value=?', key, row.value);
          if (input.replyKeyboard) {
            await this.bot.db.batch([this.sql(`INSERT INTO outbox(method,payload,chat_id,created_at)
              SELECT 'sendMessage',?,?,? FROM meta WHERE key=? AND value=?`,
            JSON.stringify({ chat_id: owner, text: closedText, reply_markup: { remove_keyboard: true } }),
            owner, this.clock(), key, row.value), clear]);
          } else await clear.run();
        }
        return false;
      }
      const now = this.clock();
      let action = erpCallback ? (homeButton ? 'home' : callback.data.slice(4)) : null;
      let prompt = '', text = panel, markup = erpKeyboard, nextInput = null, queued = false;
      if (homeButton) {
        const session = await this.sql('SELECT state FROM sessions WHERE chat_id=?', owner).first();
        const state = session ? JSON.parse(session.state) : {};
        if (callback.data !== `${state.revision}|erp:home` || !Object.hasOwn(state.buttons || {}, 'erp:home')) action = 'stale';
      }
      if (command) {
        const argument = (command[2] || '').trim();
        if (argument) prompt = argument;
        else action = 'assistant';
      }
      if (action === 'home') action = 'assistant';
      const list = /^list:(assistant|draft):([01])$/.exec(action || '');
      if (list) action = list[1];
      const topic = action?.startsWith('q:') && quickInputs.find(item => item.id === action.slice(2));
      if (!callback && input?.replyKeyboard && raw === closeButton) action = 'close';
      const choice = /^choose:(stock|specs):(\d+)$/.exec(action || '');
      const canClarify = input?.subject && input.expires >= now;
      if (choice && canClarify && Number(choice[2]) === input.lastJob) {
        prompt = choice[1] === 'stock' ? `залишки: ${input.subject}` : `знання: ${input.subject} — характеристики`;
      } else if (choice) {
        text = 'Це уточнення вже завершено. Напишіть нове запитання з назвою товару.';
        nextInput = input?.expires >= now ? input : null;
        markup = nextInput ? inputKeyboard : erpKeyboard;
      } else if (topic) {
        prompt = questionPrompt(topic.question, topic.kind);
      } else if (action === 'stock') prompt = 'залишки';
      else if (Object.hasOwn(inputPrompts, action)) {
        nextInput = { mode: action, expires: now + 600, revision: crypto.randomUUID() };
        text = inputPrompts[action] + '\n\nЩоб завершити, натисніть «Завершити» або відкрийте /menu. Кожне запитання обробляється окремо.';
        markup = action === 'assistant' ? sectionMenu : inputKeyboard;
        if (list) {
          text = sectionNames[action] + '\n\nНатисніть тему, щоб одразу надіслати запитання.';
          markup = questionMenu(action, Number(list[2]));
        }
      } else if (action === 'close') text = closedText;
      else if (action) text = 'Кнопка застаріла. Відкрийте чат із Векторією.';
      if (!command && !erpCallback && !action) {
        if (!input || !Object.hasOwn(inputPrompts, input.mode) || input.expires < now) text = 'Час очікування завершився. Оберіть дію ще раз.';
        else if (!raw) { text = 'Надішліть запитання текстом або натисніть «Завершити».'; nextInput = input; markup = inputKeyboard; }
        else if (canClarify && /^(залишки|залишок|наявність)[.!?]?$/iu.test(raw)) prompt = `залишки: ${input.subject}`;
        else if (canClarify && /^(характеристики|база знань)[.!?]?$/iu.test(raw)) prompt = `знання: ${input.subject} — характеристики`;
        else if (input.mode === 'assistant' || input.mode === 'draft') {
          const entry = quickInputs.find(item => item.question === raw);
          prompt = questionPrompt(raw, entry?.kind ?? input.mode);
        }
        else prompt = input.mode === 'search' ? (/^(?:залишки|stock)$/i.test(raw) ? 'залишки' : `залишки: ${raw}`)
          : input.mode === 'knowledge' ? `знання: ${raw}` : `auto: ${raw}`;
      }
      if (prompt) {
        nextInput = (command || erpCallback) && !choice
          ? { mode: topic?.kind === 'draft' ? 'draft' : 'assistant', revision: crypto.randomUUID() } : { ...input };
        nextInput.expires = now + 600;
        markup = inputKeyboard;
        const beat = await this.sql("SELECT value FROM meta WHERE key='ai_bridge'").first();
        const status = beat ? JSON.parse(beat.value) : {};
        if (prompt.length > 2000) {
          text = 'Скоротіть повідомлення до 1900 символів і спробуйте ще раз.';
        } else if (this.env.AI_PILOT_ENABLED !== 'true' || status.ready !== true || now - status.at > 45 || status.at > now + 5) text = unavailable;
        else {
          queued = true;
          text = 'Дочекайтеся відповіді на попереднє запитання. Спільний ліміт можна перевірити через /usage.';
        }
      }
      if (nextInput && input?.replyKeyboard) nextInput.replyKeyboard = true;
      if (!nextInput && input?.replyKeyboard) markup = { remove_keyboard: true };
      const statements = [this.sql('INSERT INTO updates(id,received_at) VALUES(?,?)', update.update_id, now),
        this.sql(`INSERT OR REPLACE INTO commit_guard(id,valid) VALUES(1,
          CASE WHEN (SELECT value FROM meta WHERE key=?) IS ? THEN 1 ELSE 0 END)`, key, row?.value ?? null)];
      if (callback?.id) statements.push(this.bot.enqueue('answerCallbackQuery', { callback_query_id: callback.id }));
      // Remove a still-open reply keyboard when the owner returns to the topic menus.
      if (input?.replyKeyboard && (list || action === 'assistant')) {
        delete nextInput.replyKeyboard;
        statements.push(this.bot.enqueue('sendMessage', {
          chat_id: owner, text: 'Оберіть тему під повідомленням Векторії.', reply_markup: { remove_keyboard: true }
        }));
      }
      if (nextInput) statements.push(this.sql('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)', key, JSON.stringify(nextInput)));
      else statements.push(this.sql('DELETE FROM meta WHERE key=?', key));
      const jobInsertIndex = statements.length;
      if (queued) {
        // Serialize queued Telegram questions; ERP admits actual work across both channels.
        statements.push(this.sql(`INSERT INTO ai_jobs(id,chat_id,prompt,state,created_at)
          SELECT ?,?,?,'pending',? WHERE
          NOT EXISTS(SELECT 1 FROM ai_jobs WHERE chat_id=? AND prompt!='/usage' AND state IN ('pending','running'))`,
        update.update_id, owner, prompt, now, owner));
        statements.push(this.sql(`UPDATE meta SET value=json_remove(json_set(value,'$.lastJob',?),'$.subject')
          WHERE key=? AND EXISTS (SELECT 1 FROM ai_jobs WHERE id=?)`, update.update_id, key, update.update_id));
        // UI feedback belongs to the accepted job, never to a rejected or duplicate request.
        const progress = !topic && /^(?:залишки|stock)(?:\s*:[\s\S]*)?$/i.test(prompt)
          ? '🔎 Шукаю у Вектор ERP…' : '🤖 Готую відповідь…';
        const feedback = [
          ['sendMessage', { chat_id: owner, text: progress, disable_notification: true, _erp_progress: update.update_id }],
          ['sendChatAction', { chat_id: owner, action: 'typing', _erp_progress: update.update_id }]
        ];
        if (topic) feedback.unshift(['sendMessage', { chat_id: owner, text: topic.question, disable_notification: true }]);
        for (const [method, payload] of feedback) statements.push(this.sql(`INSERT INTO outbox(method,payload,chat_id,created_at)
          SELECT ?,?,?,? WHERE EXISTS(SELECT 1 FROM ai_jobs WHERE id=?)`,
        method, JSON.stringify(payload), owner, now, update.update_id));
        statements.push(this.sql(`INSERT INTO outbox(method,payload,chat_id,created_at)
          SELECT 'sendMessage',?,?,? WHERE NOT EXISTS(SELECT 1 FROM ai_jobs WHERE id=?)`,
        JSON.stringify({ chat_id: owner, text, reply_markup: markup }), owner, now, update.update_id));
      } else statements.push(this.bot.enqueue('sendMessage', { chat_id: owner, text, reply_markup: markup }));
      try {
        const results = await this.bot.db.batch(statements);
        if (queued && results[jobInsertIndex].meta?.changes === 1) onAccepted?.(owner);
        return true;
      }
      catch (error) {
        if (await this.sql('SELECT id FROM updates WHERE id=?', update.update_id).first()) return true;
        if (attempt === 3 || !/constraint/i.test(String(error))) throw error;
      }
    }
    throw new Error('Concurrent ERP input conflict');
  }
  async expire() {
    const now = this.clock();
    await this.bot.db.batch([
      this.sql(`INSERT INTO outbox(method,payload,chat_id,created_at)
        SELECT 'sendMessage',json_object('chat_id',chat_id,'text',?,'_erp_result',id,'reply_markup',json(?)),chat_id,? FROM ai_jobs
        WHERE state IN ('pending','running') AND created_at<?`, unavailable, JSON.stringify(erpKeyboard), now, now - 300),
      this.sql("UPDATE ai_jobs SET state='expired',prompt='' WHERE state IN ('pending','running') AND created_at<?", now - 300),
      this.sql("UPDATE meta SET value=json_remove(value,'$.subject') WHERE key LIKE 'erp_input:%' AND json_extract(value,'$.expires')<? AND json_type(value,'$.subject') IS NOT NULL", now),
      this.sql("DELETE FROM ai_jobs WHERE state IN ('complete','expired') AND created_at<?", now - 7 * 86400)
    ]);
  }
  async bridge(payload) {
    const now = this.clock();
    if (payload?.action === 'claim' && typeof payload.ready === 'boolean') {
      await this.sql("INSERT OR REPLACE INTO meta(key,value) VALUES('ai_bridge',?)",
        JSON.stringify({ at: now, ready: payload.ready && this.env.AI_PILOT_ENABLED === 'true' })).run();
      if (!payload.ready || this.env.AI_PILOT_ENABLED !== 'true') return { job: null };
      const claim = crypto.randomUUID();
      // Running jobs are never reclaimed: retries must never create a second paid request.
      const job = await this.sql(`UPDATE ai_jobs SET state='running',claim_id=? WHERE id=(
        SELECT id FROM ai_jobs WHERE state='pending' AND chat_id=? AND created_at>=? ORDER BY id LIMIT 1)
        AND state='pending' RETURNING id,prompt,claim_id`, claim, Number(this.env.TELEGRAM_STAFF_CHAT_ID), now - 300).first();
      if (job) await this.bot.enqueue('sendChatAction', {
        chat_id: Number(this.env.TELEGRAM_STAFF_CHAT_ID), action: 'typing', _erp_progress: job.id
      }, null, now).run().catch(() => {}); // A UI failure must not strand a claimed job.
      return { job };
    }
    if (payload?.action === 'complete' && Number.isSafeInteger(payload.id) && typeof payload.claim_id === 'string'
        && typeof payload.text === 'string' && payload.text.length > 0 && payload.text.length <= 3500) {
      const args = [payload.id, payload.claim_id, Number(this.env.TELEGRAM_STAFF_CHAT_ID)];
      const [answer] = await this.bot.db.batch([
        this.sql(`INSERT INTO outbox(method,payload,chat_id,created_at)
          SELECT 'sendMessage',json_object('chat_id',chat_id,'text',?,'link_preview_options',json('{"is_disabled":true}'),'reply_markup',json(?),'_erp_result',id),
          chat_id,? FROM ai_jobs WHERE id=? AND claim_id=? AND chat_id=? AND state='running'`, payload.text, JSON.stringify(erpKeyboard), now, ...args),
        ...(payload.clarify === true ? [this.sql(`UPDATE meta SET value=json_set(value,'$.subject',
          (SELECT CASE WHEN substr(prompt,1,6)='auto: ' THEN substr(prompt,7) ELSE prompt END FROM ai_jobs WHERE id=?))
          WHERE key=? AND json_extract(value,'$.lastJob')=? AND EXISTS
          (SELECT 1 FROM ai_jobs WHERE id=? AND claim_id=? AND chat_id=? AND state='running')`,
          payload.id, `erp_input:${args[2]}`, payload.id, ...args)] : []),
        this.sql("UPDATE ai_jobs SET state='complete',prompt='' WHERE id=? AND claim_id=? AND chat_id=? AND state='running'", ...args)
      ]);
      return { ok: true, queuedAnswer: answer.meta.changes === 1 };
    }
    throw new Error('Invalid bridge payload');
  }
}
