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
                const motion = await group.evaluate(async group => {
                    const entries = group.querySelector(':scope > .nested-container');
                    const button = group.querySelector('.drop-icon');
                    const title = group.querySelector('.sidebar-item-label');
                    const titleOffset = () => title.getBoundingClientRect().top - group.getBoundingClientRect().top;
                    const height = () => entries.getBoundingClientRect().height;
                    async function toggle() {
                        const before = titleOffset();
                        button.click();
                        const slide = group.getAnimations().find(a => a.transitionProperty === 'grid-template-rows');
                        if (!slide) throw Error('Missing sidebar height transition');
                        slide.currentTime = slide.effect.getTiming().duration / 2;
                        const middle = height();
                        if (Math.abs(titleOffset() - before) > 0.5) throw Error('Sidebar title moved during the slide');
                        if (!entries.checkVisibility({ visibilityProperty: true })) {
                            throw Error('Entries disappeared during the slide');
                        }
                        await slide.finished;
                        return [middle, height()];
                    }
                    const full = height();
                    const closing = await toggle();
                    const opening = await toggle();
                    return { full, closing, opening };
                });
                assert(motion.closing[0] > 0 && motion.closing[0] < motion.full && motion.closing[1] === 0,
                    `entries slide closed: ${JSON.stringify(motion)}`);
                assert(motion.opening[0] > 0 && motion.opening[0] < motion.full && motion.opening[1] === motion.full,
                    `entries slide open: ${JSON.stringify(motion)}`);
                await page.emulateMedia({ reducedMotion: 'reduce' });
                await page.waitForFunction(() => document.documentElement.classList.contains('vector-animations-disabled'));
                await heading.locator('button').press('Enter');
                assert.equal(await firstLink.isVisible(), false);
                await heading.locator('button').press('Enter');
                assert.equal(await firstLink.isVisible(), true);
                await page.emulateMedia({ reducedMotion: 'no-preference' });
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
        // On a short panel the scroll arrows appear only once the entries start opening.
        await page.setViewportSize({ width: 1440, height: 500 });
        await page.evaluate(() => {
            const sidebar = frappe.app.sidebar;
            sidebar.open();
            sidebar.items.filter(item => item.item.type === 'Section Break' && item.wrapper?.[0]?.isConnected)
                .forEach(item => item.close());
            document.querySelector('.body-sidebar-top').scrollTop = 0;
        });
        const arrow = page.locator('.body-sidebar .vector-navigation-scroll-arrow').first();
        await arrow.waitFor({ state: 'hidden' });
        await page.waitForFunction(() => !document.querySelector('.body-sidebar')
            .getAnimations({ subtree: true }).some(a => a.transitionProperty === 'grid-template-rows'));
        const fixedTitle = page.locator('.body-sidebar .section-item > .standard-sidebar-item .sidebar-item-label')
            .filter({ hasText: /^Звіти$/ }).first();
        const start = await fixedTitle.boundingBox();
        for (const state of ['visible', 'hidden']) {
            await fixedTitle.click();
            await arrow.waitFor({ state });
            assert(Math.abs((await fixedTitle.boundingBox()).y - start.y) < 1,
                'the title stays at the same screen position when scroll controls appear or disappear');
        }
        console.log('PASS: the dropdown title stays still when the short panel gains or loses scroll arrows.');
        assert.deepEqual(errors, []);
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
