// Profile fixtures stay in this browser; no User records or preferences are saved.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
(async () => {
    const env = Object.fromEntries(fs.readFileSync(path.join(root, '.env'), 'utf8')
        .split(/\r?\n/).filter(l => l && !l.startsWith('#')).map(l => {
            const i = l.indexOf('='); return [l.slice(0, i), l.slice(i + 1)];
        }));
    const browser = await chromium.launch({ headless: true });
    try {
        const context = await browser.newContext({ viewport: { width: 1920, height: 900 } });
        const base = `http://localhost:${env.HTTP_PORT || 8080}`;
        assert.equal((await context.request.post(base + '/api/method/login', {
            form: { usr: 'Administrator', pwd: env.ADMIN_PASSWORD },
        })).status(), 200);
        if (process.env.VECTOR_TEST_LOCAL_ASSETS) {
            for (const asset of ['js/styled_tooltips.js', 'js/user_navigation.js', 'js/navigation_position.js',
                'js/dock_labels.js', 'js/navigation_scroll.js', 'js/navigation_mobile.js', 'js/navigation_resize.js', 'css/dock_labels.css', 'css/navigation_position.css']) {
                await context.route(`**/assets/warehouse_core/${asset}*`, route => route.fulfill({
                    path: path.join(root, 'apps/warehouse_core/warehouse_core/public', asset),
                    contentType: asset.endsWith('.css') ? 'text/css' : 'application/javascript',
                }));
            }
        }
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', e => errors.push(e.message));
        await page.goto(base + '/desk/home');
        await page.locator('#desk-dock .vector-user-label').waitFor({ state: 'attached' });
        await page.evaluate(() => new Promise(resolve => frappe.after_ajax(resolve)));
        if (!process.env.VECTOR_TEST_LOCAL_ASSETS) {
            assert(await page.evaluate(() => Object.hasOwn(frappe.boot.user, 'middle_name')),
                'boot supplies the middle name for the current user');
        }
        await page.evaluate(() => {
            frappe.vector_navigation_position.set('top');
            frappe.app.sidebar.dock.set_pinned(true);
        });
        const settle = () => page.waitForTimeout(200);
        const label = page.locator('#desk-dock .vector-user-label');
        const button = page.locator('#desk-dock .dock-user-button');
        async function profile(lang, first, middle, last, short, full) {
            await page.evaluate(({ lang, first, middle, last }) => {
                frappe.boot.lang = lang;
                frappe.session.user_fullname = [first, middle, last].filter(Boolean).join(' ');
                Object.assign(frappe.boot.user, { first_name: first, middle_name: middle, last_name: last });
                frappe.app.sidebar.dock.refresh();
            }, { lang, first, middle, last });
            await settle();
            assert.equal(await label.textContent(), short);
            assert.equal(await label.getAttribute('data-full-name'), full);
            assert((await button.getAttribute('aria-label')).endsWith(full));
        }
        await profile('uk', 'Тарас', 'Григорович', 'Шевченко', 'Шевченко Т. Г.', 'Шевченко Тарас Григорович');
        await profile('en', 'Taras', 'Hryhorovych', 'Shevchenko', 'T. H. Shevchenko', 'Taras Hryhorovych Shevchenko');
        await profile('en-GB', 'Jean-Luc', '', 'de la Cruz', 'J.-L. de la Cruz', 'Jean-Luc de la Cruz');
        await profile('uk', 'Анна', '', 'Коваль-Петренко', 'Коваль-Петренко А.', 'Коваль-Петренко Анна');
        await profile('en', 'Administrator', '', '', 'Administrator', 'Administrator');
        await profile('uk', 'Тарас', 'Григорович', 'Шевченко', 'Шевченко Т. Г.', 'Шевченко Тарас Григорович');
        const visibilityByWidth = new Map();
        for (const width of [1920, 1440, 1100, 800, 1100, 1440, 1920]) {
            await page.setViewportSize({ width, height: 900 });
            await settle();
            const visible = await label.isVisible();
            if (visibilityByWidth.has(width)) {
                assert.equal(visible, visibilityByWidth.get(width), 'same width has the same layout in either resize direction');
            }
            visibilityByWidth.set(width, visible);
            if (width === 1920) assert(visible, 'wide top panel displays name');
            if (width === 800) assert(!visible, 'crowded top panel uses avatar only');
            if (visible) {
                const l = await label.boundingBox();
                const a = await button.locator('.avatar').boundingBox();
                assert(l.x + l.width <= a.x, 'name is to the left of avatar');
                assert(Math.abs(l.y + l.height / 2 - a.y - a.height / 2) < 2, 'name beside avatar');
                const fits = await page.locator('#desk-dock .dock-items').evaluate(el => el.scrollWidth <= el.clientWidth + 1);
                assert(fits, 'name does not cause module overflow');
            }
            await page.waitForTimeout(400);
            assert.equal(await label.isVisible(), visible, 'name visibility settles without oscillation');
            console.log(`PASS ${width}px: name ${visible ? 'visible' : 'hidden'}`);
        }
        await button.hover();
        const tip = page.locator('.es-tooltip[data-state="open"]');
        await page.waitForTimeout(650);
        assert.equal(await tip.count(), 0, 'fitting abbreviated name stays quiet');
        await page.mouse.move(700, 300);
        await page.screenshot({ path: path.join(root, '.artifacts/top-user-name.png'), clip: { x: 0, y: 0, width: 1920, height: 120 } });
        await button.click();
        await page.locator('.es-menu[data-state="open"]').first().waitFor();
        await page.keyboard.press('Escape');
        await page.setViewportSize({ width: 390, height: 844 });
        await page.evaluate(() => frappe.app.sidebar.open());
        const sidebarButton = page.locator('.body-sidebar .sidebar-user-button');
        const sidebarLabel = sidebarButton.locator('.vector-user-label');
        assert.equal(await sidebarButton.isVisible(), true, 'workspace mode keeps account access in the left sidebar');
        await page.mouse.click(385, 300);
        await page.waitForFunction(() => !frappe.app.sidebar.sidebar_expanded);
        await page.locator('.vector-workspace-chip:visible').click();
        assert.equal(await button.isVisible(), false, 'workspace picker has no duplicate account control');
        await page.keyboard.press('Escape');
        await page.evaluate(() => frappe.app.sidebar.open());
        assert.equal(await sidebarLabel.textContent(), 'Шевченко Т. Г.');
        await sidebarLabel.hover();
        await page.waitForTimeout(650);
        assert.equal(await tip.count(), 0, 'fitting workspace name stays quiet');
        await page.mouse.move(385, 300);
        await sidebarLabel.evaluate(e => e.style.maxWidth = '40px');
        await sidebarLabel.hover();
        await tip.waitFor();
        assert.equal((await tip.innerText()).trim(), 'Шевченко Тарас Григорович');
        await sidebarButton.click();
        await page.locator('.es-menu[data-state="open"]').first().waitFor();
        await page.keyboard.press('Escape');
        assert.deepEqual(errors, []);
        console.log('PASS: localized initials, compound surnames, missing middle/surname, responsive top panel, full-name tooltips and user menu.');
    } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
