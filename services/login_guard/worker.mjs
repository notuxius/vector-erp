// Private service binding only. Each pseudonymous client has one globally
// coordinated Durable Object, independent of the Pages isolate or location.
export function consume(state, now) {
  if (!state || now >= state.hourUntil) state = {hourUntil: now + 3600000, hour: 0, minuteUntil: now + 60000, minute: 0};
  if (now >= state.minuteUntil) { state.minuteUntil = now + 60000; state.minute = 0; }
  const retryAfter = Math.max(state.hour >= 50 ? state.hourUntil - now : 0, state.minute >= 10 ? state.minuteUntil - now : 0);
  if (retryAfter > 0) return {state, allowed: false, retryAfter: Math.ceil(retryAfter / 1000)};
  state.hour++; state.minute++;
  return {state, allowed: true, retryAfter: 0};
}

export class LoginAttempts {
  constructor(ctx) { this.ctx = ctx; }
  async fetch() {
    const result = await this.ctx.storage.transaction(async storage => {
      const result = consume(await storage.get('attempts'), Date.now());
      await storage.put('attempts', result.state);
      await storage.setAlarm(result.state.hourUntil);
      return result;
    });
    return Response.json({allowed: result.allowed, retryAfter: result.retryAfter});
  }
  async alarm() {
    // A delayed alarm must not erase a window opened by a newer request.
    const state = await this.ctx.storage.get('attempts');
    if (!state || state.hourUntil <= Date.now()) await this.ctx.storage.deleteAll();
    else await this.ctx.storage.setAlarm(state.hourUntil);
  }
}

export default {
  async fetch(request, env) {
    if (request.method !== 'POST' || new URL(request.url).pathname !== '/attempt') return new Response('Not found', {status: 404});
    const key = request.headers.get('X-Client-Key');
    if (!/^[a-f0-9]{64}$/.test(key || '')) return new Response('Invalid client', {status: 400});
    return env.ATTEMPTS.get(env.ATTEMPTS.idFromName(key)).fetch('https://attempt.internal/');
  }
};
