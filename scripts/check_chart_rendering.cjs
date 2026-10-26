// Exercise the actual SVG animation/resize race without changing business data.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const env = Object.fromEntries(fs.readFileSync(path.join(root, '.env'), 'utf8')
    .split(/\r?\n/).filter(line => line && !line.startsWith('#')).map(line => {
        const i = line.indexOf('='); return [line.slice(0, i), line.slice(i + 1)];
    }));

(async () => {
    const browser = await chromium.launch({ headless: true });
    try {
        const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
        const base = `http://localhost:${env.HTTP_PORT || 8080}`;
        assert.equal((await context.request.post(`${base}/api/method/login`, {
            form: { usr: 'Administrator', pwd: env.ADMIN_PASSWORD },
        })).status(), 200);
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', error => { errors.push(error.stack || error.message); console.error(error.stack || error.message); });
        await page.goto(`${base}/desk/role`);
        await page.waitForFunction(() => window.frappe?.app?.sidebar);
        await page.evaluate(() => new Promise(resolve => frappe.after_ajax(resolve)));
        if (process.env.VECTOR_TEST_LOCAL_ASSETS) {
            await page.addScriptTag({ path: path.join(root,
                'apps/warehouse_core/warehouse_core/public/js/chart_rendering.js') });
        }
        for (const type of ['bar', 'line']) {
            await page.evaluate(type => {
                const host = document.createElement('div');
                host.id = 'chart-rendering-check';
                host.style.cssText = 'position:absolute;inset:100px auto auto 100px;width:700px;height:300px;background:white;z-index:9999';
                document.body.append(host);
                window.testChart = new frappe.Chart(host, {
                    type, height: 280, animate: true, disableEntryAnimation: false,
                    data: { labels: ['A', 'B', 'C'], datasets: [{ name: 'Values', values: [10, 20, 30] }] },
                });
            }, type);
            // Catch entry animation while the canonical SVG is detached.
            await page.waitForFunction(() => testChart.svg.parentNode !== testChart.container);
            await page.evaluate(() => window.dispatchEvent(new Event('resize')));
            await page.waitForTimeout(550);
            async function verify(expected) {
                const state = await page.evaluate(() => ({
                    svgCount: testChart.container.querySelectorAll(':scope > svg.frappe-chart').length,
                    attached: testChart.svg.parentNode === testChart.container,
                    width: Number(testChart.svg.getAttribute('width')),
                    hostWidth: testChart.parent.clientWidth,
                    values: testChart.data.datasets[0].values,
                    units: testChart.svg.querySelectorAll('.bar, .line-graph-path').length,
                    animate: testChart.config.animate,
                }));
                assert.equal(state.svgCount, 1, `${type}: only the current SVG survives animation timers`);
                assert(state.attached, `${type}: chart owns its visible SVG`);
                assert.equal(state.width, state.hostWidth, `${type}: SVG matches resized host`);
                assert.deepEqual(state.values, expected);
                assert(state.units > 0, `${type}: data marks are rendered`);
                assert.equal(state.animate, true, 'animation preference preserved');
            }
            await verify([10, 20, 30]);
            for (const trigger of ['draw', 'resize-observer', 'orientationchange']) {
                await page.evaluate(() => testChart.update({ labels: ['A', 'B', 'C'],
                    datasets: [{ name: 'Values', values: [30, 5, 15] }] }));
                await page.waitForFunction(() => testChart.svg.parentNode !== testChart.container);
                await page.evaluate(trigger => {
                    if (trigger === 'draw') testChart.draw(true);
                    else if (trigger === 'resize-observer') testChart.parent.style.width = '520px';
                    else window.dispatchEvent(new Event(trigger));
                }, trigger);
                await page.waitForTimeout(550);
                await verify([30, 5, 15]);
            }
            await page.evaluate(() => { testChart.destroy(); testChart.parent.remove(); delete window.testChart; });
            console.log(`PASS: ${type} entry/update animation, resize observer, orientation, SVG ownership and values`);
        }
        assert.deepEqual(errors, []);
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
