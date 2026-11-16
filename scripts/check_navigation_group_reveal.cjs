// Regression for opening a submenu from the collapsed rail after other groups expanded.
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
        const context = await browser.newContext({ viewport: { width: 1440, height: 700 } });
        const login = await context.request.post(`${base}/api/method/login`, {
            form: { usr: 'Administrator', pwd: env.ADMIN_PASSWORD },
        });
        assert.equal(login.status(), 200);
        await context.addInitScript(() => {
            localStorage.setItem('desk-sidebar-collapsed', '0');
            localStorage.setItem('vector-navigation-widths:Administrator',
                JSON.stringify({ sidebar: 384, sidebarFitText: false, sidebarDefaultsVersion: 2 }));
        });
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        if (process.env.VECTOR_TEST_LOCAL_ASSETS || process.env.VECTOR_TEST_SECTIONS_ASSET) {
            await page.route('**/assets/warehouse_core/js/navigation_sections.js*', route => route.fulfill({
                path: process.env.VECTOR_TEST_SECTIONS_ASSET || path.join(root, 'apps/warehouse_core/warehouse_core/public/js/navigation_sections.js'),
                contentType: 'application/javascript',
            }));
        }
        for (const [module, initial, groupLabel] of [
            ['selling', 'quotation', 'Налаштування'],
            ['accounts', 'sales-invoice', 'Налаштування'],
            ['selling', 'quotation', 'Товари'],
            ['selling', 'quotation', 'Звіти'],
        ]) {
            await page.goto(`${base}/desk/${module}/${initial}`);
            await page.waitForFunction(() => window.frappe?.app?.sidebar?.dock?.enabled).catch(async error => {
                fs.mkdirSync(path.join(root, '.artifacts'), { recursive: true });
                await page.screenshot({ path: path.join(root, '.artifacts/submenu-load-failure.png') });
                console.error('App load failed:', page.url(), errors);
                throw error;
            });
            await page.evaluate(() => new Promise(resolve => frappe.after_ajax(resolve)));
            // Reproduce the video's wide panel and previously expanded groups.
            await page.evaluate(() => {
                const sidebar = frappe.app.sidebar;
                sidebar.items.filter(item => item.wrapper?.[0]?.isConnected && item.item.type === 'Section Break')
                    .forEach(item => item.open());
                sidebar.close();
            });
            await page.waitForFunction(() => document.querySelector('.body-sidebar').getBoundingClientRect().width === 56);
            const heading = page.locator('.body-sidebar .section-item > .standard-sidebar-item > .item-anchor')
                .filter({ hasText: new RegExp(`^\\s*${groupLabel}\\s*$`) }).first();
            await heading.scrollIntoViewIfNeeded();
            const before = await heading.boundingBox();
            if (module === 'accounts') {
                await heading.locator('button').focus();
                await page.keyboard.press('Enter');
            } else {
                await heading.click();
            }
            await page.waitForFunction(() => document.querySelector('.body-sidebar').getBoundingClientRect().width === 384);
            await page.waitForFunction(() => !document.querySelector('.body-sidebar-container')
                .getAnimations({ subtree: true }).some(animation => animation.playState === 'running'
                    && Number.isFinite(animation.effect.getComputedTiming().endTime)));
            const group = heading.locator('..').locator('..');
            const firstLink = group.locator('.nested-container .item-anchor[href]').first();
            const position = await heading.evaluate((anchor, anchorY) => {
                const viewport = anchor.closest('.vector-navigation-scroll-viewport');
                const bounds = viewport.getBoundingClientRect();
                const row = anchor.parentElement.getBoundingClientRect();
                const first = anchor.closest('.section-item').querySelector('.nested-container .standard-sidebar-item').getBoundingClientRect();
                const desiredTop = Math.max(bounds.top, Math.min(anchorY - row.height / 2,
                    bounds.bottom - (first.bottom - row.top) - 4));
                return { top: row.top, desiredTop, scroll: viewport.scrollTop,
                    maximum: viewport.scrollHeight - viewport.clientHeight };
            }, before.y + before.height / 2);
            // Alignment may stop at the natural beginning/end of the list.
            assert(Math.abs(position.top - position.desiredTop) < 2
                || (position.scroll < 1 && position.top < position.desiredTop)
                || (position.scroll >= position.maximum - 1 && position.top > position.desiredTop),
            `${module}: ${groupLabel} should stay at the activation position, subject to scroll limits: ${JSON.stringify(position)}`);
            const visibleInPanel = await firstLink.evaluate(anchor => {
                const viewport = anchor.closest('.vector-navigation-scroll-viewport').getBoundingClientRect();
                const rect = anchor.getBoundingClientRect();
                return { visible: rect.top >= viewport.top && rect.bottom <= viewport.bottom,
                    top: rect.top, bottom: rect.bottom, viewportTop: viewport.top, viewportBottom: viewport.bottom };
            });
            assert(visibleInPanel.visible, `${module}: ${groupLabel} must reveal its first destination without manual scrolling: ${JSON.stringify(visibleInPanel)}`);
            if (module === 'selling' && groupLabel === 'Налаштування') {
                fs.mkdirSync(path.join(root, '.artifacts'), { recursive: true });
                await page.screenshot({ path: path.join(root, '.artifacts/submenu-group-reveal.png') });
            }
            const href = await firstLink.getAttribute('href');
            await firstLink.click();
            await page.waitForFunction(href => decodeURI(location.pathname) === decodeURI(href.split('?')[0]), href);
            await page.evaluate(() => new Promise(resolve => frappe.after_ajax(resolve)));
            assert.equal(await page.locator('.body-sidebar .active-sidebar > .item-anchor').getAttribute('href'), href);
            console.log(`PASS: ${module} / ${groupLabel} stays near the activation position, reveals destinations, navigates, and selects the entry.`);
        }
        assert.deepEqual(errors, []);
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
