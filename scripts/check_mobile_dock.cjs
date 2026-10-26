// Integration regression: mobile overlay drawer and attached desktop navigation.
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
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true });
    await context.addInitScript(() => localStorage.setItem('vector-navigation-position:Administrator', 'left'));
    let page = await context.newPage();
    const errors = [];
    async function prepare(page) {
        page.on('pageerror', error => errors.push(error.stack || error.message));
        // Exercise source changes before rebuilding the local deployment.
        if (process.env.VECTOR_TEST_LOCAL_ASSETS) {
            for (const asset of ['js/navigation_mobile.js', 'js/navigation_resize.js', 'css/dock_labels.css']) {
                await page.route(`**/assets/warehouse_core/${asset}*`, route => route.fulfill({
                    path: path.join(root, 'apps/warehouse_core/warehouse_core/public', asset),
                    contentType: asset.endsWith('.js') ? 'application/javascript' : 'text/css',
                }));
            }
        }
    }
    await prepare(page);
    let initial;
    async function ready() {
        await page.waitForFunction(() => window.frappe?.app?.sidebar?.dock?.enabled);
        await page.evaluate(() => new Promise(resolve => frappe.after_ajax(resolve)));
        await page.evaluate(() => frappe.vector_navigation_position.set('left'));
    }
    async function opened(value) {
        await page.waitForFunction(value => frappe.app.sidebar.dock.is_open === value, value);
        assert.equal(await page.locator('#desk-dock').getAttribute('aria-hidden'), String(!value));
        assert.equal(await page.locator('#desk-dock').evaluate(el => el.inert), !value);
    }
    let touchSession;
    async function swipe(from, to) {
        const session = touchSession ||= await context.newCDPSession(page);
        await session.send('Input.dispatchTouchEvent', {
            type: 'touchStart', touchPoints: [{ x: from[0], y: from[1] }],
        });
        for (let step = 1; step <= 6; step++) {
            await session.send('Input.dispatchTouchEvent', {
                type: 'touchMove', touchPoints: [{
                    x: from[0] + (to[0] - from[0]) * step / 6,
                    y: from[1] + (to[1] - from[1]) * step / 6,
                }],
            });
        }
        await session.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
        await page.waitForTimeout(450); // let gesture click suppression and motion finish
    }
    try {
        const login = await context.request.post(`${base}/api/method/login`, {
            form: { usr: 'Administrator', pwd: env.ADMIN_PASSWORD },
        });
        assert.equal(login.status(), 200);
        await page.goto(`${base}/desk/stock/item`);
        await ready();
        initial = await page.evaluate(() => frappe.boot.desk_settings.dock_mode);
        for (const mode of ['Floating', 'Pinned']) {
            await page.goto(`${base}/desk/stock/item`);
            await ready();
            await page.evaluate(mode => frappe.db.set_value('User', frappe.session.user, 'dock_mode', mode), mode);
            await page.reload();
            await ready();
            for (const width of [320, 390, 767]) {
                await page.setViewportSize({ width, height: 844 });
                await page.reload();
                await ready();
                const opener = page.locator('.vector-workspace-chip:visible');
                await opener.waitFor({ timeout: 5000 });
                assert.equal(await opener.count(), 1);
                assert.equal(await opener.innerText(), '', 'mobile chip is icon-only');
                assert(await opener.getAttribute('aria-label'), 'icon has an accessible label');
                const tabBox = await opener.boundingBox();
                assert(tabBox.width >= 44 && tabBox.height >= 44, 'touch target is at least 44px');
                await opened(false);
                await opener.tap();
                await opened(true);
                await page.waitForFunction(() => document.querySelector('#desk-dock').getBoundingClientRect().x >= 0);
                const box = await page.locator('#desk-dock').boundingBox();
                assert(box && box.x >= 0 && box.x + box.width <= width && box.y >= 0
                    && box.y + box.height <= 845, 'mobile dock fits viewport');
                assert.equal(box.x, 0, 'drawer is flush with the left edge');
                assert(box.y >= tabBox.y + tabBox.height, 'drawer leaves the title row visible');
                const title = page.locator('.page-head:visible .vector-navigation-breadcrumbs li:last-child');
                const titleBox = await title.boundingBox();
                assert(titleBox.width > 0 && titleBox.y < box.y, 'current page title stays visible');
                assert.equal(await page.locator('#desk-dock').getAttribute('aria-modal'), 'true');
                assert.equal(await page.locator('.vector-mobile-menu-scrim:visible').count(), 1);
                const row = await page.locator('#desk-dock .dock-items > .dock-item').first().boundingBox();
                assert(row.width > 180 && row.height >= 44, 'drawer uses full-width touch rows');
                assert.equal(await page.locator('.vector-dock-mode-toggle:visible').count(), 0);
                assert.equal(await opener.getAttribute('aria-pressed'), 'true');
                assert.equal(await opener.getAttribute('aria-expanded'), 'true');
                await opener.tap();
                await opened(false);
                await opener.tap();
                await opened(true);
                await page.touchscreen.tap(width - 5, 80);
                await opened(false);
                await opener.focus();
                await page.keyboard.press('Enter');
                await opened(true);
                await page.keyboard.press('Escape');
                await opened(false);
                assert.equal(await opener.evaluate(el => document.activeElement === el), true, 'Escape returns focus');
                await opener.tap();
                await opened(true);
                await page.locator('.vector-mobile-menu-close:visible').tap();
                await opened(false);
                await swipe([4, 115], [145, 120]);
                await opened(true);
                await swipe([145, 250], [16, 255]);
                await opened(false);
                await opener.tap();
                await opened(true);
                await page.locator('.vector-mobile-menu-close:visible').tap();
                await opened(false);
                await swipe([4, 115], [8, 230]);
                await opened(false); // Vertical scrolling must not open navigation.
                await swipe([120, 250], [245, 255]);
                await opened(false); // Swiping page content must not open navigation.
                console.log(`PASS: mobile overlay and gestures at ${width}px (${mode})`);
            }
            await page.setViewportSize({ width: 390, height: 844 });
            await page.reload();
            await ready();
            await page.locator('.vector-workspace-chip:visible').tap();
            await opened(true);
            const previous = await page.evaluate(() => frappe.app.sidebar.current_module);
            await page.locator('#desk-dock .dock-items > .dock-item:not(.active)').first().tap();
            await page.waitForFunction(previous => frappe.app.sidebar.current_module !== previous, previous);
            await page.evaluate(() => new Promise(resolve => frappe.after_ajax(resolve)));
            await opened(false);
            await page.locator('.vector-workspace-chip:visible').waitFor({ state: 'visible' });

            // Returning to desktop applies the preference without a reload or a server write.
            for (const width of [768, 1440]) {
                await page.setViewportSize({ width, height: 1000 });
                await page.waitForFunction(() => frappe.app.sidebar.dock.is_pinned);
                assert.equal(await page.locator('.vector-workspace-chip:visible').count(), 0);
                await opened(true);
            }
            await page.setViewportSize({ width: 390, height: 844 });
            await opened(false);
            assert.equal(await page.evaluate(() => frappe.boot.desk_settings.dock_mode), mode);
            console.log(`PASS: ${mode}, overlay/swipes/title, touch/keyboard, module switching, 320/390/767px, desktop restoration`);
        }
        // Short landscape viewport, saved wide dock, and scroll reachability.
        await page.setViewportSize({ width: 320, height: 568 });
        await page.evaluate(() => document.documentElement.style.setProperty('--vector-dock-width', '240px'));
        await page.locator('.vector-workspace-chip:visible').tap();
        await opened(true);
        await page.waitForTimeout(350);
        const mobileBox = await page.locator('#desk-dock').boundingBox();
        assert(mobileBox.x + mobileBox.width <= 320 && mobileBox.y + mobileBox.height <= 568);
        await page.locator('#desk-dock .dock-items > .dock-item').last().scrollIntoViewIfNeeded();
        await page.screenshot({ path: path.join(root, '.artifacts/mobile-dock.png'), animations: 'disabled' });
        await page.goto(`${base}/desk/desktop`);
        await page.waitForFunction(() => window.frappe?.app?.sidebar?.current_page?.()?.hide_dock);
        assert.equal(await page.locator('.vector-workspace-chip:visible').count(), 0, 'no opener on All apps');

        const desktop = await browser.newContext({ viewport: { width: 1440, height: 1000 },
            storageState: await context.storageState() });
        page = await desktop.newPage();
        await prepare(page);
        await page.goto(`${base}/desk/stock/item`);
        await ready();
        await page.evaluate(() => frappe.db.set_value('User', frappe.session.user, 'dock_mode', 'Floating'));
        await page.reload();
        await ready();
        assert.equal(await page.evaluate(() => frappe.app.sidebar.dock.is_pinned), true);
        assert.equal(await page.locator('.vector-workspace-chip:visible').count(), 0);
        assert.equal(await page.locator('.vector-dock-label-toggle').count(), 0);
        await opened(true);
        // Cached and new routes keep attached navigation with no desktop opener.
        for (const route of ['/desk/stock/item', '/desk/stock/item/CNC-001', '/desk/stock/item']) {
            await page.evaluate(route => { frappe.set_route(route); }, route);
            await page.waitForFunction(route => decodeURIComponent(location.pathname) === route, route);
            await ready();
            assert.equal(await page.locator('.vector-workspace-chip:visible').count(), 0);
            assert.equal(await page.evaluate(() => frappe.app.sidebar.dock.is_pinned), true);
            await page.keyboard.press('Escape');
            await opened(true);
        }
        await page.setViewportSize({ width: 600, height: 844 });
        await page.evaluate(() => frappe.app.sidebar.close());
        const sidebarToggle = page.locator('.sidebar-toggle-btn:visible');
        await page.mouse.move(590, 800);
        await page.evaluate(() => document.activeElement?.blur());
        assert.equal(await sidebarToggle.locator('.sidebar-toggle-icon').isVisible(), true, 'directional icon visible at rest');
        assert.equal(await sidebarToggle.locator('.sidebar-toggle-placeholder').isVisible(), false, 'hamburger hidden at rest');
        await sidebarToggle.hover();
        assert.equal(await sidebarToggle.locator('.sidebar-toggle-placeholder').isVisible(), false, 'hamburger hidden on hover');
        assert.equal(await sidebarToggle.locator('.sidebar-toggle-icon use').getAttribute('href'), '#icon-panel-left-open');
        assert.equal(await sidebarToggle.getAttribute('aria-expanded'), 'false');
        await sidebarToggle.click();
        await page.waitForFunction(() => frappe.app.sidebar.sidebar_expanded);
        assert.equal(await sidebarToggle.locator('.sidebar-toggle-icon use').getAttribute('href'), '#icon-panel-left-close');
        assert.equal(await sidebarToggle.getAttribute('aria-expanded'), 'true');
        // Edge arrows are retired. The drawer closes through click-away or
        // the existing keyboard shortcut, including at narrow phone widths.
        for (const width of [320, 390, 600, 767]) {
            await page.setViewportSize({ width, height: 844 });
            await page.evaluate(() => frappe.app.sidebar.open());
            assert.equal(await page.locator('.sidebar-collapse-arrow').count(), 0);
            await page.mouse.click(width - 5, 800);
            await page.waitForFunction(() => !frappe.app.sidebar.sidebar_expanded);
            await sidebarToggle.click();
            await page.keyboard.press('Control+/');
            await page.waitForFunction(() => !frappe.app.sidebar.sidebar_expanded);
        }
        await page.setViewportSize({ width: 600, height: 844 });
        await sidebarToggle.click();
        await page.screenshot({ path: path.join(root, '.artifacts/sidebar-header-toggle.png'), animations: 'disabled' });
        // Click-away dismissal still works too.
        await page.mouse.click(590, 800);
        await page.waitForFunction(() => !frappe.app.sidebar.sidebar_expanded);
        assert.equal(await sidebarToggle.locator('.sidebar-toggle-icon use').getAttribute('href'), '#icon-panel-left-open');
        await page.mouse.move(590, 800);
        await page.evaluate(() => document.activeElement?.blur());
        assert.equal(await sidebarToggle.locator('.sidebar-toggle-icon').isVisible(), true, 'directional icon remains after pointer leaves');
        const workspaceChip = page.locator('.vector-workspace-chip:visible');
        assert.equal(await workspaceChip.locator('.vector-workspace-chip-action').isVisible(), true);
        await workspaceChip.hover();
        await opened(false);
        await page.screenshot({ path: path.join(root, '.artifacts/workspace-chip-hover-icons.png'), animations: 'disabled' });
        console.log('PASS: mobile drawer and directional icons, routes, keyboard, attached desktop navigation with legacy preferences');
        assert.deepEqual(errors, []);
    } catch (error) {
        console.error('Browser errors:', errors, 'URL:', page.url());
        console.error('Navigation state:', await page.evaluate(() => ({
            expanded: frappe.app.sidebar.sidebar_expanded,
            dock: frappe.app.sidebar.dock.is_open,
            pinned: frappe.app.sidebar.dock.is_pinned,
            preference: frappe.boot.desk_settings.dock_mode,
            position: frappe.vector_navigation_position.get(),
            modal: !!document.querySelector(".modal.show, .modal.in, .es-menu[data-state='open']"),
        })));
        await page.screenshot({ path: path.join(root, '.artifacts/workspace-chip-failure.png') }).catch(() => {});
        throw error;
    } finally {
        try {
            if (initial !== undefined) await page.evaluate(mode =>
                frappe.db.set_value('User', frappe.session.user, 'dock_mode', mode), initial);
        } finally {
            await browser.close();
        }
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
