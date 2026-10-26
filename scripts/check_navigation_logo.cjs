// Logo fixtures stay inside this browser; company records and branding are not saved.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');

(async () => {
    const env = Object.fromEntries(fs.readFileSync(path.join(root, '.env'), 'utf8')
        .split(/\r?\n/).filter(line => line && !line.startsWith('#')).map(line => {
            const i = line.indexOf('='); return [line.slice(0, i), line.slice(i + 1)];
        }));
    const base = `http://localhost:${env.HTTP_PORT || 8080}`;
    const browser = await chromium.launch({ headless: true });
    try {
        const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
        assert.equal((await context.request.post(base + '/api/method/login', {
            form: { usr: 'Administrator', pwd: env.ADMIN_PASSWORD },
        })).status(), 200);
        const fixtures = {
            red: '#d42b35', yellow: '#efbe19', green: '#26954b',
            pale: '#fff2b0', deep: '#052c14', monochrome: '#333333',
        };
        if (process.env.VECTOR_TEST_LOCAL_ASSETS) {
            for (const asset of ['js/navigation_logo.js', 'css/navigation_position.css']) {
                await context.route(`**/assets/warehouse_core/${asset}*`, route => route.fulfill({
                    path: path.join(root, 'apps/warehouse_core/warehouse_core/public', asset),
                    contentType: asset.endsWith('.css') ? 'text/css' : 'application/javascript',
                }));
            }
        }
        let releaseSlow;
        const slowGate = new Promise(resolve => { releaseSlow = resolve; });
        await context.route('**/vector-logo-fixture/*.svg', async route => {
            const name = new URL(route.request().url()).pathname.split('/').pop().replace('.svg', '');
            if (name === 'broken') return route.fulfill({ status: 404, body: '' });
            if (name === 'slow') await slowGate;
            const color = name === 'slow' ? fixtures.red : fixtures[name];
            // A coloured mark with ample white background, transparent border and a small accent.
            return route.fulfill({ contentType: 'image/svg+xml', body:
                `<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect x="4" y="4" width="56" height="56" fill="white"/><rect x="12" y="12" width="40" height="40" fill="${color}"/>${name === 'monochrome' ? '' : '<rect x="44" y="44" width="4" height="4" fill="#2584dc"/>'}</svg>` });
        });
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.goto(base + '/desk/stock');
        const dock = page.locator('#desk-dock');
        const home = dock.locator('.vector-dashboard-link');
        await home.waitFor();
        await page.waitForFunction(() => document.querySelector('#desk-dock').style
            .getPropertyValue('--vector-logo-color'));
        const accent = () => dock.evaluate(el => el.style.getPropertyValue('--vector-logo-color'));
        const original = await home.locator('img').getAttribute('src');
        const originalColor = await accent();
        const channels = originalColor.match(/\d+/g).map(Number);
        channels.forEach((channel, i) => assert(Math.abs(channel - [36, 140, 200][i]) <= 2,
            'extract the original M artwork colour'));
        async function replace(source) {
            await home.locator('img').evaluate((image, source) => { image.src = source; }, source);
        }
        async function expectAccent(expected) {
            await page.waitForFunction(expected => document.querySelector('#desk-dock').style
                .getPropertyValue('--vector-logo-color') === expected, expected);
        }
        const settlePalette = () => page.evaluate(() => new Promise(resolve =>
            requestAnimationFrame(() => requestAnimationFrame(resolve))));
        for (const [name, color] of Object.entries(fixtures)) {
            const source = `${base}/vector-logo-fixture/${name}.svg`;
            await replace(source);
            if (name === 'monochrome') {
                await home.locator('img').evaluate(image => image.decode());
                await page.waitForTimeout(100);
                assert.equal(await accent(), '', 'neutral fallback for monochrome artwork');
                continue;
            }
            const expected = `rgb(${color.slice(1).match(/../g).map(hex => parseInt(hex, 16)).join(', ')})`;
            await expectAccent(expected);
            for (const theme of ['light', 'dark']) {
                await page.evaluate(theme => document.documentElement.setAttribute('data-theme', theme), theme);
                await settlePalette();
                await page.waitForTimeout(200);
                const activeIcon = dock.locator('.dock-items > .active .dock-item-icon');
                assert.equal(await activeIcon.evaluate(el => getComputedStyle(el).color), expected);
                assert.equal(await activeIcon.evaluate(el => getComputedStyle(el)
                    .getPropertyValue('--duotone-light').trim()), 'currentColor');
                assert.equal(await activeIcon.evaluate(el => getComputedStyle(el)
                    .getPropertyValue('--duotone-dark').trim()), 'currentColor');
                const activeButton = dock.locator('.dock-items > .active');
                for (const action of ['hover', 'focus']) {
                    await activeButton[action]();
                    assert.equal(await activeIcon.evaluate(el => getComputedStyle(el).color), expected,
                        `${name}/${theme}/${action}: preserve exact source colour`);
                }
                assert(await dock.locator('.dock-items > .dock-item:not(.active) .dock-item-icon')
                    .evaluateAll((els, expected) => els.every(el => getComputedStyle(el).color !== expected), expected));
                // Only toggle the existing selected state here, so the real app logo isn't restored by routing.
                const logo = home.locator('.header-logo');
                const before = await logo.boundingBox();
                await home.evaluate(el => el.classList.add('active'));
                await page.waitForTimeout(200);
                assert.equal(await logo.evaluate(el => getComputedStyle(el, '::before').backgroundColor), expected);
                assert((await logo.evaluate(el => getComputedStyle(el, '::before').maskImage)).includes(source));
                assert.deepEqual(await logo.boundingBox(), before, 'logo stays fixed when selected');
                await home.evaluate(el => el.classList.remove('active'));
                assert.notEqual(await logo.evaluate(el => getComputedStyle(el, '::before').backgroundColor), expected);
            }
        }
        // Complete a stale red extraction only after green has already won.
        await replace(`${base}/vector-logo-fixture/slow.svg`);
        await page.waitForTimeout(50);
        await replace(`${base}/vector-logo-fixture/green.svg`);
        await expectAccent('rgb(38, 149, 75)');
        releaseSlow();
        await page.waitForTimeout(200);
        assert.equal(await accent(), 'rgb(38, 149, 75)');
        await replace(`${base}/vector-logo-fixture/broken.svg`);
        await page.waitForTimeout(200);
        assert.equal(await accent(), '', 'unreadable logo clears previous accent');
        await replace(original);
        await expectAccent(originalColor);
        await home.click();
        await page.locator('.vo-metric').first().waitFor();
        assert.equal(await home.getAttribute('aria-current'), 'page');
        assert.equal(await accent(), originalColor);
        assert.deepEqual(errors, []);
        console.log('PASS: exact logo colour in both icon tones, red/yellow/green/pale/deep, live light/dark changes, hover/focus/selection, fallbacks, stale loads, fixed position and navigation.');
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
