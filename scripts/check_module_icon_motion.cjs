// Pixel checks catch SVG <use> animations that document.getAnimations() can miss.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const env = Object.fromEntries(fs.readFileSync(path.join(root, '.env'), 'utf8')
    .split(/\r?\n/).filter(line => line && !line.startsWith('#')).map(line => {
        const i = line.indexOf('='); return [line.slice(0, i), line.slice(i + 1)];
    }));
const base = `http://127.0.0.1:${env.HTTP_PORT || 8080}`;
(async () => {
    const browser = await chromium.launch({ headless: true });
    try {
        const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
        assert.equal((await context.request.post(`${base}/api/method/login`, {
            form: { usr: 'Administrator', pwd: env.ADMIN_PASSWORD },
        })).status(), 200);
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        if (process.env.VECTOR_TEST_LOCAL_ASSETS) await page.route('**/assets/warehouse_core/css/interface_preferences.css*', route =>
            route.fulfill({ path: path.join(root, 'apps/warehouse_core/warehouse_core/public/css/interface_preferences.css'), contentType: 'text/css' }));
        for (const [route, symbol, animated] of [
            ['/desk/stock/item', '#icon-gantt-chart-duotone', true],
            ['/desk/system-settings', '#icon-system-monitor-soft', false],
        ]) {
            await page.goto(base + route);
            await page.waitForFunction(() => window.frappe?.app?.sidebar?.dock?.enabled);
            await page.evaluate(() => new Promise(resolve => frappe.after_ajax(resolve)));
            await page.evaluate(() => frappe.vector_animations.set(false));
            const buttons = page.locator('#desk-dock .dock-item');
            const index = await buttons.evaluateAll((elements, symbol) => elements.findIndex(el => {
                const use = el.querySelector('use');
                return (use?.getAttribute('href') || use?.getAttribute('xlink:href')) === symbol;
            }), symbol);
            assert(index >= 0, `live dock contains ${symbol}: ${await buttons.evaluateAll(elements => elements.map(el => el.querySelector('use')?.outerHTML).join(' '))}`);
            const button = buttons.nth(index);
            const icon = button.locator('.dock-item-icon');
            await page.evaluate(() => frappe.app.sidebar.dock.open());
            await button.hover();
            const controls = () => icon.locator('svg').evaluate(el => ({
                motion: getComputedStyle(el).getPropertyValue('--icon-motion').trim(),
                opacity: getComputedStyle(el).getPropertyValue('--icon-motion-opacity').trim(),
            }));
            assert.deepEqual(await controls(), { motion: 'paused', opacity: '0' });
            // Sample actual pixels at different points in the icon's animation cycle.
            const samples = async () => {
                const values = [];
                for (const ms of [250, 350, 500]) {
                    await page.waitForTimeout(ms);
                    values.push(await icon.screenshot({ animations: 'allow' }));
                }
                return values;
            };
            const stopped = await samples();
            assert(stopped.every(frame => frame.equals(stopped[0])), `${symbol} stays visually still with animations off`);
            await page.evaluate(() => frappe.vector_animations.set(true));
            assert.deepEqual(await controls(), { motion: 'running', opacity: '1' });
            const moving = await samples();
            assert.equal(moving.some(frame => !frame.equals(moving[0])), animated, `${symbol} retains its native motion when enabled`);
            // Turn it off during hover, then exercise keyboard focus as well.
            await page.evaluate(() => frappe.vector_animations.set(false));
            const stoppedAgain = await samples();
            assert(stoppedAgain.every(frame => frame.equals(stoppedAgain[0])), `${symbol} stops immediately after opting out`);
            await page.mouse.move(1599, 999);
            await button.focus();
            await page.keyboard.press('Tab');
            await page.keyboard.press('Shift+Tab');
            assert.deepEqual(await controls(), { motion: 'paused', opacity: '0' });
            console.log(`PASS: ${symbol} respects the animation preference.`);
        }
        assert.deepEqual(errors, []);
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
