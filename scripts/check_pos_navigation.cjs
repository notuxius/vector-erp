// Exercise POS cancellation against the running site without creating business records.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const env = Object.fromEntries(fs.readFileSync(path.join(root, '.env'), 'utf8')
    .split(/\r?\n/).filter(line => line && !line.startsWith('#')).map(line => {
        const i = line.indexOf('='); return [line.slice(0, i), line.slice(i + 1)];
    }));
const base = `http://localhost:${env.HTTP_PORT || 8080}`;

(async () => {
    const browser = await chromium.launch({ headless: true });
    try {
        const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
        assert.equal((await context.request.post(`${base}/api/method/login`, {
            form: { usr: 'Administrator', pwd: env.ADMIN_PASSWORD },
        })).status(), 200);
        const page = await context.newPage();
        const errors = [];
        const writes = [];
        page.on('pageerror', error => { errors.push(error.message); console.log('Browser error:', error.stack); });
        page.on('request', request => {
            if (request.url().includes('create_opening_voucher')) writes.push(request.url());
        });
        // Keep this check usable even when the signed-in user has an open shift.
        await page.route('**/api/method/erpnext.selling.page.point_of_sale.point_of_sale.check_opening_entry',
            route => route.fulfill({ json: { message: [] } }));
        const ready = async () => {
            await page.waitForFunction(() => window.frappe?.router && frappe.boot);
        };
        const popup = () => page.locator('.modal:visible').filter({ has: page.locator('.vector-pos-back') });
        const enter = async () => {
            await page.evaluate(() => frappe.set_route('point-of-sale'));
            await popup().waitFor();
            await page.waitForFunction(() => window.cur_dialog?.display && cur_dialog.vectorPosNavigation);
            assert.equal(await popup().count(), 1);
        };
        const gone = async (route) => {
            await page.waitForFunction(route => JSON.stringify(frappe.get_route()) === JSON.stringify(route), route);
            await page.waitForFunction(() => !document.querySelector('.modal-backdrop'));
            assert.equal(await page.locator('.modal:visible').count(), 0);
        };

        await page.goto(`${base}/desk/stock`);
        await ready();
        await page.waitForFunction(() => frappe.get_route()?.[0] === 'Workspaces');
        const origin = await page.evaluate(() => frappe.get_route());
        await enter();
        // A failed required-field check must not disable cancellation.
        await popup().locator('.btn-modal-primary').click();
        await page.waitForFunction(() => window.cur_dialog?.display && !cur_dialog.vectorPosNavigation);
        await page.locator('.modal:visible').filter({ hasNot: page.locator('.vector-pos-back') })
            .locator('.btn-modal-close').click();
        await page.waitForFunction(() => window.cur_dialog?.vectorPosNavigation);
        await popup().locator('.vector-pos-back').click();
        await gone(origin);
        console.log('PASS Back after failed validation');

        await enter();
        await popup().locator('.btn-modal-close').click();
        await gone(origin);
        await enter();
        // Company autofocus can open its link suggestions asynchronously. Escape
        // belongs to that dropdown first; test dialog cancellation without it.
        await popup().locator('.modal-title').click();
        await popup().locator('.vector-pos-back').focus();
        await page.keyboard.press('Escape');
        await gone(origin);
        console.log('PASS Close, Escape, and repeated POS visits');

        await enter();
        await page.goBack();
        await gone(origin);
        await page.goForward();
        await popup().waitFor();
        await popup().locator('.vector-pos-back').click();
        await gone(origin);
        console.log('PASS browser Back and Forward');

        await page.goto(`${base}/desk/point-of-sale`);
        await ready();
        await popup().waitFor();
        await page.setViewportSize({ width: 390, height: 844 });
        const bounds = await popup().locator('.vector-pos-back').boundingBox();
        assert(bounds && bounds.x >= 0 && bounds.x + bounds.width <= 390);
        await page.screenshot({ path: path.join(root, '.artifacts/pos-navigation-mobile.png') });
        await popup().locator('.vector-pos-back').click();
        // Desk now resolves its empty route to the configured Vector homepage.
        await gone(['operations-overview']);
        assert.equal(new URL(page.url()).pathname, '/desk/operations-overview');
        console.log('PASS direct-link fallback and mobile Back');

        // Unrelated mandatory dialogs must retain their own close policy.
        await page.evaluate(() => {
            const dialog = new frappe.ui.Dialog({ title: 'Navigation regression check', static: true });
            dialog.show();
        });
        await page.waitForFunction(() => window.cur_dialog?.display);
        assert.equal(await page.locator('.modal:visible .vector-pos-back').count(), 0);
        assert.equal(await page.locator('.modal:visible .btn-modal-close').isVisible(), false);
        await page.evaluate(() => cur_dialog.hide());

        assert.deepEqual(writes, [], 'cancellation never creates an opening voucher');
        assert.deepEqual(errors, [], 'no browser errors');
        console.log('PASS no opening-entry writes or browser errors; other dialogs unchanged');
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
