// Shared by the bot and both Pages authentication gates. No D1 dependency.
const quotaCache = new WeakMap();
export const maintenanceText = 'Тимчасове технічне обслуговування. Нові звернення зараз не приймаються. Спробуйте, будь ласка, пізніше.';

export async function quotaStatus(env) {
  if (env.MAINTENANCE_MODE === 'true') return { blocked: true };
  if (env.QUOTA_GUARD_ENABLED !== 'true') return { blocked: false, enabled: false };
  if (!env.QUOTA_GUARD) return { blocked: true };
  const binding = env.QUOTA_GUARD, now = Date.now();
  let cached = quotaCache.get(binding);
  if (!cached || cached.until <= now) {
    const pending = (async () => {
      try {
        const response = await binding.fetch('https://quota.internal/status', { signal: AbortSignal.timeout(3000) });
        if (!response.ok) throw new Error('Unavailable');
        const status = await response.json();
        if (typeof status.blocked !== 'boolean') throw new Error('Invalid status');
        return { blocked: status.blocked, enabled: true };
      } catch { return { blocked: true, enabled: true }; }
    })();
    cached = { until: now + 15000, pending }; quotaCache.set(binding, cached);
  }
  return cached.pending;
}

export function maintenanceResponse() {
  return new Response(`<!doctype html><html lang="uk"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>MTech · Технічне обслуговування</title><style>body{margin:0;min-height:100dvh;display:grid;place-items:center;background:#f3f5f6;color:#14273a;font:18px/1.6 system-ui}main{max-width:560px;padding:32px}h1{font-size:32px;line-height:1.2}a{color:#2076c2}</style><main><strong>MTech Ukraine</strong><h1>Технічне обслуговування</h1><p>${maintenanceText}</p><a href="mailto:office@mtech.com.ua">office@mtech.com.ua</a></main></html>`, {
    status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store',
      'Retry-After': '300', 'X-Robots-Tag': 'noindex, nofollow', 'X-Content-Type-Options': 'nosniff',
      'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'" }
  });
}
