// Real Desk hover/focus checks; UI fixtures are removed with the browser context.
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
        const context = await browser.newContext({ viewport: { width: 1200, height: 900 } });
        if (process.env.VECTOR_TEST_LOCAL_ASSETS) {
            for (const asset of ['styled_tooltips', 'user_navigation', 'dock_labels', 'navigation_breadcrumbs']) {
                await context.route(`**/assets/warehouse_core/js/${asset}.js*`, route => route.fulfill({
                    path: path.join(root, `apps/warehouse_core/warehouse_core/public/js/${asset}.js`),
                    contentType: 'application/javascript',
                }));
            }
        }
        assert.equal((await context.request.post(`${base}/api/method/login`, {
            form: { usr: 'Administrator', pwd: env.ADMIN_PASSWORD },
        })).status(), 200);
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', e => errors.push(e.message));
        const bubble = () => page.locator('.es-tooltip[data-state="open"]');
        async function hover(trigger, expected) {
            await page.mouse.move(0, 0);
            await trigger.hover();
            await bubble().waitFor();
            assert.equal(await bubble().count(), 1);
            assert.equal((await bubble().textContent()).trim(), expected);
            assert.equal(await trigger.getAttribute('title'), null);
        }
        async function quiet(trigger) {
            await page.mouse.move(0, 0);
            await trigger.hover();
            await page.waitForTimeout(650);
            assert.equal(await bubble().count(), 0, 'fitting text stays tooltip-free');
        }
        async function checkText(trigger, label = trigger) {
            const clipped = await label.evaluate(e => e.scrollWidth > e.clientWidth + 1);
            if (clipped) await hover(trigger, await trigger.getAttribute('data-vector-tooltip'));
            else await quiet(trigger);
        }
        for (const route of ['/desk/stock', '/desk/email-account', '/desk/system-settings', '/apps']) {
            await page.goto(base + route);
            await page.waitForFunction(() => !!(window.frappe?.vector_tooltip && frappe.app?.sidebar));
            await page.locator('.vector-top-actions .sidebar-notification .item-anchor').waitFor();
            await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
            assert.equal(await page.locator('body [title]:not([title=""])').count(), 0, route);
            const crumb = page.locator('.page-head:visible .es-breadcrumbs__item').last();
            if (await crumb.isVisible()) {
                await checkText(crumb, crumb.locator('.es-breadcrumbs__label'));
            }
            for (const selector of ['.navbar-modal-search-mobile', '.sidebar-notification']) {
                const action = page.locator(`.vector-top-actions ${selector} .item-anchor`);
                await page.mouse.move(0, 0);
                await action.hover();
                await page.waitForTimeout(600);
                assert.equal(await bubble().count(), 0, `${selector} remains tooltip-free`);
                assert.ok(await action.getAttribute('aria-label'));
            }
            if (route === '/desk/stock') {
                const link = page.locator('.link-item[data-vector-tooltip]:visible').first();
                await quiet(link);
            }
            if (route === '/desk/email-account') {
                const order = page.locator('.btn-order:visible');
                await hover(order, await order.getAttribute('data-vector-tooltip'));
                await order.click();
                await hover(order, await order.getAttribute('data-vector-tooltip'));
            }
            if (route === '/desk/system-settings') {
                const assign = page.locator('.add-assignment-btn:visible');
                await hover(assign, await assign.getAttribute('data-vector-tooltip'));
            }
            console.log(`PASS: styled tooltips and no browser titles on ${route}`);
        }
        await page.goto(base + '/desk/email-account');
        await page.locator('.page-head:visible .es-breadcrumbs__item').last().waitFor();
        await page.setViewportSize({ width: 800, height: 900 });
        const crumb = page.locator('.page-head:visible .es-breadcrumbs__item').last();
        const label = crumb.locator('.es-breadcrumbs__label');
        await label.evaluate(e => e.style.maxWidth = '40px');
        await hover(crumb, await crumb.getAttribute('data-vector-tooltip'));
        await page.screenshot({ path: path.join(root, '.artifacts/styled-breadcrumb-tooltip.png') });
        await label.evaluate(e => e.style.maxWidth = 'none');
        await page.setViewportSize({ width: 1600, height: 900 });
        await bubble().waitFor({ state: 'hidden' });
        await quiet(crumb);

        await page.setViewportSize({ width: 1440, height: 900 });
        await page.evaluate(() => {
            frappe.app.sidebar.dock.set_pinned(true);
            frappe.app.sidebar.dock.open();
        });
        assert.equal(await page.locator('.vector-dock-label-toggle').count(), 0);
        const compact = await page.addStyleTag({ content: ':root { --vector-sidebar-width: 56px !important; }' });
        const link = page.locator('.body-sidebar .body-sidebar-top .item-anchor[data-vector-tooltip]').first();
        await link.waitFor();
        await hover(link, await link.getAttribute('aria-label'));
        await compact.evaluate(e => e.remove());
        await page.setViewportSize({ width: 800, height: 900 });
        const arrow = page.locator('#desk-dock .vector-navigation-scroll-arrow:visible').first();
        await hover(arrow, await arrow.getAttribute('aria-label'));
        console.log('PASS: dock labels/pinning, compact sidebar links, and navigation scroll arrows.');

        await page.evaluate(() => {
            const fixture = document.createElement('div');
            fixture.id = 'tooltip-fixture';
            fixture.style = 'position:fixed;top:180px;left:20px;z-index:2000;background:white;padding:20px';
            fixture.innerHTML = '<button id="tooltip-start">Start</button><button id="tooltip-dynamic" title="Dynamic hint"><svg width="20" height="20" aria-hidden="true"></svg></button><button id="tooltip-existing" title="Legacy hint">Existing</button><a id="tooltip-row" tabindex="0" title="A long nested row label"><span style="display:block;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;width:40px">A long nested row label</span></a>';
            document.body.append(fixture);
            frappe.ui.tooltip($('#tooltip-existing'), { text: 'Existing styled hint' });
        });
        const dynamic = page.locator('#tooltip-dynamic');
        await hover(dynamic, 'Dynamic hint');
        await dynamic.evaluate(e => e.title = 'Updated hint <plain text>');
        await page.waitForFunction(() => document.querySelector('.es-tooltip[data-state=open]')?.textContent === 'Updated hint <plain text>');
        await page.locator('#tooltip-start').focus();
        await page.keyboard.press('Tab');
        await bubble().waitFor();
        assert.equal((await bubble().textContent()).trim(), 'Updated hint <plain text>');
        await page.keyboard.press('Escape');
        await bubble().waitFor({ state: 'hidden' });
        await quiet(page.locator('#tooltip-existing'));
        await page.locator('#tooltip-existing').evaluate(e => e.style = 'width:35px;overflow:hidden;white-space:nowrap');
        await hover(page.locator('#tooltip-existing'), 'Existing styled hint');
        const row = page.locator('#tooltip-row');
        await hover(row, 'A long nested row label');
        await row.locator('span').evaluate(e => e.style.width = '300px');
        await bubble().waitFor({ state: 'hidden' });
        await quiet(row);
        await row.locator('span').evaluate(e => e.style.width = '40px');
        await page.locator('#tooltip-existing').focus();
        await page.keyboard.press('Tab');
        await bubble().waitFor();
        assert.equal((await bubble().textContent()).trim(), 'A long nested row label');
        await page.keyboard.press('Escape');
        await bubble().waitFor({ state: 'hidden' });
        await hover(dynamic, 'Updated hint <plain text>');
        await dynamic.evaluate(e => e.remove());
        await bubble().waitFor({ state: 'hidden' });
        await page.locator('#tooltip-fixture').evaluate(e => e.remove());

        // About's copy handler used to read title; verify its value survives conversion.
        await page.evaluate(() => frappe.ui.toolbar.show_about());
        const app = page.locator('.about-app-row:visible').first();
        await app.waitFor();
        const version = await app.getAttribute('data-vector-tooltip');
        assert.ok(version);
        await page.evaluate(() => { frappe.utils.copy_to_clipboard = value => { window.copiedVersion = value; }; });
        await app.click();
        assert.equal(await page.evaluate(() => window.copiedVersion), version);
        assert.deepEqual(errors, []);
        console.log('PASS: dynamic titles, keyboard focus/Escape, plain text, existing styled tips, removal cleanup, and About copy action.');
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exit(1); });
