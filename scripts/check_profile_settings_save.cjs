// Run against the laptop demo. The edited account preference is restored in finally.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const env = Object.fromEntries(fs.readFileSync(path.join(__dirname, '../.env'), 'utf8')
    .split(/\r?\n/).filter(line => line.includes('=')).map(line => {
        const i = line.indexOf('='); return [line.slice(0, i), line.slice(i + 1)];
    }));
const base = `http://localhost:${env.HTTP_PORT || 8080}`;

async function checkCloseIcon(button, headerSelector) {
    assert.equal(await button.innerText(), '');
    assert.match(await button.locator('svg use').getAttribute('href'), /-x$/);
    assert.ok(await button.evaluate((el, selector) => {
        const header = el.closest(selector);
        const right = header.getBoundingClientRect().right - parseFloat(getComputedStyle(header).paddingRight);
        return Math.abs(right - el.getBoundingClientRect().right) <= 8;
    }, headerSelector), 'close icon sits at the right edge of the header');
}

(async () => {
    const browser = await chromium.launch();
    const page = await browser.newPage();
    const open = async () => {
        await page.goto(base + '/desk/operations-overview');
        await page.waitForFunction(() => window.frappe?.vector_animations);
        await page.evaluate(async () => {
            await frappe.require('user_settings_dialog.bundle.js');
            await frappe.ui.show_user_settings('profile');
        });
    };
    const tab = id => page.locator(`.settings-dialog-tab-item[data-tab-id="${id}"]`).click();
    const panel = page.locator('.settings-dialog-panel:visible');
    let initial;
    try {
        assert.equal((await page.request.post(base + '/api/method/login', {
            form: { usr: 'Administrator', pwd: env.ADMIN_PASSWORD },
        })).status(), 200);
        await open();
        const label = await page.evaluate(() => __('Save'));
        const save = panel.getByRole('button', { name: label, exact: true });
        const close = panel.getByRole('button', { name: await page.evaluate(() => __('Close')), exact: true });
        for (const width of [1440, 390]) {
            await page.setViewportSize({ width, height: 844 });
            const middleName = panel.locator('[data-fieldname="middle_name"] input');
            await middleName.fill('Unsaved dialog test');
            const clickOutside = () => page.mouse.click(width / 2, 5);
            await clickOutside();
            assert.equal(await middleName.inputValue(), 'Unsaved dialog test');
            await panel.locator('.change-password-btn').click();
            const nested = page.locator('.modal.show:not(.settings-dialog-wrapper)').last();
            const nestedClose = nested.locator('.btn-modal-close');
            await nestedClose.waitFor();
            await checkCloseIcon(nestedClose, '.modal-header');
            await clickOutside();
            await nestedClose.click();
            await nested.waitFor({ state: 'hidden' });
            assert.equal(await middleName.inputValue(), 'Unsaved dialog test');
            for (const id of ['profile', 'email', 'appearance', 'preferences', 'lists', 'forms', 'reports', 'session-defaults']) {
                await tab(id);
                await close.click({ trial: true });
                await checkCloseIcon(close, '.settings-dialog-panel-header');
                const editable = id !== 'session-defaults' || await page.evaluate(() => Boolean(frappe.boot.session_defaults?.length));
                assert.equal(await save.count(), Number(editable), id);
                if (editable) await save.click({ trial: true });
            }
            await tab('keyboard-shortcuts');
            const actions = panel.locator('.settings-dialog-panel-actions');
            assert.equal(await actions.getByRole('button').count(), 1);
            await checkCloseIcon(close, '.settings-dialog-panel-header');
            await close.click();
            await page.locator('.settings-dialog-wrapper:visible').waitFor({ state: 'hidden' });
            await page.locator('#vector-ai-launcher').click();
            const chatClose = page.locator('.vector-ai-close');
            await checkCloseIcon(chatClose, '.vector-ai-header');
            await chatClose.press('Enter');
            await page.locator('#vector-ai-panel').waitFor({ state: 'hidden' });
            await open();
        }
        await tab('reports');
        const control = panel.locator('[data-fieldname="report_split_view"]');
        initial = await control.getByRole('switch').isChecked();
        // Drop only the native autosave: the explicit button must persist the change itself.
        await page.route('**/api/method/frappe.client.set_value', route => route.fulfill({
            json: { message: {} },
        }), { times: 1 });
        await Promise.all([
            page.waitForResponse(r => r.url().endsWith('/frappe.client.set_value')),
            control.locator('.switch-visual').click(),
        ]);
        const written = page.waitForResponse(r => r.url().endsWith('/frappe.client.set_value'));
        await save.click();
        assert.equal((await written).status(), 200);
        await open();
        await tab('reports');
        assert.equal(await control.getByRole('switch').isChecked(), !initial);
        await tab('appearance');
        const theme = page.waitForResponse(r => r.url().endsWith('.user.switch_theme'));
        await save.click();
        assert.equal((await theme).status(), 200);
        assert.equal(await page.evaluate(() => localStorage.getItem('vector-animations:Administrator')), null);
    } finally {
        try {
            if (initial !== undefined) await page.evaluate(async value => {
                try {
                    await frappe.db.set_value('User', frappe.session.user, { report_split_view: Number(value) });
                } catch (error) {
                    throw new Error(`Could not restore report preference: ${error.status} ${error.responseJSON?.exc_type || ''}`);
                }
            }, initial);
        } finally { await browser.close(); }
    }
    console.log('PASS: Save reachable on desktop and mobile; explicit save survives reload; motion default preserved; report preference restored.');
})().catch(error => { console.error(error); process.exitCode = 1; });
