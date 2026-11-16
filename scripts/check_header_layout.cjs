// Resize the real list/form/report headers; no document or user settings are saved.
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
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    try {
        const login = await context.request.post(`${base}/api/method/login`, {
            form: { usr: 'Administrator', pwd: env.ADMIN_PASSWORD },
        });
        assert.equal(login.status(), 200);
        if (process.env.VECTOR_TEST_LOCAL_ASSETS) {
            for (const asset of ['css/navigation_breadcrumbs.css', 'css/navigation_position.css',
                'js/navigation_position.js', 'js/navigation_breadcrumbs.js', 'js/navigation_mobile.js',
                'js/navigation_resize.js', 'css/dock_labels.css']) {
                await context.route(`**/assets/warehouse_core/${asset}*`, route => route.fulfill({
                    path: path.join(root, 'apps/warehouse_core/warehouse_core/public', asset),
                    contentType: asset.endsWith('.js') ? 'application/javascript' : 'text/css',
                }));
            }
        }
        let page = await context.newPage();
        const errors = [];
        page.on('pageerror', e => { errors.push(e.stack || e.message); console.error(e.stack || e.message); });
        await page.goto(`${base}/desk/sales-taxes-and-charges-template`);
        const header = () => page.locator('.page-head:visible');
        await header().locator('.vector-navigation-breadcrumbs li:last-child').waitFor();
        await page.evaluate(() => new Promise(resolve => frappe.after_ajax(resolve)));
        // Optional pre-deployment check of the exact stylesheet to be shipped.
        if (process.env.HEADER_LOCAL_CSS) {
            await page.addStyleTag({ path: path.join(root,
                'apps/warehouse_core/warehouse_core/public/css/navigation_breadcrumbs.css') });
        }
        const measure = async () => {
            // Let resize observers and the next paint settle before measuring.
            await page.evaluate(() => new Promise(resolve =>
                requestAnimationFrame(() => requestAnimationFrame(resolve))));
            return header().evaluate(el => {
            const rect = e => {
                const r = e.getBoundingClientRect();
                return { x: r.x, y: r.y, right: r.right, bottom: r.bottom, width: r.width, height: r.height };
            };
            const crumbs = [...el.querySelectorAll('.vector-navigation-breadcrumbs li')];
            const title = crumbs.pop();
            const visible = e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
            return {
                head: rect(el), title: rect(title),
                toggle: rect(el.querySelector('.sidebar-toggle-btn')),
                parents: crumbs.map(rect),
                utility: [...el.querySelectorAll('.vector-top-actions .item-anchor')].map(rect),
                controls: [...el.querySelectorAll('.standard-actions button')].filter(visible).map(rect),
                view: [...el.querySelectorAll('.view-switcher button')].filter(visible).map(rect),
                appearance: rect(el.querySelector('.vector-appearance-button')),
                actions: rect(el.querySelector('.page-actions')),
                isList: frappe.get_route()[0] === 'List',
                titleLabel: rect(title.querySelector('.es-breadcrumbs__label')),
                container: el.querySelector('.container').clientWidth,
                viewport: innerWidth,
            };
            });
        };
        function check(m, label) {
            assert(m.title.width > 0 && m.titleLabel.height > 0, `${label}: title visible`);
            for (const parent of m.parents) {
                assert(parent.y < m.title.bottom && parent.bottom > m.title.y,
                    `${label}: breadcrumbs and title stay on one line`);
                assert(parent.bottom <= m.title.y + 1 || parent.right <= m.title.x + 1,
                    `${label}: breadcrumb path does not overlap title`);
            }
            for (const utility of m.utility) {
                assert(utility.width >= 44 && utility.x >= 0 && utility.right <= m.viewport + 1,
                    `${label}: utility control is usable and fits`);
                assert(m.controls.some(control => control.y < utility.bottom && control.bottom > utility.y),
                    `${label}: search and notifications share the page action row`);
                assert(m.controls.every(control => control.x >= utility.right - 1
                    || control.right <= utility.x + 1), `${label}: utility does not overlap actions`);
            }
            for (const control of m.controls) {
                assert(control.width >= 20, `${label}: usable action width`);
                assert(control.x >= -1 && control.right <= m.viewport + 1, `${label}: action fits viewport`);
                assert(control.y >= m.head.y && control.bottom <= m.head.bottom + 1,
                    `${label}: action fits header`);
            }
            if (m.controls.length) {
                assert(m.controls.every(c => Math.abs(c.y - m.controls[0].y) <= 1),
                    `${label}: standard actions stay on one row`);
                if (m.title.y < m.appearance.bottom && m.title.bottom > m.appearance.y) {
                    const next = m.controls.find(c => c.x >= m.appearance.right - 1);
                    if (next) {
                        assert(next.x - m.appearance.right <= 16,
                            `${label}: appearance stays beside actions on a shared title row`);
                    }
                }
            }
            if (m.isList) {
                assert.equal(m.view.length, 1, `${label}: view switcher stays visible`);
                const view = m.view[0];
                assert(view.width >= 28 && view.x >= 0 && view.right <= m.viewport,
                    `${label}: view switcher fits viewport`);
                assert(Math.abs(view.y - m.appearance.y) <= 1,
                    `${label}: view and appearance share a row`);
                assert(m.appearance.x - view.right >= 0 && m.appearance.x - view.right <= 12,
                    `${label}: view and appearance stay adjacent`);
                assert(view.x - m.actions.x <= 16,
                    `${label}: no unused space before view preferences`);
            }
            assert(m.titleLabel.bottom <= m.head.bottom + 1, `${label}: title is not clipped`);
        }
        // Fine-grained resize in both directions catches the oscillation from the video.
        let previous;
        for (const width of [767, 740, 700, 660, 620, 580, 540, 500, 460, 420, 390, 360, 320,
            360, 390, 420, 460, 500, 540, 580, 620, 660, 700, 740, 767]) {
            await page.setViewportSize({ width, height: 900 });
            const m = await measure();
            check(m, `tax list ${width}`);
            if (previous && width < previous.width) {
                assert(m.head.height >= previous.height - 1,
                    `narrowing ${previous.width} → ${width} must not pull rows back up`);
            }
            previous = { width, height: m.head.height };
        }
        for (const full of [false, true]) {
            await page.evaluate(full => {
                if (document.body.classList.contains('full-width') !== full) {
                    frappe.ui.toolbar.toggle_full_width();
                }
            }, full);
            for (const width of [1440, 1280, 1200, 1100, 1000, 900, 800, 768]) {
                await page.setViewportSize({ width, height: 900 });
                check(await measure(), `top ${full ? 'full' : 'compact'} ${width}`);
            }
            await page.setViewportSize({ width: 1440, height: 900 });
            for (const sidebarWidth of [56, 128, 320, 480]) {
                await page.evaluate(width => document.documentElement.style
                    .setProperty('--vector-sidebar-width', `${width}px`), sidebarWidth);
                check(await measure(), `top sidebar ${sidebarWidth}`);
            }
        }
        await page.setViewportSize({ width: 1098, height: 900 });
        await page.evaluate(() => {
            document.documentElement.style.removeProperty('--vector-sidebar-width');
            if (!document.body.classList.contains('full-width')) frappe.ui.toolbar.toggle_full_width();
        });
        const wide = await measure();
        assert(wide.parents.every(parent => parent.y < wide.title.bottom && parent.bottom > wide.title.y),
            '1098px: breadcrumbs and title share a line');
        const utilityLabels = () => page.locator('.vector-top-actions .sidebar-item-label:visible').count();
        // Leave room for module names and both utility captions in the top bar.
        await page.setViewportSize({ width: 2400, height: 900 });
        check(await measure(), 'wide utility controls');
        assert.equal(await utilityLabels(), 2, 'wide action row shows utility labels');
        await page.setViewportSize({ width: 320, height: 900 });
        check(await measure(), 'compact utility controls');
        assert.equal(await utilityLabels(), 0, 'narrow action row uses icons only');
        // The title must retain its vertical position when the action section
        // wraps, including headers with and without the 44px utility controls.
        await page.setViewportSize({ width: 1800, height: 900 });
        await page.goto(`${base}/desk/role`);
        await header().locator('.view-switcher button').waitFor();
        await page.evaluate(() => new Promise(resolve => frappe.after_ajax(resolve)));
        if (process.env.HEADER_LOCAL_CSS) {
            await page.addStyleTag({ path: path.join(root,
                'apps/warehouse_core/warehouse_core/public/css/navigation_breadcrumbs.css') });
        }
        let reference;
        const rows = new Set();
        const widths = Array.from({ length: 63 }, (_, i) => 1800 - i * 16);
        for (const width of [...widths, ...widths.toReversed()]) {
            await page.setViewportSize({ width, height: 900 });
            const m = await measure();
            check(m, `role top ${width}`);
            const offsets = [m.toggle, ...m.parents, m.title].map(r => r.y - m.head.y);
            if (reference) {
                offsets.forEach((value, i) => assert(Math.abs(value - reference[i]) <= 0.5,
                    `role top ${width}: arrow and breadcrumbs must not jump when actions wrap`));
            }
            reference = offsets;
            const wrapped = m.actions.y >= m.title.bottom;
            if (!rows.has(wrapped)) {
                await header().screenshot({ path: path.join(root,
                    `.artifacts/header-role-top-${wrapped ? 'wrapped' : 'shared'}.png`) });
            }
            rows.add(wrapped);
        }
        assert.equal(rows.size, 2, `top: checked both shared and wrapped action rows`);
        // Reproduce the recording's page, including the desktop-only mount
        // crossing 992px and the different mount used by a fresh mobile load.
        await page.goto(`${base}/desk/email-account`);
        await header().locator('.view-switcher button').waitFor();
        await page.evaluate(() => new Promise(resolve => frappe.after_ajax(resolve)));
        let stableHeader;
        for (const width of [740, 720, 716, 700, 680, 660, 680, 700, 716, 720, 740]) {
            await page.setViewportSize({ width, height: 900 });
            const m = await measure();
            check(m, `email breadcrumb wrap boundary ${width}`);
            const offsets = [m.head.height, m.title.y - m.head.y, m.actions.y - m.head.y];
            if (stableHeader) {
                offsets.forEach((value, i) => assert(Math.abs(value - stableHeader[i]) <= 1,
                    `${width}: breadcrumb resizing must not move the title or action row vertically`));
            }
            stableHeader = offsets;
        }
        assert(await header().locator('.vector-navigation-breadcrumbs .es-breadcrumbs__item')
            .evaluateAll(items => items.every(item => !item.hasAttribute('title') && !!item.dataset.vectorTooltip)),
            'truncated breadcrumbs retain styled full-text tooltips');
        for (const width of [1440, 1100, 1000, 992, 991, 900, 800, 768, 767, 600, 390, 320,
            390, 767, 768, 991, 992, 1100, 1440]) {
            await page.setViewportSize({ width, height: 900 });
            check(await measure(), `email list ${width}`);
        }
        for (const width of [320, 800, 1440]) {
            await page.setViewportSize({ width, height: 900 });
            await page.reload();
            await header().locator('.view-switcher button').waitFor();
            await page.evaluate(() => new Promise(resolve => frappe.after_ajax(resolve)));
            check(await measure(), `fresh email list ${width}`);
            for (const selector of ['.view-switcher button', '.vector-appearance-button']) {
                const trigger = header().locator(selector);
                await trigger.focus();
                await page.keyboard.press('ArrowDown');
                const menu = page.locator('.es-menu[data-state="open"]').first();
                await menu.waitFor();
                const bounds = await menu.boundingBox();
                assert(bounds.x >= 0 && bounds.x + bounds.width <= width + 1,
                    `${selector} menu fits ${width}px`);
                await page.keyboard.press('Escape');
                assert.equal(await trigger.getAttribute('aria-expanded'), 'false');
            }
            await header().screenshot({ path: path.join(root, `.artifacts/header-preferences-${width}.png`) });
        }
        for (const route of [
            '/desk/home',
            '/desk/stock',
            '/desk/system-settings',
            '/desk/query-report/Stock%20Balance',
        ]) {
            // Each page owns its chart/resize observers; close it before checking
            // the next page so background charts do not keep resizing off-screen.
            await page.close();
            page = await context.newPage();
            page.on('pageerror', e => errors.push(e.stack || e.message));
            await page.goto(base + route);
            if (process.env.HEADER_LOCAL_CSS) {
                await page.addStyleTag({ path: path.join(root,
                    'apps/warehouse_core/warehouse_core/public/css/navigation_breadcrumbs.css') });
            }
            console.log('Checking', await page.evaluate(() => frappe.get_route()));
            await header().locator('.vector-navigation-breadcrumbs li:last-child').waitFor();
            await page.evaluate(() => new Promise(resolve => frappe.after_ajax(resolve)));
            for (const width of [1440, 1200, 1000, 800, 767, 600, 390, 320]) {
                await page.setViewportSize({ width, height: 900 });
                check(await measure(), `${route} ${width}`);
            }
        }
        assert.deepEqual(errors, []);
        console.log('PASS: single-line breadcrumbs, shared utility/action rows, and visible controls at 320–1440px; top navigation and varying sidebar widths.');
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exit(1); });
