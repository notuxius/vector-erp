// Exercise real Settings controls and frame-by-frame sidebar restoration.
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
    const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.stack || error.message));
    let originalMode;
    const ready = async () => {
        await page.waitForFunction(() => window.frappe?.app?.sidebar?.sidebar_header);
        await page.evaluate(async () => {
            await new Promise(resolve => frappe.after_ajax(resolve));
            await document.fonts.ready;
            frappe.app.sidebar.open();
        });
        await page.waitForFunction(() => !document.documentElement.classList.contains('vector-navigation-restoring'));
    };
    const settings = async () => {
        await page.evaluate(async () => {
            await frappe.require('user_settings_dialog.bundle.js');
            await frappe.ui.show_user_settings('appearance');
        });
        await page.locator('[data-fieldname="vector_animations"] input').waitFor({ state: 'visible' });
    };
    const closeSettings = () => page.evaluate(() => frappe.ui._user_settings_dialog.hide());
    try {
        assert.equal((await context.request.post(`${base}/api/method/login`, {
            form: { usr: 'Administrator', pwd: env.ADMIN_PASSWORD },
        })).status(), 200);
        await context.addInitScript(() => {
            window.sidebarFrames = [];
            const sample = () => {
                const sidebar = document.querySelector('body > .body-sidebar-container.expanded .body-sidebar');
                if (sidebar && sidebar.getClientRects().length && window.frappe?.app?.sidebar?.sidebar_header) {
                    window.sidebarFrames.push(sidebar.getBoundingClientRect().width);
                }
                if (window.sidebarFrames.length < 180) requestAnimationFrame(sample);
            };
            requestAnimationFrame(sample);
        });
        await page.goto(`${base}/desk/stock/item`);
        await ready();
        originalMode = await page.evaluate(() => frappe.boot.desk_settings.dock_mode);
        await settings();
        const toggle = page.locator('[data-fieldname="vector_animations"] input');
        assert.equal(await toggle.isChecked(), true, 'animations default to on in a fresh browser');
        await page.locator('[data-fieldname="vector_animations"] .switch-visual').click();
        assert.equal(await page.evaluate(() => frappe.vector_animations.enabled()), false);
        const effects = await page.evaluate(async () => {
            const button = document.createElement('div');
            button.style.cssText = 'transition: transform 2s; animation: test 2s infinite';
            document.body.append(button);
            const duration = getComputedStyle(button).transitionDuration;
            const animation = button.animate([{ opacity: 0 }, { opacity: 1 }], 1000);
            await animation.finished;
            const timing = animation.effect.getTiming().duration;
            button.remove();
            return { duration, timing, jquery: $.fx.off };
        });
        assert(parseFloat(effects.duration) < .001);
        assert.equal(effects.timing, 0);
        assert.equal(effects.jquery, true);
        await closeSettings();
        const checkHover = async () => {
            const button = page.locator('.vector-top-actions .item-anchor:visible').first();
            await page.mouse.move(1599, 999);
            await button.evaluate(el => el.getAnimations().forEach(animation => animation.finish()));
            await button.hover();
            const hover = await button.evaluate(el => ({
                duration: getComputedStyle(el).transitionDuration,
                properties: getComputedStyle(el).transitionProperty,
                running: el.getAnimations().some(animation => animation.transitionProperty === 'background-color'),
            }));
            assert.equal(hover.duration, '0.15s');
            assert(!hover.properties.includes('transform'), 'panel state changes remain instant');
            assert(hover.running, 'button hover visibly animates while global animations are off');
            // Finish hover-in before leaving; an immediate reversal can last less than a frame.
            await button.evaluate(el => el.getAnimations().forEach(animation => animation.finish()));
            await page.mouse.move(1599, 999);
            assert(await button.evaluate(el => el.getAnimations().some(animation =>
                animation.transitionProperty === 'background-color')), 'hover-out also animates');
            assert(parseFloat(await page.locator('.body-sidebar').evaluate(el =>
                getComputedStyle(el).transitionDuration)) < .001, 'side panel animations remain disabled');
        };
        await checkHover();
        await page.goto(`${base}/desk/setup/user`);
        await ready();
        await checkHover();
        await page.reload();
        await ready();
        assert.equal(await page.evaluate(() => frappe.vector_animations.enabled()), false);
        await settings();
        assert.equal(await toggle.isChecked(), false);
        await page.locator('[data-fieldname="vector_animations"] .switch-visual').click();
        assert.equal(await page.evaluate(() => $.fx.off), false);
        assert.equal(await page.evaluate(() => {
            const animation = document.body.animate([{ opacity: 1 }, { opacity: 1 }], 321);
            const duration = animation.effect.getTiming().duration;
            animation.cancel();
            return duration;
        }), 321);
        // Legacy floating preferences must never detach desktop navigation.
        assert.equal(await page.locator('.dock-preview-rail, .dock-preview-tray').count(), 0);
        assert.equal(await page.locator('[data-fieldname="vector_navigation_position"]').count(), 0);
        await closeSettings();
        await page.evaluate(() => {
            localStorage.setItem(`vector-navigation-position:${frappe.session.user}`, 'left');
            return frappe.db.set_value('User', frappe.session.user, 'dock_mode', 'Floating');
        });
        await page.reload();
        await ready();
        assert.equal(await page.evaluate(() => frappe.app.sidebar.dock.is_pinned), true);
        assert(await page.locator('body').evaluate(el => el.classList.contains('vector-navigation-top')));
        const dock = await page.locator('#desk-dock').boundingBox();
        assert.equal(dock.y, 0);
        assert.equal(dock.width, await page.evaluate(() => document.documentElement.clientWidth));
        await page.evaluate(() => {
            localStorage.setItem('vector-dock-labels:Administrator', 'hidden');
            frappe.app.sidebar.dock.set_pinned(false);
        });
        await page.reload();
        await ready();
        assert.equal(await page.evaluate(() => frappe.app.sidebar.dock.is_pinned), true);
        assert.equal(await page.locator('.vector-workspace-chip:visible, .vector-dock-label-toggle').count(), 0);
        assert.equal(await page.locator('#desk-dock .dock-item-label').first().isVisible(), true);
        await settings();
        fs.mkdirSync(path.join(root, '.artifacts'), { recursive: true });
        await page.screenshot({ path: path.join(root, '.artifacts/interface-preferences.png') });
        await closeSettings();
        for (const route of ['/desk/stock/item', '/desk/selling/quotation', '/desk/setup/user']) {
            await page.goto(base + route);
            await ready();
            await page.waitForFunction(() => document.querySelector('.body-sidebar').getAnimations().length === 0);
            const width = await page.locator('body > .body-sidebar-container .body-sidebar').evaluate(el => el.getBoundingClientRect().width);
            await page.reload();
            await ready();
            const after = await page.locator('body > .body-sidebar-container .body-sidebar').evaluate(el => el.getBoundingClientRect().width);
            const frames = await page.evaluate(() => window.sidebarFrames);
            console.log(route, { width, after, frames: [...new Set(frames)] });
            assert(Math.abs(after - width) < 1, 'reload preserves fitted width');
            assert(frames.every(value => Math.abs(value - width) < 1), 'no intermediate maximum-width flash');
            assert.equal(await page.evaluate(() => frappe.vector_animations.enabled()), true);
        }
        // Follow a real Desk route change while the expanded sidebar refits to different labels.
        const fromWidth = await page.locator('.body-sidebar').evaluate(el => el.offsetWidth);
        await page.evaluate(() => {
            window.sidebarTransitions = [];
            document.querySelector('.body-sidebar-container').addEventListener('transitionrun', event => {
                if (['width', 'left', 'transform', 'opacity'].includes(event.propertyName))
                    window.sidebarTransitions.push(event.propertyName);
            });
            return frappe.set_route('operations-overview');
        });
        await page.locator('.body-sidebar [data-overview-section]').first().waitFor();
        await ready();
        await page.waitForFunction(width => document.querySelector('.body-sidebar').offsetWidth !== width, fromWidth);
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        assert.deepEqual(await page.evaluate(() => window.sidebarTransitions), [], 'route refits do not animate');
        // Without an explicit choice, follow the operating system's motion preference.
        await page.evaluate(() => localStorage.removeItem('vector-animations:Administrator'));
        await page.emulateMedia({ reducedMotion: 'no-preference' });
        await page.reload();
        await ready();
        assert.equal(await page.evaluate(() => frappe.vector_animations.enabled()), true);
        await page.emulateMedia({ reducedMotion: 'reduce' });
        await page.reload();
        await ready();
        assert.equal(await page.evaluate(() => frappe.vector_animations.enabled()), false);
        // Keyboard activation of the native switch works on a narrow screen too.
        await page.setViewportSize({ width: 390, height: 844 });
        await settings();
        await toggle.focus();
        await page.keyboard.press('Space');
        assert.equal(await page.evaluate(() => frappe.vector_animations.enabled()), true);
        await closeSettings();
        assert.deepEqual(errors, []);
        console.log('PASS: default motion, reduced motion, saved settings, hover feedback, CSS/jQuery/Web Animations, fixed top panel, stable reload widths and instant sidebar refitting on navigation.');
    } finally {
        if (originalMode !== undefined) await page.evaluate(mode =>
            frappe.db.set_value('User', frappe.session.user, 'dock_mode', mode), originalMode);
        await browser.close();
    }
})().catch(error => { console.error(error); process.exit(1); });
