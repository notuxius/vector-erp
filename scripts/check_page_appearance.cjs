// Browser integration check against the running local site; restores appearance afterwards.
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
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const login = await context.request.post(`${base}/api/method/login`, {
        form: { usr: 'Administrator', pwd: env.ADMIN_PASSWORD },
    });
    assert.equal(login.status(), 200);
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.stack || error.message));
    let initial;
    try {
        await page.goto(`${base}/desk/stock`);
        await page.waitForFunction(() => window.frappe?.get_current_page?.()?.vector_appearance);
        initial = await page.evaluate(() => ({
            theme: document.documentElement.getAttribute('data-theme-mode'),
            layout: localStorage.getItem('container_fullwidth'),
        }));
        const trigger = () => page.locator('.vector-appearance-button:visible');
        const menu = () => page.locator('.es-menu[data-state="open"]');
        await trigger().click();
        console.log('MENU', await menu().innerText());
        assert.equal(await menu().getByRole('menuitemradio').count(), 5);
        assert.equal(await menu().getByRole('menuitemradio', { checked: true }).count(), 2);
        await page.screenshot({ path: path.join(root, '.artifacts/page-appearance-desktop.png'), animations: 'disabled' });
        await page.keyboard.press('Escape');
        assert.equal(await trigger().getAttribute('aria-expanded'), 'false');
        await trigger().focus();
        await page.keyboard.press('ArrowDown');
        assert.equal(await trigger().getAttribute('aria-expanded'), 'true');
        await page.keyboard.press('Escape');

        // Every preference is exercised through a visible menu row.
        for (const [label, value] of [['Темна', 'dark'], ['Світла', 'light'], ['Автоматично', 'automatic']]) {
            await trigger().click();
            const before = await page.evaluate(() => document.documentElement.getAttribute('data-theme-mode'));
            const saved = before === value ? null : page.waitForResponse(r => r.url().includes('user.switch_theme') && r.request().method() === 'POST');
            await menu().getByRole('menuitemradio', { name: label, exact: true }).click();
            if (saved) assert.equal((await saved).status(), 200);
            assert.equal(await page.evaluate(() => document.documentElement.getAttribute('data-theme-mode')), value);
        }
        for (const [label, value] of [['Повна ширина', true], ['Компактний', false]]) {
            await trigger().click();
            await menu().getByRole('menuitemradio', { name: label, exact: true }).click();
            assert.equal(await page.evaluate(() => document.body.classList.contains('full-width')), value);
            await trigger().click();
            await menu().getByRole('menuitemradio', { name: label, exact: true }).click();
            assert.equal(await page.evaluate(() => document.body.classList.contains('full-width')), value);
        }

        await page.evaluate(async () => {
            await frappe.require('user_settings_dialog.bundle.js');
            await frappe.ui.show_user_settings('appearance');
        });
        const settings = page.locator('.modal:visible');
        await settings.getByText('Тема оформлення', { exact: true }).waitFor();
        assert.equal(await settings.locator('.theme-card-wrapper.selected').filter({ hasText: 'Автоматично' }).count(), 1);
        assert.equal(await settings.locator('.theme-card-wrapper.selected').filter({ hasText: 'Компактний' }).count(), 1);
        const settingsSaved = page.waitForResponse(r => r.url().includes('user.switch_theme') && r.request().method() === 'POST');
        await settings.locator('.theme-card').filter({ hasText: 'Темна' }).click();
        await settingsSaved;
        await settings.locator('.theme-card').filter({ hasText: 'Повна ширина' }).click();
        await page.keyboard.press('Escape');
        await trigger().click();
        assert.equal(await menu().getByRole('menuitemradio', { name: 'Темна', exact: true }).getAttribute('aria-checked'), 'true');
        assert.equal(await menu().getByRole('menuitemradio', { name: 'Повна ширина', exact: true }).getAttribute('aria-checked'), 'true');
        await page.keyboard.press('Escape');

        // Workspaces, list, form and report; route switches must close menus.
        for (const route of [['Workspaces'], ['Workspaces', 'Stock'], ['List', 'Item'], ['Form', 'System Settings'], ['query-report', 'Stock Balance']]) {
            await page.evaluate(route => frappe.set_route(...route), route);
            await page.waitForFunction(() => window.frappe?.get_current_page?.()?.vector_appearance);
            await trigger().waitFor({ state: 'visible' });
            assert.equal(await trigger().count(), 1, `one header button: ${route}`);
            assert.equal(await menu().count(), 0);
            await trigger().click();
        }
        await page.keyboard.press('Escape');
        await page.reload();
        await trigger().waitFor({ state: 'visible' });
        assert.equal(await page.evaluate(() => document.documentElement.getAttribute('data-theme-mode')), 'dark');
        assert.equal(await page.evaluate(() => document.body.classList.contains('full-width')), true);
        await page.setViewportSize({ width: 390, height: 844 });
        await page.reload();
        await trigger().waitFor({ state: 'visible' });
        await trigger().click();
        const bounds = await menu().boundingBox();
        assert(bounds.x >= 0 && bounds.x + bounds.width <= 391, 'menu fits narrow viewport');
        await page.screenshot({ path: path.join(root, '.artifacts/page-appearance-mobile.png'), animations: 'disabled' });
        await page.keyboard.press('Escape');
        await page.setViewportSize({ width: 1440, height: 1000 });
        await page.goto(`${base}/desk`);
        await trigger().waitFor({ state: 'visible' });
        assert.equal(await trigger().count(), 1, 'All apps header');
        await trigger().click();
        assert.equal(await menu().getByRole('menuitemradio').count(), 5);
        await page.evaluate(() => frappe.set_route('List', 'Item'));
        await page.waitForFunction(() => frappe.get_route()[0] === 'List');
        await page.evaluate(() => frappe.set_route('desktop'));
        await page.waitForFunction(() => frappe.get_route()[0] === 'desktop');
        await trigger().waitFor({ state: 'visible' });
        assert.equal(await trigger().count(), 1, 'All apps revisit has no duplicate button');
        await trigger().click();
        assert.equal(await menu().getByRole('menuitemradio').count(), 5);
        await page.screenshot({ path: path.join(root, '.artifacts/page-appearance-apps.png'), animations: 'disabled' });
        await page.keyboard.press('Escape');
        await page.setViewportSize({ width: 320, height: 720 });
        await trigger().click();
        const appsBounds = await menu().boundingBox();
        assert(appsBounds.x >= 0 && appsBounds.x + appsBounds.width <= 321, 'All apps menu fits 320px');
        assert.deepEqual(errors, []);
        console.log('PASS: page coverage, keyboard, theme/layout choices, Settings sync both ways, reload persistence, narrow layout.');
    } finally {
        if (initial) await page.evaluate(async initial => {
            await frappe.xcall('frappe.core.doctype.user.user.switch_theme', {
                theme: initial.theme[0].toUpperCase() + initial.theme.slice(1),
            });
            document.documentElement.setAttribute('data-theme-mode', initial.theme);
            if (initial.layout === null) localStorage.removeItem('container_fullwidth');
            else localStorage.setItem('container_fullwidth', initial.layout);
            frappe.ui.toolbar.set_fullwidth_if_enabled();
        }, initial);
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
