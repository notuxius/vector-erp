// Live slider, text fitting, and preference persistence against the local sandbox.
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
        const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
        assert.equal((await context.request.post(`${base}/api/method/login`, {
            form: { usr: 'Administrator', pwd: env.ADMIN_PASSWORD },
        })).status(), 200);
        await context.addInitScript(() => {
            localStorage.setItem('vector-navigation-widths:undefined',
                JSON.stringify({ sidebar: 288, dock: 104 }));
            if (!localStorage.getItem('vector-navigation-widths:Administrator')) {
                localStorage.setItem('vector-navigation-widths:Administrator',
                    JSON.stringify({ sidebar: 1280, dock: 104, sidebarFitText: false, sidebarFitTextDefaultApplied: true }));
            }
        });
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(error.stack || error.message));
        if (process.env.VECTOR_TEST_LOCAL_ASSETS) {
            for (const asset of ['js/dashboard_navigation.js', 'js/navigation_resize.js', 'js/navigation_mobile.js', 'css/dock_labels.css', 'css/dialog_scrollbars.css']) {
                await page.route(`**/assets/warehouse_core/${asset}*`, route => route.fulfill({
                    path: path.join(root, 'apps/warehouse_core/warehouse_core/public', asset),
                    contentType: asset.endsWith('.js') ? 'application/javascript' : 'text/css',
                }));
            }
        }
        const ready = async () => {
            await page.waitForFunction(() => window.frappe?.app?.sidebar?.sidebar_header);
            await page.evaluate(() => new Promise(resolve => frappe.after_ajax(resolve)));
        };
        const openEditor = async () => {
            await page.locator('.body-sidebar .sidebar-header .header-title').click();
            await page.getByText('Редагувати бічну панель', { exact: true }).click();
            await page.locator('.vector-sidebar-width-slider').waitFor({ state: 'visible' });
        };
        const width = () => page.locator('.body-sidebar').evaluate(el => el.getBoundingClientRect().width);
        const expectWidth = async value => {
            try {
                await page.waitForFunction(expected =>
                    Math.abs(document.querySelector('.body-sidebar').getBoundingClientRect().width - expected) < 1, value);
            } catch (error) {
                console.error('Sidebar width mismatch:', { expected: value, actual: await width() });
                throw error;
            }
        };
        const preferences = () => page.evaluate(() =>
            JSON.parse(localStorage.getItem('vector-navigation-widths:Administrator')));
        await page.goto(`${base}/desk/stock/item`);
        await ready();
        await openEditor();
        const slider = page.locator('.vector-sidebar-width-slider');
        const number = page.locator('.vector-sidebar-width-number');
        const fit = page.locator('.vector-sidebar-fit-text');
        const maxWidth = Number(await slider.getAttribute('max'));
        assert.equal(maxWidth, 400, 'maximum is capped at 400px');
        assert.equal(await slider.getAttribute('min'), '100');
        assert.equal(await number.getAttribute('min'), '100');
        assert.equal(await slider.inputValue(), '400', 'existing settings adopt the 400px default');
        assert.equal(await number.inputValue(), '400');
        assert.equal(await fit.isChecked(), true, 'previously disabled text fitting is enabled once');
        assert.equal((await preferences()).sidebarFitText, true);
        assert.equal((await preferences()).dock, undefined, 'dock width is no longer a preference');
        assert.equal(await page.evaluate(() => document.documentElement.style.getPropertyValue('--vector-dock-width')), '88px');
        assert.equal(await page.locator('.vector-dock-resize, .vector-navigation-resize').count(), 0);
        assert.equal((await preferences()).sidebarDefaultsVersion, 2);
        await fit.uncheck();
        await number.fill('333');
        await expectWidth(333);
        assert.equal(await slider.inputValue(), '333');
        await number.press('ArrowUp');
        await expectWidth(334);
        assert.equal(await slider.inputValue(), '334');
        await number.press('ArrowDown');
        await expectWidth(333);
        await number.fill('99999');
        await number.press('Tab');
        await expectWidth(maxWidth);
        assert.equal(await number.inputValue(), String(maxWidth));
        await number.fill('');
        await number.press('Tab');
        assert.equal(await number.inputValue(), String(maxWidth));
        await number.fill('55');
        await number.press('Tab');
        await expectWidth(100);
        assert.equal(await number.inputValue(), '100');
        assert(await page.locator('.body-sidebar .sidebar-item-label').first().isVisible(), 'labels remain visible at the minimum');
        assert.equal(await page.locator('.sidebar-edge').isVisible(), false);
        await slider.click(); // Also wait for the dialog's opening animation to settle.
        await page.keyboard.press('End');
        const bounds = await slider.boundingBox();
        const start = bounds.x + bounds.width - 8;
        await page.mouse.move(start, bounds.y + bounds.height / 2);
        await page.mouse.down();
        await page.mouse.move(start - 100, bounds.y + bounds.height / 2, { steps: 8 });
        const dragged = Number(await slider.inputValue());
        assert(dragged < maxWidth, 'drag updates the maximum before release');
        assert.equal(await number.inputValue(), String(dragged));
        await expectWidth(dragged);
        await page.mouse.up();
        await slider.focus();
        await page.keyboard.press('End');
        await expectWidth(maxWidth);
        const modal = await page.locator('.vector-sidebar-editor .modal-content').boundingBox();
        assert(Math.abs(modal.x + modal.width / 2 - 800) < 2, 'editor is horizontally centered');
        assert(Math.abs(modal.y + modal.height / 2 - 500) < 2, 'editor is vertically centered');
        fs.mkdirSync(path.join(root, '.artifacts'), { recursive: true });
        await page.screenshot({ path: path.join(root, '.artifacts/sidebar-width-slider.png') });
        await slider.focus();
        await page.keyboard.press('End');
        await page.setViewportSize({ width: 1100, height: 1000 });
        await expectWidth(400);
        assert.equal(await slider.getAttribute('max'), '400');
        assert.equal(await number.inputValue(), '400');
        assert.equal((await preferences()).sidebar, maxWidth, 'viewport resizing preserves the chosen width');
        await page.setViewportSize({ width: 1600, height: 1000 });
        await expectWidth(maxWidth);
        await fit.check();

        // Short/long labels exercise fitting independently of changes in seeded module data.
        // These DOM-only fixtures are discarded on reload; no records are edited.
        await page.evaluate(() => {
            document.querySelectorAll('.body-sidebar .sidebar-item-label, .body-sidebar .header-title')
                .forEach(label => { label.textContent = 'Товар'; });
            return new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        });
        await page.waitForFunction(() =>
            Number.parseFloat(document.documentElement.style.getPropertyValue('--vector-sidebar-width')) < 400);
        const fitted = await width();
        assert(fitted >= 100 && fitted < 400);
        assert.equal((await preferences()).sidebar, maxWidth, 'fitting never overwrites the selected maximum');
        const hiddenText = '.body-sidebar .nested-container .sidebar-item-label';
        assert(await page.locator(hiddenText).count(), 'fixture has nested labels');
        await page.locator(hiddenText).first().evaluate(label => { label.textContent = 'Довга назва '.repeat(40); });
        await expectWidth(maxWidth);
        await slider.focus();
        await page.keyboard.press('Home');
        await expectWidth(100);
        await page.keyboard.press('ArrowRight');
        await expectWidth(101);
        await page.keyboard.press('End');
        await expectWidth(maxWidth);
        await page.locator(hiddenText).first().evaluate(label => { label.textContent = 'Товар'; });
        await expectWidth(fitted);
        await fit.uncheck();
        await expectWidth(maxWidth);
        await fit.check();
        await expectWidth(fitted);
        assert.equal((await preferences()).sidebarFitText, true);
        await page.keyboard.press('Escape');
        await page.reload();
        await ready();
        await openEditor();
        assert.equal(await slider.inputValue(), String(maxWidth));
        assert.equal(await fit.isChecked(), true);
        assert(await width() <= maxWidth);
        await page.keyboard.press('Escape');
        await page.goto(`${base}/desk/selling/quotation`);
        await ready();
        await openEditor();
        assert.equal(await fit.isChecked(), true);
        assert(await width() <= maxWidth);
        await fit.uncheck();
        await page.keyboard.press('Escape');
        await page.reload();
        await ready();
        await openEditor();
        assert.equal(await fit.isChecked(), false, 'an explicit opt-out survives reload');
        assert.equal(await number.inputValue(), String(maxWidth));
        await page.keyboard.press('Escape');
        for (const viewportWidth of [767, 600, 390, 320, 1600]) {
            await page.setViewportSize({ width: viewportWidth, height: 1000 });
            await page.evaluate(() => frappe.app.sidebar.open());
            await expectWidth(viewportWidth < 768 ? Math.min(400, viewportWidth - 60) : 400);
            assert.equal((await preferences()).sidebar, 400, 'mobile never overwrites the saved maximum');
        }
        // Overview uses the same preferences without requesting a database arrangement.
        const arrangementRequests = [];
        page.on('request', request => {
            if (request.url().includes('custom_sidebar.')) arrangementRequests.push(request.url());
        });
        await page.goto(`${base}/desk/operations-overview`);
        await ready();
        await page.locator('.vo-metric').first().waitFor();
        const overviewLinks = await page.locator('.body-sidebar [data-overview-section]').count();
        await openEditor();
        assert.equal(await fit.isChecked(), false);
        await number.fill('333');
        await expectWidth(333);
        assert.equal(await slider.inputValue(), '333');
        await fit.check();
        assert(await width() <= 333);
        assert.equal((await preferences()).sidebarFitText, true);
        await page.screenshot({ path: path.join(root, '.artifacts/overview-sidebar-settings.png') });
        await page.keyboard.press('Escape');
        await page.locator('.modal:visible').waitFor({ state: 'hidden' });
        assert.equal(await page.locator('.vector-sidebar-width-controls').count(), 0);
        assert.equal(await page.locator('.body-sidebar [data-overview-section]').count(), overviewLinks);
        await page.reload();
        await ready();
        await openEditor();
        assert.equal(await number.inputValue(), '333');
        assert.equal(await fit.isChecked(), true);
        await fit.uncheck();
        await expectWidth(333);
        await page.keyboard.press('Escape');
        await page.locator('.modal:visible').waitFor({ state: 'hidden' });
        await page.setViewportSize({ width: 390, height: 844 });
        await page.evaluate(() => frappe.app.sidebar.open());
        await openEditor();
        assert.equal(await number.inputValue(), '333');
        await number.fill('260');
        await expectWidth(260);
        await fit.check();
        assert(await width() <= 260);
        await page.keyboard.press('Escape');
        assert.deepEqual(arrangementRequests, [], 'overview does not load or save Sidebar customizations');
        assert.deepEqual(errors, []);
        const freshContext = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
        await freshContext.addCookies(await context.cookies());
        const freshPage = await freshContext.newPage();
        freshPage.on('pageerror', error => errors.push(error.stack || error.message));
        const localAssets = async target => {
            if (!process.env.VECTOR_TEST_LOCAL_ASSETS) return;
            for (const asset of ['js/dashboard_navigation.js', 'js/navigation_resize.js', 'js/navigation_mobile.js', 'css/dock_labels.css']) {
                await target.route(`**/assets/warehouse_core/${asset}*`, route => route.fulfill({
                    path: path.join(root, 'apps/warehouse_core/warehouse_core/public', asset),
                    contentType: asset.endsWith('.js') ? 'application/javascript' : 'text/css',
                }));
            }
        };
        await localAssets(freshPage);
        await freshPage.goto(`${base}/desk/stock/item`);
        await freshPage.waitForFunction(() => window.frappe?.app?.sidebar?.sidebar_header);
        const freshDefaults = await freshPage.evaluate(() =>
            JSON.parse(localStorage.getItem('vector-navigation-widths:Administrator')));
        assert.equal(freshDefaults.sidebar, 400, 'first visit starts with a 400px maximum');
        assert.equal(freshDefaults.sidebarFitText, true, 'first visit starts with text fitting enabled');
        const openAndSettle = async target => {
            await target.waitForFunction(() => window.frappe?.app?.sidebar?.sidebar_header);
            await target.evaluate(async () => {
                await new Promise(resolve => frappe.after_ajax(resolve));
                await document.fonts.ready;
                frappe.app.sidebar.open();
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            });
            await target.waitForFunction(() => Math.abs(
                document.querySelector('.body-sidebar').getBoundingClientRect().width
                - parseFloat(document.documentElement.style.getPropertyValue('--vector-sidebar-width'))) < 1);
        };
        await openAndSettle(freshPage);
        const desktopFitted = await freshPage.locator('.body-sidebar').evaluate(el => el.getBoundingClientRect().width);
        for (const viewportWidth of [767, 600, 500, 390, 320, 1600]) {
            await freshPage.setViewportSize({ width: viewportWidth, height: 1000 });
            await openAndSettle(freshPage);
            const fittedWidth = await freshPage.locator('.body-sidebar').evaluate(el => el.getBoundingClientRect().width);
            const expected = viewportWidth < 768 ? Math.min(desktopFitted, viewportWidth - 60) : desktopFitted;
            assert(Math.abs(fittedWidth - expected) < 1,
                `${viewportWidth}px: mobile text fitting matches desktop, subject to screen space (${fittedWidth} vs ${expected})`);
            if (viewportWidth < 768) {
                const panelBox = await freshPage.locator('.body-sidebar').boundingBox();
                assert(panelBox.height >= 900, 'mobile drawer remains visibly full-height');
                assert(await freshPage.locator('.body-sidebar .sidebar-item-label').first().isVisible(),
                    'mobile sidebar labels are visible');
                assert.equal(await freshPage.locator('.body-sidebar').evaluate(el => {
                    const rect = el.getBoundingClientRect();
                    return el.contains(document.elementFromPoint(rect.x + 30, rect.y + 100));
                }), true, 'mobile drawer is above the page content');
                assert.equal(await freshPage.locator('.sidebar-collapse-arrow').count(), 0);
                if (viewportWidth === 390) {
                    await freshPage.screenshot({ path: path.join(root, '.artifacts/sidebar-width-mobile.png') });
                }
                await freshPage.mouse.click(viewportWidth - 5, 800);
                await freshPage.waitForFunction(() => !frappe.app.sidebar.sidebar_expanded);
            }
        }
        for (const viewportWidth of [320, 390, 600]) {
            const mobilePage = await freshContext.newPage();
            mobilePage.on('pageerror', error => errors.push(error.stack || error.message));
            await localAssets(mobilePage);
            await mobilePage.setViewportSize({ width: viewportWidth, height: 1000 });
            await mobilePage.goto(`${base}/desk/stock/item`);
            await openAndSettle(mobilePage);
            const initialWidth = await mobilePage.locator('.body-sidebar').evaluate(el => el.getBoundingClientRect().width);
            assert(Math.abs(initialWidth - Math.min(desktopFitted, viewportWidth - 60)) < 1,
                'direct mobile loads use the same text-fitting measurement as desktop');
            await mobilePage.close();
        }
        assert.deepEqual(errors, []);
        await freshContext.close();
        console.log('PASS: default text fitting, exact pixel entry and arrow steps, slider synchronization, input limits, fit-to-text bounds, saved opt-out, reload/module persistence, overview settings on desktop/mobile without arrangement requests, and no browser errors.');
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exit(1); });
