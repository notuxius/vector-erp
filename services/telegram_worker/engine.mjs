// Conversation rules match the local Python bot. Both use the same content.json.
export const pop = (object, key, fallback = null) => {
  const value = object[key] ?? fallback;
  delete object[key];
  return value;
};
export const truncate = (text, length) => Array.from(text).slice(0, length).join('');
export function chunks(text, limit = 3500) {
  const result = []; let part = '';
  for (const ch of text) {
    if (part.length + ch.length > limit) { result.push(part); part = ''; }
    part += ch;
  }
  if (part) result.push(part);
  return result;
}
export function normalizePhone(value) {
  if (typeof value !== 'string' || value.length > 40) return null;
  value = value.replace(/[\s()\-]/g, '').replace(/^00/, '+');
  return /^\+[1-9][0-9]{7,14}$/.test(value) ? value : null;
}
export function menuCommands(draft = false, subscribed = false) {
  const commands = [{ command: 'menu', description: 'Головне меню' },
    { command: 'help', description: 'Допомога' }];
  // Subscription controls content and delivery, never command visibility.
  if (draft) commands.push({ command: 'cancel', description: 'Скасувати чернетку' });
  return commands;
}
export class RequestLimit extends Error {}
export const validTime = value => typeof value === 'string' && /^(?:[01][0-9]|2[0-3]):[0-5][0-9]$/.test(value) && value.length === 5;
export function normalizeTime(value) {
  const match = typeof value === 'string' ? /^([0-9]{1,2})(?:(?::| +)([0-9]{1,2}))?$/.exec(value) : null;
  if (!match || match[0] !== value || Number(match[1]) > 23 || Number(match[2] || '0') > 59) return null;
  return `${match[1].padStart(2, '0')}:${(match[2] || '0').padStart(2, '0')}`;
}
const kyivClock = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'Europe/Kyiv', weekday: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
});
export class Engine {
  constructor(content, state, chatId, submit, customer = {}, subscribe = () => {}, saveProfile = () => {}) {
    this.c = content; this.s = state; this.chatId = chatId; this.submit = submit; this.actions = [];
    this.customer = customer; this.subscribe = subscribe;
    this.saveProfile = saveProfile;
  }
  text(key, values = {}) { return this.c.texts[key].replace(/\{(\w+)\}/g, (_, k) => String(values[k])); }
  label(key) { return this.c.labels[key]; }
  buttonLabel(label) { return this.c.button_icons[label] ? `${this.c.button_icons[label]} ${label}` : label; }
  send(text, markup) {
    const parts = chunks(text);
    parts.forEach((part, i) => {
      const payload = { chat_id: this.chatId, text: part, link_preview_options: { is_disabled: true } };
      if (markup && i === parts.length - 1) payload.reply_markup = markup;
      this.actions.push(['sendMessage', payload]);
    });
  }
  screen(text, items, columns = 1, phone = false) {
    if (pop(this.s, 'phone_keyboard', false) && !phone) this.send(this.text('continue_form'), { remove_keyboard: true });
    const revision = crypto.randomUUID().replaceAll('-', '').slice(0, 8);
    this.s.revision = revision;
    this.s.buttons = Object.fromEntries(items.filter(([, a]) => !a.startsWith('https:')).map(([l, a]) => [a, l]));
    if (phone) {
      this.s.phone_keyboard = true;
      this.send(text, { keyboard: [[{ text: this.buttonLabel(this.label('share_phone')), request_contact: true }],
        items.filter(([, action]) => ['back', 'cancel'].includes(action)).map(([label]) => this.buttonLabel(label))], resize_keyboard: true, one_time_keyboard: true });
      return;
    }
    const buttons = items.map(([label, action]) => ({ text: this.buttonLabel(label),
      ...(action.startsWith('https:') ? { url: action } : { callback_data: `${revision}|${action}` }) }));
    const rows = []; for (let i = 0; i < buttons.length; i += columns) rows.push(buttons.slice(i, i + columns));
    this.send(text, { inline_keyboard: rows });
  }
  nav(parent = 'home') {
    const items = [[this.label('back'), `menu:${parent}`]];
    if (parent !== 'home') items.push([this.label('home'), 'menu:home']);
    return items;
  }
  fallback(question = '', notice = '', timestamp) {
    const [kind, key] = (this.s.screen || '').split(':');
    const topic = kind === 'category' ? 'catalogue' : kind === 'info' ? this.c.info[key]?.source : key;
    this.s.screen = 'fallback';
    const limit = this.c.fields.message.max_length;
    this.s.pending_text = Array.from(question).length <= limit ? question : '';
    if (question && !this.s.pending_text) notice = this.text('handoff_long', { limit });
    const parts = kyivClock.formatToParts(timestamp === undefined ? Date.now() : timestamp * 1000);
    const local = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
    const time = local.hour + ':' + local.minute, hours = this.c.working_hours;
    const items = [[this.label('callback'), 'flow:callback'], [this.label('written'), 'flow:written']];
    let text = this.text('fallback');
    if (['Sat', 'Sun'].includes(local.weekday) || time < hours.start || time >= hours.end) {
      text = this.text('fallback_closed', hours);
      const priority = link => {
        if (link.keywords.some(word => question.toLowerCase().includes(word))) return 0;
        return link.topics.includes(topic) ? 1 : 2;
      };
      for (const link of [...this.c.self_help].sort((a, b) => priority(a) - priority(b)))
        items.push([link.label, link.url]);
    }
    this.screen((notice ? notice + '\n\n' : '') + text, [...items, [this.label('home'), 'menu:home']]);
  }
  menu(name = 'home') {
    delete this.s.pending_text;
    delete this.s.profile_field;
    this.s.screen = `menu:${name}`;
    if (name === 'status') {
      const rows = (this.customer.requests || []).map(r => this.text('status_row', {
        number: r.id, date: r.created_at.slice(0, 10), topic: this.c.flows[r.flow]?.title || this.label('request'),
        status: this.c.texts[`status_${r.status}`] || this.text('status_unknown') }));
      this.screen(this.text('status') + '\n\n' + (rows.join('\n\n') || this.text('status_empty')),
        [[this.label('status_refresh'), 'menu:status'], [this.label('status_ask'), 'flow:status_request'], ...this.nav()]);
      return;
    }
    if (name === 'news') {
      const subscribed = !!this.customer.subscribed;
      const items = subscribed ? (this.c.news_items || []).map(item => `${item.date} · ${item.title}\n${item.body}`) : [];
      this.screen([this.text('news'), this.text(subscribed ? 'news_on' : 'news_off'), ...items].join('\n\n'),
        [[this.label(subscribed ? 'unsubscribe' : 'subscribe'), subscribed ? 'news:off' : 'news:on'], ...this.nav()]);
      return;
    }
    const menu = this.c.menus[name];
    const items = [...menu.items];
    if (name === 'home' && Object.keys(this.customer.profile || {}).length)
      items.splice(items.findIndex(([, action]) => action === 'menu:status') + (this.customer.erp ? 1 : 0), 0, [this.label('update_info'), 'profile:open']);
    this.screen(this.text(menu.text), [...items, ...(name !== 'home' ? this.nav() : [])], menu.columns);
  }
  info(name) {
    this.s.screen = `info:${name}`; const info = this.c.info[name];
    this.screen(info.text, [...info.items, ...this.nav(info.parent)]);
  }
  category(name) {
    this.s.screen = `category:${name}`; const [title, parent, url] = this.c.categories[name];
    this.screen(this.text('category', { name: title }), [[this.label('website'), url], [this.label('ask'), `flow:${parent}`], ...this.nav(parent)]);
  }
  fields() {
    const flow = this.c.flows[this.s.draft.flow];
    return [...flow.fields, ...(flow.contact_fields ?? this.c.contact_fields)];
  }
  remember(key, value) {
    this.customer.profile ||= {}; this.customer.profile[key] = value;
    this.saveProfile({ ...this.customer.profile });
  }
  timeNotice(value) {
    const hours = this.c.working_hours;
    return validTime(value) && !(hours.start <= value && value < hours.end)
      ? '\n\n' + this.text('outside_working_hours', { time: value, ...hours }) : '';
  }
  sharedContact(contact) {
    if (contact.user_id !== this.chatId) { this.send(this.text('foreign_contact')); return; }
    const value = contact.phone_number || '', phone = normalizePhone(value.startsWith('+') ? value : '+' + value);
    if (!phone) { this.send(this.text('invalid_phone')); return; }
    this.remember('phone', phone);
    const name = [contact.first_name, contact.last_name].filter(Boolean).join(' ').trim();
    if (name && Array.from(name).length <= this.c.fields.name.max_length && !/[\x00-\x1f]/.test(name) && !this.customer.profile.name) this.remember('name', name);
    this.send(this.text('profile_saved')); this.menu();
  }
  profile() {
    delete this.s.profile_field; this.s.screen = 'profile';
    const summary = this.c.contact_fields.map(key => `${this.c.fields[key].label}: ${this.customer.profile?.[key] || this.label('not_provided')}`).join('\n');
    this.screen(this.text('profile', { summary }) + this.timeNotice(this.customer.profile?.time), [...this.c.contact_fields.map(key => [this.c.fields[key].label, `profile:${key}`]),
      [this.label('back'), 'profile:done']]);
  }
  profileField(key) {
    this.s.profile_field = key; this.s.screen = 'profile_field';
    const field = this.c.fields[key], items = (field.options || []).map((v, i) => [v, `option:${i}`]);
    if (field.optional) items.push([this.label('skip'), 'skip']);
    this.screen(field.prompt, [...items, [this.label('back'), 'back']], 1, field.type === 'phone');
  }
  startFlow(name) {
    const previous = this.s.screen || '';
    const draft = { key: crypto.randomUUID().replaceAll('-', ''), flow: name, step: 0, answers: {} };
    const profile = this.customer.profile || {};
    draft.reused_contacts = (this.c.flows[name].contact_fields ?? this.c.contact_fields).filter(key => Object.hasOwn(profile, key) && (key !== 'time' || validTime(profile[key])));
    for (const key of draft.reused_contacts) draft.answers[key] = profile[key];
    if (previous.startsWith('category:')) {
      const [title, parent, url] = this.c.categories[previous.slice(9)];
      if (parent === name) { draft.category = title; draft.category_url = url; }
    } else if (previous.startsWith('info:')) draft.origin = previous;
    const pending = pop(this.s, 'pending_text');
    if (previous === 'fallback' && ['general', 'written', 'callback'].includes(name) && pending) {
      const field = this.c.flows[name].fields[0];
      draft.answers[field] = truncate(pending, this.c.fields[field].max_length);
      draft.step = 1;
    }
    this.s.draft = draft;
    if (this.c.flows[name].intro) this.send(this.text(this.c.flows[name].intro));
    this.prompt();
  }
  prompt() {
    const draft = this.s.draft;
    while (!draft.editing && draft.step < this.fields().length && (draft.reused_contacts || []).includes(this.fields()[draft.step])) draft.step++;
    if (draft.step >= this.fields().length) { this.review(); return; }
    this.s.screen = 'field';
    const key = this.fields()[draft.step], field = this.c.fields[key], unknown = draft.unknown_field === key;
    let text = unknown ? field.unknown_prompt : field.prompt;
    const items = unknown ? [] : (field.options || []).map((v, i) => [v, `option:${i}`]);
    if (field.type === 'photos') {
      text += '\n' + this.text('photo_prompt');
      if (draft.answers[key]?.length) { text += '\n' + this.text('photo_added', { count: draft.answers[key].length }); items.push([this.label('done'), 'done']); }
    }
    if (field.optional) items.push([this.label('skip'), 'skip']);
    items.push([this.label('back'), 'back'], [this.label('cancel'), 'cancel']);
    this.screen(text, items, 1, field.type === 'phone');
  }
  summary() {
    const d = this.s.draft, lines = [`${this.label('topic')}: ${this.c.flows[d.flow].title}`];
    if (d.category) lines.push(`${this.label('category')}: ${d.category}`, d.category_url);
    for (const key of this.fields()) {
      const field = this.c.fields[key]; let value = d.answers[key];
      if (field.type === 'photos') value = String((value || []).length);
      lines.push(`${field.label}: ${value || this.label('not_provided')}`);
    }
    return lines.join('\n');
  }
  review() {
    if (this.fields().includes('time') && !validTime(this.s.draft.answers.time)) {
      this.s.draft.step = this.fields().indexOf('time'); this.s.draft.editing = true;
      this.prompt(); return;
    }
    const items = [[this.label('submit'), 'submit']];
    if (this.fields().includes('phone')) items.push([this.label('update_info'), 'profile:open']);
    this.s.screen = 'review'; this.screen(this.text('review', { summary: this.summary() }) + this.timeNotice(this.s.draft.answers.time),
      [...items, [this.label('edit'), 'edit'], [this.label('cancel'), 'cancel']]);
  }
  edit() {
    this.s.screen = 'edit'; this.screen(this.text('edit'), [...this.fields().map(k => [this.c.fields[k].label, `edit:${k}`]), [this.label('return_review'), 'review']]);
  }
  exitDraft() {
    delete this.s.profile_field;
    this.s.screen = 'exit'; this.screen(this.text('draft_exit'), [[this.label('resume'), 'resume'], [this.label('discard'), 'discard']]);
  }
  accept(value) {
    const profileKey = this.s.profile_field;
    const key = profileKey || this.fields()[this.s.draft.step];
    if (this.c.fields[key].type === 'time') {
      value = normalizeTime(value);
      if (value === null) { this.send(this.text('invalid_time')); return; }
    }
    if (profileKey) {
      this.remember(profileKey, value);
      if (this.s.draft) this.s.draft.answers[profileKey] = value;
      this.profile(); return;
    }
    const d = this.s.draft; delete d.unknown_field; d.answers[this.fields()[d.step]] = value;
    if (this.c.contact_fields.includes(this.fields()[d.step])) this.remember(this.fields()[d.step], value);
    d.step = pop(d, 'editing', false) ? this.fields().length : d.step + 1; this.prompt();
  }
  handle(message, callback) {
    if (callback) {
      const data = callback.data || '';
      this.actions.push(['answerCallbackQuery', { callback_query_id: callback.id }]);
      if (data === this.s.last_submission?.callback) { this.send(this.text('already_saved', { number: this.s.last_submission.number })); return this.actions; }
      const split = data.indexOf('|'), revision = data.slice(0, split), action = data.slice(split + 1);
      if (split < 0 || revision !== this.s.revision || !Object.hasOwn(this.s.buttons || {}, action)) this.send(this.text('stale'));
      else this.action(action, data);
      return this.actions;
    }
    let text = (message.text || '').trim();
    const labels = [...Object.values(this.s.buttons || {}), ...['home', 'back', 'cancel'].map(k => this.label(k))];
    text = labels.find(label => this.buttonLabel(label) === text) || text;
    const command = text.split(' ')[0].split('@')[0].toLowerCase();
    if (['/start', '/menu', '/cancel'].includes(command) || text === this.label('home')) this.s.draft ? this.exitDraft() : this.menu();
    else if (command === '/help') {
      this.send(this.text('help') + (this.customer.erp ? '\n\n' + this.text('help_ai') : ''));
      if (this.customer.staff) this.send(this.text('help_staff'));
    }
    else if (['/status', '/news'].includes(command)) this.s.draft ? this.exitDraft() : this.menu(command.slice(1));
    else if ([this.label('back'), this.label('cancel')].includes(text) && (this.s.draft || this.s.profile_field)) this.action(text === this.label('back') ? 'back' : 'cancel');
    else if (message.voice || message.audio || message.video_note) {
      if (this.s.draft) this.send(this.text('voice'));
      else this.fallback('', this.text('voice'), message.date);
    } else if (this.s.profile_field || (this.s.draft && this.s.screen === 'field')) this.answer(message, text);
    else if (this.s.draft) this.send(this.text('invalid_choice'));
    else if (message.contact) this.sharedContact(message.contact);
    else {
      const action = Object.entries(this.s.buttons || {}).find(([, label]) => label === text)?.[0];
      if (action) this.action(action);
      else {
        const question = text.replace(/^\/(?:ai|erp)(?:@mtech_customer_support_bot)?(?:\s+|$)/i, '');
        this.fallback(question, '', message.date);
      }
    }
    return this.actions;
  }
  action(action, callbackData = null) {
    const d = this.s.draft;
    if (action === 'back' && this.s.screen === 'profile') d ? this.review() : this.menu();
    else if (action.startsWith('profile:')) {
      const key = action.slice(8);
      if (key === 'open') this.profile();
      else if (key === 'done') d ? this.review() : this.menu();
      else if (this.c.contact_fields.includes(key)) this.profileField(key);
    } else if (this.s.profile_field && ['back', 'cancel'].includes(action)) this.profile();
    else if (['cancel', 'menu:home'].includes(action) && d) this.exitDraft();
    else if (action === 'discard') { delete this.s.draft; this.send(this.text('cancelled')); this.menu(); }
    else if (action === 'resume') this.prompt();
    else if (action === 'back') {
      if (pop(d, 'unknown_field')) this.prompt();
      else if (pop(d, 'editing', false)) { d.step = this.fields().length; this.review(); }
      else if (d.step === 0) this.exitDraft();
      else {
        d.step--;
        while (d.step > 0 && (d.reused_contacts || []).includes(this.fields()[d.step])) d.step--;
        this.prompt();
      }
    } else if (action === 'submit') {
      if (this.fields().includes('time') && !validTime(d.answers.time)) { this.review(); return; }
      let number;
      try { number = this.submit(d, this.summary()); }
      catch (error) { if (!(error instanceof RequestLimit)) throw error; this.send(this.text('request_limit')); return; }
      this.s.last_submission = { number, callback: callbackData }; delete this.s.draft;
      this.send(this.text(d.flow === 'written' ? 'written_saved' : 'saved', { number })); this.menu();
    } else if (action === 'edit') this.edit();
    else if (action === 'review') this.review();
    else if (action.startsWith('edit:')) { d.step = this.fields().indexOf(action.slice(5)); d.editing = true; this.prompt(); }
    else if (action === 'skip') this.accept(null);
    else if (action === 'done') this.accept(d.answers.photos || []);
    else if (action.startsWith('option:')) {
      const key = this.s.profile_field || this.fields()[d.step], field = this.c.fields[key];
      if (field.type === 'time') {
        this.send(this.text('invalid_time')); this.s.profile_field ? this.profileField(key) : this.prompt();
      } else if (field.unknown_prompt) { d.unknown_field = key; this.prompt(); }
      else this.accept(field.options[Number(action.slice(7))]);
    } else if (!d) {
      if (action === 'news:on' || action === 'news:off') {
        this.customer.subscribed = action === 'news:on';
        this.subscribe(this.customer.subscribed);
        this.send(this.text(this.customer.subscribed ? 'news_subscribed' : 'news_unsubscribed'));
        this.menu('news'); return;
      }
      const [kind, key] = action.split(':');
      this[({ menu: 'menu', info: 'info', category: 'category', flow: 'startFlow' })[kind]](key);
    }
  }
  answer(message, text) {
    const d = this.s.draft || {}, key = this.s.profile_field || this.fields()[d.step], field = this.c.fields[key], kind = field.type || 'text';
    if (kind === 'photos') {
      const photos = d.answers[key] || [];
      if (!message.photo?.length) this.send(this.text('photo_required'));
      else if (photos.length >= 3) this.send(this.text('photo_limit'));
      else {
        const p = message.photo.at(-1);
        if (!photos.some(x => x.file_unique_id === p.file_unique_id)) photos.push({ file_id: p.file_id, file_unique_id: p.file_unique_id });
        d.answers[key] = photos; this.prompt();
      }
    } else if (kind === 'phone') {
      const contact = message.contact;
      if (contact) {
        if (contact.user_id !== this.chatId) { this.send(this.text('foreign_contact')); return; }
        text = contact.phone_number || ''; if (!text.startsWith('+')) text = '+' + text;
      }
      const phone = normalizePhone(text); if (phone) this.accept(phone); else this.send(this.text('invalid_phone'));
    } else if (kind === 'time') this.accept(text);
    else if (kind === 'choice') {
      if (field.options.includes(text)) this.accept(text); else this.send(this.text('invalid_choice'));
    } else if (field.unknown_prompt && (field.options || []).includes(text)) { d.unknown_field = key; this.prompt(); }
    else if (!text || Array.from(text).length > (field.max_length || 700) || /[\x00-\x08\x0b-\x1f]/.test(text)) this.send(this.text('invalid_text', { limit: field.max_length || 700 }));
    else this.accept(text);
  }
}
