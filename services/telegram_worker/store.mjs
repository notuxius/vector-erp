import { Engine, RequestLimit, chunks, menuCommands } from './engine.mjs';
import { erpKeyboard, inputKeyboard } from './ai.mjs';

const nowSeconds = () => Date.now() / 1000;
const json = JSON.stringify;
export class Store {
  constructor(db, content, staffChatId, aiOwnerId = null) {
    this.db = db; this.c = content; this.staff = staffChatId;
    this.aiOwner = Number.isSafeInteger(aiOwnerId) && aiOwnerId > 0 ? aiOwnerId : null;
  }
  sql(query, ...values) { return this.db.prepare(query).bind(...values); }
  enqueue(method, payload, requestId = null, now = nowSeconds()) {
    return this.sql('INSERT INTO outbox(method,payload,chat_id,request_id,created_at) VALUES(?,?,?,?,?)',
      method, json(payload), payload.chat_id ?? payload.scope?.chat_id ?? null, requestId, now);
  }
  commands(chatId, hasDraft, subscribed = false) {
    return [this.sql("DELETE FROM outbox WHERE chat_id=? AND method IN ('setMyCommands','deleteMyCommands') AND status IN ('pending','failed')", chatId),
      ...['', 'uk'].map(language => {
        const commands = menuCommands(hasDraft, subscribed);
        if (chatId === this.aiOwner) commands.splice(1, 0,
          { command: 'ai', description: language === 'uk' ? 'Векторія ✦ ШІ-помічник' : 'Vectoria ✦ AI-assistant' },
          { command: 'usage', description: 'Використання та ліміти' });
        return this.enqueue('setMyCommands', { scope: { type: 'chat', chat_id: chatId }, language_code: language, commands });
      })];
  }
  async queueStaffReply(text, statements) {
    const match = /^\/reply(?:@mtech_customer_support_bot)?\s+([1-9]\d{0,14})\s+([\s\S]+)$/i.exec(text.trim());
    const body = match?.[2].trim();
    let feedback = this.c.texts.reply_usage;
    if (body && Array.from(body).length <= 1500) {
      const number = Number(match[1]);
      const request = await this.sql("SELECT chat_id FROM requests WHERE id=? AND flow='written'", number).first();
      feedback = this.c.texts.reply_missing;
      if (request) {
        const text = this.c.texts.staff_reply.replace(/\{(number|body)\}/g, (_, key) => key === 'number' ? number : body);
        statements.push(this.enqueue('sendMessage', { chat_id: request.chat_id, text, link_preview_options: { is_disabled: true } }, number));
        feedback = this.c.texts.reply_queued.replace('{number}', number);
      }
    }
    statements.push(this.enqueue('sendMessage', { chat_id: this.staff, text: feedback }));
  }
  async process(update) {
    // D1 batches are atomic. A compare-and-swap guard rejects concurrent state changes;
    // the whole update (state, lead, notifications and deduplication) commits together.
    for (let attempt = 0; attempt < 4; attempt++) {
      if (await this.sql('SELECT id FROM updates WHERE id=?', update.update_id).first()) return false;
      const callback = update.callback_query;
      const message = callback ? callback.message || {} : update.message || {};
      const sender = callback ? callback.from || {} : message.from || {};
      const chat = message.chat || {}, now = nowSeconds();
      const statements = [this.sql('INSERT INTO updates(id,received_at) VALUES(?,?)', update.update_id, now)];
      const replyCommand = !callback && /^\/reply(?:@mtech_customer_support_bot)?(?:\s|$)/i.test(message.text || '');
      if (replyCommand || chat.type !== 'private' || sender.id !== chat.id || sender.is_bot) {
        if (replyCommand && chat.id === this.staff && sender.id > 0 && !sender.is_bot && !message.sender_chat && !message.forward_origin &&
            (['group', 'supergroup'].includes(chat.type) || (chat.type === 'private' && sender.id === chat.id)))
          await this.queueStaffReply(message.text, statements);
        if (callback?.id) statements.push(this.enqueue('answerCallbackQuery', { callback_query_id: callback.id }));
        try { await this.db.batch(statements); return true; }
        catch (error) { if (await this.sql('SELECT id FROM updates WHERE id=?', update.update_id).first()) return false; throw error; }
      }
      const row = await this.sql('SELECT state FROM sessions WHERE chat_id=?', chat.id).first();
      const state = row ? JSON.parse(row.state) : {}, hadDraft = !!state.draft;
      let subscribed = !!(await this.sql('SELECT subscribed FROM subscriptions WHERE chat_id=?', chat.id).first())?.subscribed;
      const wasSubscribed = subscribed;
      let throttle = state.throttle || { start: now, count: 0 };
      if (now - throttle.start >= 60) throttle = { start: now, count: 0 };
      throttle.count++; state.throttle = throttle;
      // SQL NULL-safe IS also checks absent/new sessions. Failure rolls back dedup row.
      statements.push(this.sql(`INSERT OR REPLACE INTO commit_guard(id,valid)
        VALUES(1, CASE WHEN (SELECT state FROM sessions WHERE chat_id=?) IS ? THEN 1 ELSE 0 END)`, chat.id, row?.state ?? null));
      if (throttle.count > 30) {
        if (callback) statements.push(this.enqueue('answerCallbackQuery', { callback_query_id: callback.id, text: this.c.texts.rate_limit }));
        else if (throttle.count === 31) statements.push(this.enqueue('sendMessage', { chat_id: chat.id, text: this.c.texts.rate_limit }));
      } else {
        // Submission data is read only for a valid submit button.
        const submitting = callback?.data === `${state.revision}|submit` && Object.hasOwn(state.buttons || {}, 'submit') && state.draft;
        let existing = null, count = 0, number = null;
        if (submitting) {
          const results = await this.db.batch([
            this.sql('SELECT id FROM requests WHERE draft_key=?', state.draft.key),
            this.sql('SELECT COUNT(*) AS n FROM requests WHERE chat_id=? AND created_ts>?', chat.id, now - 3600),
            this.sql("SELECT MAX(COALESCE((SELECT MAX(id) FROM requests),0), COALESCE((SELECT seq FROM sqlite_sequence WHERE name='requests'),0))+1 AS n")
          ]);
          existing = results[0].results[0]; count = results[1].results[0].n; number = results[2].results[0].n;
        }
        const submit = (draft, summary) => {
          if (existing) return String(existing.id);
          if (count >= 5) throw new RequestLimit();
          if (!number) throw new Error('Invalid submission');
          const created = new Date(now * 1000).toISOString().replace(/\.\d{3}Z$/, '+00:00');
          const payload = { flow: draft.flow, answers: draft.answers, category: draft.category ?? null,
            category_url: draft.category_url ?? null, origin: draft.origin ?? null, content_reviewed_at: this.c.reviewed_at };
          statements.push(this.sql(`INSERT INTO requests(id,draft_key,chat_id,flow,payload,summary,created_at,created_ts)
            VALUES(?,?,?,?,?,?,?,?)`, number, draft.key, chat.id, draft.flow, json(payload), summary, created, now));
          if (this.staff !== null) {
            let notice = this.c.texts.staff_notice.replace(/\{(number|summary|created_at)\}/g,
              (_, key) => ({ number, summary, created_at: created })[key]);
            if (draft.flow === 'written') notice += '\n\n' + this.c.texts.reply_hint.replace('{number}', number);
            for (const text of chunks(notice)) statements.push(this.enqueue('sendMessage', { chat_id: this.staff, text,
              link_preview_options: { is_disabled: true } }, number));
            for (const photo of draft.answers.photos || []) statements.push(this.enqueue('sendPhoto', {
              chat_id: this.staff, photo: photo.file_id, caption: `${this.c.labels.request} №${number}` }, number));
          }
          return String(number);
        };
        const customer = {
          staff: chat.id === this.staff, erp: chat.id === this.aiOwner,
          requests: (await this.sql('SELECT id,flow,status,created_at FROM requests WHERE chat_id=? ORDER BY created_ts DESC,id DESC LIMIT 10', chat.id).all()).results,
          subscribed,
          profile: JSON.parse((await this.sql('SELECT data FROM customer_profiles WHERE chat_id=?', chat.id).first())?.data || '{}')
        };
        const subscribe = enabled => { subscribed = enabled; statements.push(this.sql(`INSERT INTO subscriptions(chat_id,subscribed,updated_at,consent_version)
          VALUES(?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET subscribed=excluded.subscribed,
          updated_at=excluded.updated_at,consent_version=excluded.consent_version`, chat.id, Number(enabled), now, 'mtech-news-v1')); };
        const saveProfile = data => statements.push(this.sql(`INSERT INTO customer_profiles(chat_id,data,updated_at) VALUES(?,?,?)
          ON CONFLICT(chat_id) DO UPDATE SET data=excluded.data,updated_at=excluded.updated_at`, chat.id, json(data), now));
        const engine = new Engine(this.c, state, chat.id, submit, customer, subscribe, saveProfile);
        for (const [method, payload] of engine.handle(message, callback)) statements.push(this.enqueue(method, payload));
      }
      const menuVersion = chat.id === this.aiOwner ? '15-ai' : 7;
      if (wasSubscribed !== subscribed || hadDraft !== !!state.draft || state.command_menu_version !== menuVersion) {
        statements.push(...this.commands(chat.id, !!state.draft, subscribed)); state.command_menu_version = menuVersion;
      }
      statements.push(this.sql('INSERT OR REPLACE INTO sessions(chat_id,state,updated_at) VALUES(?,?,?)', chat.id, json(state), now));
      try { await this.db.batch(statements); return true; }
      catch (error) {
        // Re-read from the primary on a collision; never retry Telegram sends here.
        if (await this.sql('SELECT id FROM updates WHERE id=?', update.update_id).first()) return false;
        if (attempt === 3 || !/constraint/i.test(String(error))) throw error;
      }
    }
    throw new Error('Concurrent update conflict');
  }
  async acquire(name, owner, ttl = 90) {
    const now = nowSeconds();
    return !!await this.sql(`INSERT INTO leases(name,owner,expires_at) VALUES(?,?,?)
      ON CONFLICT(name) DO UPDATE SET owner=excluded.owner,expires_at=excluded.expires_at
      WHERE leases.expires_at<? RETURNING owner`, name, owner, now + ttl, now).first();
  }
  async release(name, owner) { await this.sql('DELETE FROM leases WHERE name=? AND owner=?', name, owner).run(); }
  async deliver(api, row) {
    const { _erp_progress: progress, _erp_result: result, ...payload } = JSON.parse(row.payload);
    const key = `erp_progress:${result ?? progress}`;
    if (progress !== undefined) {
      const job = await this.sql('SELECT state,created_at FROM ai_jobs WHERE id=? AND chat_id=?', progress, row.chat_id).first();
      // Feedback is disposable: stale indicators and failed typing must never delay an answer.
      if (!job || !['pending', 'running'].includes(job.state) || job.created_at < nowSeconds() - 300) return;
      try {
        const receipt = await api.call(row.method, payload);
        if (row.method === 'sendMessage' && Number.isSafeInteger(receipt?.message_id))
          await this.sql('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)', key, String(receipt.message_id)).run();
      } catch { /* Best effort only; the durable answer still follows. */ }
      return;
    }
    if (result !== undefined) {
      // The owner can leave the assistant while an answer is waiting for delivery.
      const input = await this.sql('SELECT value FROM meta WHERE key=?', `erp_input:${row.chat_id}`).first();
      const session = input && JSON.parse(input.value);
      payload.reply_markup = session?.expires >= nowSeconds() ? inputKeyboard : erpKeyboard;
      if (payload.reply_markup === inputKeyboard && session.lastJob === result && session.subject) {
        payload.reply_markup = { inline_keyboard: [[
          { text: 'Залишок', callback_data: `erp:choose:stock:${result}` },
          { text: 'Характеристики', callback_data: `erp:choose:specs:${result}` }
        ], ...inputKeyboard.inline_keyboard] };
      }
      const receipt = await this.sql('SELECT value FROM meta WHERE key=?', key).first();
      await api.call('sendMessage', payload, row.id);
      if (receipt) {
        try { await api.call('deleteMessage', { chat_id: row.chat_id, message_id: Number(receipt.value) }); }
        catch { /* Placeholder cleanup is best effort. The answer has already arrived. */ }
      }
      return key;
    }
    await api.call(row.method, payload);
  }
  async dispatch(api, budget = 12) {
    const owner = crypto.randomUUID();
    if (!await this.acquire('dispatch', owner)) return;
    const deadline = Date.now() + 20000;
    try {
      for (let sent = 0; sent < budget && Date.now() < deadline; sent++) {
        const row = await this.sql(`SELECT o.* FROM outbox o WHERE o.status='pending' AND o.available_at<=?
          AND (o.chat_id IS NULL OR NOT EXISTS (SELECT 1 FROM outbox p WHERE p.chat_id=o.chat_id
            AND p.status='pending' AND p.id<o.id))
          ORDER BY CASE WHEN o.method='answerCallbackQuery' THEN 0 ELSE 1 END,o.id LIMIT 1`, nowSeconds()).first();
        if (!row) break;
        let receiptKey;
        try { receiptKey = await this.deliver(api, row); }
        catch (error) {
          const attempts = row.attempts + 1;
          const terminal = [400, 401, 403, 404].includes(error.code) || row.method === 'answerCallbackQuery' || attempts >= 12;
          const delay = Math.max(error.retryAfter || 0, Math.min(300, 2 ** Math.min(attempts, 8)));
          await this.sql('UPDATE outbox SET status=?,attempts=?,available_at=?,error=? WHERE id=?',
            terminal ? 'failed' : 'pending', attempts, nowSeconds() + delay, `Telegram API error ${error.code || 'network'}`, row.id).run();
          if (error.code === 429) break;
          continue;
        }
        const committed = [this.sql("UPDATE outbox SET status='sent',payload='{}',error=NULL WHERE id=?", row.id)];
        if (receiptKey) committed.push(this.sql('DELETE FROM meta WHERE key=?', receiptKey));
        await this.db.batch(committed);
      }
    } finally { await this.release('dispatch', owner); }
  }
  async maintenance() {
    const owner = crypto.randomUUID();
    if (!await this.acquire('maintenance', owner)) return;
    try {
      const last = await this.sql("SELECT value FROM meta WHERE key='maintenance_at'").first();
      const now = nowSeconds(); if (Number(last?.value || 0) > now - 3600) return;
      const expired = await this.sql('SELECT chat_id,state FROM sessions WHERE updated_at<? LIMIT 10', now - 7 * 86400).all();
      for (const row of expired.results) {
        const subscribed = !!(await this.sql('SELECT subscribed FROM subscriptions WHERE chat_id=?', row.chat_id).first())?.subscribed;
        // Guard prevents expiring a session that received an update after our read.
        try {
          await this.db.batch([
            this.sql(`INSERT OR REPLACE INTO commit_guard(id,valid) VALUES(1,
              CASE WHEN EXISTS(SELECT 1 FROM sessions WHERE chat_id=? AND state=? AND updated_at<?) THEN 1 ELSE 0 END)`, row.chat_id, row.state, now - 7 * 86400),
            ...this.commands(row.chat_id, false, subscribed), this.sql('DELETE FROM sessions WHERE chat_id=?', row.chat_id)
          ]);
        } catch (error) { if (!/constraint/i.test(String(error))) throw error; }
      }
      await this.db.batch([
        this.sql('DELETE FROM updates WHERE id IN (SELECT id FROM updates WHERE received_at<? LIMIT 500)', now - 7 * 86400),
        this.sql("DELETE FROM outbox WHERE id IN (SELECT id FROM outbox WHERE status='sent' AND created_at<? LIMIT 500)", now - 7 * 86400),
        this.sql("INSERT OR REPLACE INTO meta(key,value) VALUES('maintenance_at',?)", String(now))
      ]);
    } finally { await this.release('maintenance', owner); }
  }
}
