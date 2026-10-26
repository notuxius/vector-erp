// Exercise real popup lifecycle and shell visibility without saving records.
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
        const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
        assert.equal((await context.request.post(`${base}/api/method/login`, {
            form: { usr: 'Administrator', pwd: env.ADMIN_PASSWORD },
        })).status(), 200);
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.route('**/api/method/erpnext.selling.page.point_of_sale.point_of_sale.check_opening_entry',
            route => route.fulfill({ json: { message: [] } }));
        await page.goto(`${base}/desk/selling/point-of-sale`);
        await page.locator('.vector-pos-back').waitFor();
        await page.waitForFunction(() => cur_dialog?.display);
        const shellVisible = async () => {
            assert(await page.locator('.body-sidebar').isVisible(), 'inner sidebar visible');
            assert(await page.locator('#desk-dock').isVisible(), 'module dock visible');
            assert(await page.locator('.page-head:visible').isVisible(), 'page header visible');
        };
        await shellVisible();
        assert.equal(await page.evaluate(() => frappe.app.sidebar.current_page().hide_sidebar), true,
            'POS page configuration preserved');
        await page.evaluate(() => frappe.app.sidebar.open());
        await shellVisible();
        assert.equal(await page.evaluate(() => frappe.app.sidebar.sidebar_expanded), true);
        await page.locator('.modal.show .modal-title').click();
        await page.screenshot({ path: path.join(root, '.artifacts/pos-popup-navigation.png') });
        // A nested validation dialog must not hide the shell when dismissed.
        await page.evaluate(() => frappe.msgprint('Dialog navigation check'));
        await page.waitForFunction(() => cur_dialog?.display && !cur_dialog.vectorPosNavigation);
        await shellVisible();
        await page.evaluate(() => cur_dialog.hide());
        await page.waitForFunction(() => document.querySelectorAll('.modal.show').length === 1);
        await shellVisible();
        console.log('PASS POS direct link and nested popup navigation');

        for (const width of [1024, 768, 390, 1440]) {
            await page.setViewportSize({ width, height: 700 });
            await page.evaluate(() => new Promise(requestAnimationFrame));
            if (width >= 768) await shellVisible();
            else assert.equal(await page.evaluate(() => frappe.app.sidebar.sidebar_expanded), false,
                'mobile drawer stays closed behind popup');
            for (const selector of ['.vector-pos-back', '.btn-modal-close', '.btn-modal-primary']) {
                const button = page.locator('.modal.show').locator(selector);
                const r = await button.boundingBox();
                assert(r && r.x >= 0 && r.x + r.width <= width, `${selector} reachable at ${width}`);
            }
        }
        // Simulate successful closure without submitting an opening entry.
        await page.evaluate(() => cur_dialog.hide());
        await page.waitForFunction(() => !document.querySelector('.modal-backdrop'));
        assert.equal(await page.locator('.body-sidebar').isVisible(), false, 'POS layout restored');
        console.log('PASS responsive controls and restoration after last popup closes');

        await page.evaluate(() => frappe.set_route('stock'));
        await page.waitForFunction(() => frappe.get_route()[0] === 'Workspaces');
        const preference = await page.evaluate(() => frappe.app.sidebar.sidebar_expanded);
        // An arbitrary page that hides both shells must follow the same policy.
        await page.evaluate(() => {
            const current = frappe.app.sidebar.current_page();
            window.dialogCheckFlags = [current.hide_sidebar, current.hide_dock];
            current.hide_sidebar = current.hide_dock = true;
            frappe.app.sidebar.refresh();
            window.dialogCheck = new frappe.ui.Dialog({ title: 'Shared popup navigation', minimizable: true });
            dialogCheck.show();
        });
        await page.waitForFunction(() => dialogCheck.display);
        await shellVisible();
        await page.evaluate(() => dialogCheck.toggle_minimize());
        assert.equal(await page.locator('.body-sidebar').isVisible(), false);
        assert.equal(await page.locator('#desk-dock').isVisible(), false);
        await page.evaluate(() => dialogCheck.toggle_minimize());
        await shellVisible();
        await page.evaluate(() => dialogCheck.hide());
        await page.waitForFunction(() => !document.querySelector('.modal-backdrop'));
        assert.equal(await page.locator('.body-sidebar').isVisible(), false);
        assert.equal(await page.locator('#desk-dock').isVisible(), false);
        await page.evaluate(() => {
            const current = frappe.app.sidebar.current_page();
            [current.hide_sidebar, current.hide_dock] = dialogCheckFlags;
            frappe.app.sidebar.refresh();
        });
        assert.equal(await page.evaluate(() => frappe.app.sidebar.sidebar_expanded), preference,
            'expanded/collapsed preference unchanged');
        await page.evaluate(async () => {
            await frappe.require('user_settings_dialog.bundle.js');
            await frappe.ui.show_user_settings('profile');
        });
        await page.locator('.settings-dialog-panel-body:visible').waitFor();
        await shellVisible();
        await page.keyboard.press('Escape');
        await page.waitForFunction(() => !document.querySelector('.modal-backdrop'));
        await shellVisible();
        assert.deepEqual(errors, []);
        console.log('PASS shared hidden-page policy, minimize/restore, settings, saved sidebar state, no browser errors');
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
