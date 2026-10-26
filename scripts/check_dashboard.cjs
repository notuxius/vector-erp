// Read-only homepage, drilldown and responsive-layout checks against the running site.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");
const root = path.resolve(__dirname, "..");
const env = Object.fromEntries(fs.readFileSync(path.join(root, ".env"), "utf8")
    .split(/\r?\n/).filter(line => line && !line.startsWith("#")).map(line => {
        const i = line.indexOf("="); return [line.slice(0, i), line.slice(i + 1)];
    }));
const base = `http://localhost:${env.HTTP_PORT || 8080}`;
(async () => {
    const browser = await chromium.launch({ headless: true });
    try {
        const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
        const login = await context.request.post(`${base}/api/method/login`, {
            form: { usr: "Administrator", pwd: env.ADMIN_PASSWORD },
        });
        assert.equal(login.status(), 200);
        console.log("Login destination:", (await login.json()).home_page);
        const page = await context.newPage();
        page.setDefaultTimeout(30000);
        const errors = [];
        page.on("pageerror", e => errors.push(e.message));
        await page.goto(`${base}/desk/operations-overview`);
        await page.locator(".vo-metric").first().waitFor();
        assert.equal(await page.locator(".vo-metric").count(), 8);
        assert.deepEqual(await page.locator(".vo-domain h3").allTextContents(), ["Продажі", "Бухгалтерія", "Закупівлі", "Склад"]);
        assert.equal(new URL(page.url()).pathname, "/desk/operations-overview");
        assert.equal((await page.locator(".page-head:visible .vector-navigation-breadcrumbs").innerText()).trim(), "Огляд");
        assert.equal(await page.locator("#desk-dock .dock-items > .active").count(), 0);
        assert.equal(await page.locator(".body-sidebar:visible").count(), 1);
        assert.equal(await page.locator("#vo-summary, .vo-welcome, .vo-period-select").count(), 0);
        const period = page.locator(".vo-period-button:visible");
        assert(await period.evaluate(el => el.previousElementSibling?.classList.contains("vector-appearance-button")));
        const appearanceBox = await page.locator(".vector-appearance-button:visible").boundingBox();
        const periodBox = await period.boundingBox();
        assert(periodBox.x >= appearanceBox.x + appearanceBox.width - 1, "Chart period must follow Appearance");
        const sidebarToggle = page.locator(".page-head:visible .sidebar-toggle-btn");
        await sidebarToggle.click();
        await page.waitForFunction(() => !frappe.app.sidebar.sidebar_expanded);
        await sidebarToggle.click();
        await page.waitForFunction(() => frappe.app.sidebar.sidebar_expanded);
        await page.evaluate(() => document.fonts.ready);
        await page.waitForFunction(() => !document.body.classList.contains("vector-navigation-top")
            || document.querySelector(".page-head").getBoundingClientRect().top
                >= document.querySelector("#desk-dock").getBoundingClientRect().bottom - 1);
        const overview = await context.request.get(`${base}/api/method/warehouse_core.dashboard.get_overview`);
        assert.equal(overview.status(), 200);
        const data = (await overview.json()).message;
        const overviewItems = page.locator(".body-sidebar [data-overview-section]");
        assert.deepEqual(await overviewItems.locator(".sidebar-item-label").allTextContents(),
            ["Потребує уваги", "Продажі", "Бухгалтерія", "Закупівлі", "Склад", "Швидкий доступ"]);
        assert.equal(await page.locator(".vector-overview > section").first().getAttribute("id"), "vo-attention");
        assert.equal(await page.evaluate(() => frappe.app.sidebar.current_module), "Operations Overview");
        assert.equal(Number(await page.locator("#vo-attention-count").innerText()),
            data.cards.filter(c => ["overdue", "late_purchases"].includes(c.key)).reduce((sum, c) => sum + c.value, 0));
        for (const key of ["accounting", "stock", "attention", "shortcuts", "attention"]) {
            await page.locator(`[data-overview-section="${key}"]`).click();
            await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
            await page.waitForFunction(key => document.querySelector(`[data-overview-section="${key}"]`)
                ?.getAttribute("aria-current") === "location", key);
            assert.equal(await page.evaluate(() => document.activeElement.id), `vo-${key}`);
        }
        // Scrolling independently of the sidebar updates the active section too.
        await page.locator("#vo-purchasing").evaluate(el => {
            document.querySelector(".main-section").scrollBy({
                top: el.getBoundingClientRect().top - 200, behavior: "instant",
            });
        });
        await page.waitForFunction(() => document.querySelector('[data-overview-section="purchasing"]')
            ?.getAttribute("aria-current") === "location");
        // Refresh must not jump back to the last clicked URL fragment.
        await page.locator(".page-head:visible .primary-action").click();
        await page.waitForFunction(() => document.querySelector(".vector-overview").getAttribute("aria-busy") === "false");
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        assert.equal(await page.locator('[aria-current="location"]').getAttribute("data-overview-section"), "purchasing");
        await page.locator('[data-overview-section="accounting"]').focus();
        await page.keyboard.press("Enter");
        await page.reload();
        await page.locator(".vo-metric").first().waitFor();
        await page.waitForFunction(() => document.querySelector('[data-overview-section="accounting"]')
            ?.getAttribute("aria-current") === "location");
        await page.locator('[data-overview-section="attention"]').click();
        console.log("Metrics:", data.cards.map(c => ({ key: c.key, value: c.value })));
        assert.equal(await page.locator(".vo-company").count(), 0);
        assert.equal(await page.locator(".vo-chart").count(), 4);
        assert.equal(data.chart_data.months, 6);
        assert.deepEqual(data.chart_data.charts.map(chart => chart.key), ["sales", "payments", "purchases", "stock"]);
        assert.equal(await page.locator(".vo-chart-canvas svg").count(), data.chart_data.charts.filter(chart => chart.has_data).length);
        for (const months of [3, 12, 6]) {
            const response = page.waitForResponse(r => r.url().includes("warehouse_core.dashboard.get_overview") && r.request().method() === "POST");
            await page.locator(".vo-period-button:visible").click();
            const periodMenu = page.locator('.es-menu[data-state="open"]');
            assert.equal(await periodMenu.getByRole("menuitemradio", { checked: true }).count(), 1);
            await periodMenu.getByRole("menuitemradio", { name: months === 3 ? "3 місяці" : `${months} місяців`, exact: true }).click();
            const chartResult = (await (await response).json()).message.chart_data;
            await page.waitForFunction(() => document.querySelector(".vector-overview").getAttribute("aria-busy") === "false");
            assert.equal(chartResult.months, months);
            for (const chart of chartResult.charts.filter(chart => chart.key !== "stock")) assert.equal(chart.labels.length, months);
            assert.deepEqual(chartResult.charts.find(chart => chart.key === "stock"), data.chart_data.charts.find(chart => chart.key === "stock"));
        }
        await page.locator('[data-chart="sales"] summary').click();
        assert.equal(await page.locator('[data-chart="sales"] tbody tr').count(), 6);
        await page.locator('[data-chart="sales"] summary').click();
        const dir = path.join(root, ".artifacts", "dashboard");
        fs.mkdirSync(dir, { recursive: true });
        for (const key of ["sales", "payments", "purchases", "stock"]) {
            if (data.chart_data.charts.find(chart => chart.key === key).has_data) {
                await page.waitForFunction(key => [...document.querySelectorAll(`[data-chart="${key}"] svg .bar`)]
                    .some(bar => Number(bar.getAttribute("height")) > 5), key);
            }
            await page.locator(`[data-chart="${key}"]`).screenshot({ path: path.join(dir, `chart-${key}.png`) });
        }
        await page.locator('[data-overview-section="attention"]').click();
        await page.screenshot({ path: path.join(dir, "desktop.png"), fullPage: true });
        const href = await page.locator(".vo-metric").first().getAttribute("href");
        await page.locator(".vo-metric").first().click();
        await page.waitForFunction(() => window.cur_list?.doctype === "Sales Order");
        await page.waitForFunction(() => cur_list.filter_area?.get().some(filter =>
            filter[1] === "per_delivered" && filter[2] === "<" && Number(filter[3]) === 100));
        const filters = await page.evaluate(() => cur_list.filter_area.get());
        assert(filters.some(filter => filter[1] === "status" && filter[2] === "not in"), "Status drilldown filter missing");
        console.log("Drilldown:", href);
        await page.goto(`${base}/desk`);
        await page.locator(".vo-metric").first().waitFor();
        // Old warehouse bookmarks must become the shared homepage too.
        await page.goto(`${base}/desk/stock/operations-overview`);
        await page.locator(".vo-metric").first().waitFor();
        assert.equal(new URL(page.url()).pathname, "/desk/operations-overview");
        for (const workspace of ["selling", "accounting", "buying", "stock"]) {
            await page.locator(`.vo-domain a[href="/desk/${workspace}"]`).click();
            await page.waitForFunction(() => frappe.get_route()[0] === "Workspaces");
            assert.notEqual(await page.evaluate(() => frappe.app.sidebar.current_module), "Operations Overview");
            assert.equal(await overviewItems.count(), 0);
            await page.locator("#desk-dock .vector-dashboard-link").click();
            await page.locator(".vo-metric").first().waitFor();
            assert.equal(new URL(page.url()).pathname, "/desk/operations-overview");
            assert.equal(await page.locator("#desk-dock .dock-items > .active").count(), 0);
            assert.equal(await page.locator(".body-sidebar:visible").count(), 1);
            assert.equal(await page.evaluate(() => frappe.app.sidebar.current_module), "Operations Overview");
            assert.equal(await overviewItems.count(), 6);
        }
        await page.goto(`${base}/`);
        await page.locator(".vo-metric").first().waitFor();
        for (const width of [768, 390]) {
            await page.setViewportSize({ width, height: 844 });
            await page.reload();
            await page.locator(".vo-metric").first().waitFor();
            await page.screenshot({ path: path.join(dir, `${width}.png`), fullPage: true });
            const fits = await page.locator(".vector-overview").evaluate(el => {
                const r = el.getBoundingClientRect();
                return r.right <= innerWidth + 1 && el.scrollWidth <= el.clientWidth + 1;
            });
            assert(fits, `Dashboard overflows at ${width}px`);
            await period.focus();
            await page.keyboard.press("ArrowDown");
            assert.equal(await period.getAttribute("aria-expanded"), "true");
            await page.keyboard.press("Escape");
            assert.equal(await period.getAttribute("aria-expanded"), "false");
            assert(await period.evaluate(el => el.getBoundingClientRect().right <= innerWidth));
            if (width === 390) {
                await sidebarToggle.click();
                await page.waitForFunction(() => frappe.app.sidebar.sidebar_expanded);
                assert.equal(await page.locator(".body-sidebar:visible").count(), 1);
                await page.locator('[data-overview-section="attention"]').click();
                await page.waitForFunction(() => !frappe.app.sidebar.sidebar_expanded);
                assert.equal(await page.evaluate(() => document.activeElement.id), "vo-attention");
            }
        }
        assert.deepEqual(errors, []);
        // Browser-only fixtures exercise populated, negative and zero series without changing ERP records.
        await context.route("**/api/method/warehouse_core.dashboard.get_overview", async route => {
            const response = await route.fetch();
            const json = await response.json();
            for (const chart of json.message.chart_data.charts) {
                chart.has_data = true;
                if (chart.key === "stock") chart.labels = ["Основний склад", "<b>Склад</b> & резерв"];
                chart.datasets.forEach((dataset, index) => {
                    dataset.values = chart.labels.map((_, i) => i === 0 ? 0 : (i === 1 && chart.key === "sales" ? -1500 : (i + 1) * 12500 * (index + 1)));
                });
            }
            await route.fulfill({ response, json });
        });
        for (const width of [1440, 390]) {
            await page.setViewportSize({ width, height: 1000 });
            await page.reload();
            await page.locator('.vo-chart-canvas svg').first().waitFor();
            assert.equal(await page.locator('.vo-chart-canvas svg').count(), 4);
            assert.equal(await page.locator('.vo-chart-canvas svg b').count(), 0);
            await page.waitForFunction(() => [...document.querySelectorAll('.vo-chart-canvas')].every(host =>
                [...host.querySelectorAll('svg .bar')].some(bar => Number(bar.getAttribute('height')) > 5)));
            await page.evaluate(() => { document.documentElement.setAttribute("data-theme-mode", "dark"); frappe.ui.set_theme("dark"); });
            await page.locator('[data-chart="payments"]').screenshot({path:path.join(dir, `fixture-payments-${width}.png`)});
            assert(await page.locator('.vo-domains').evaluate(el => el.scrollWidth <= el.clientWidth + 1));
        }
        assert.deepEqual(errors, []);
        // Browser-only restricted payloads verify full counts, zero and omitted
        // sections without creating records or changing user permissions.
        await context.unroute("**/api/method/warehouse_core.dashboard.get_overview");
        const limited = JSON.parse(JSON.stringify(data));
        limited.sections = limited.sections.filter(s => s.key === "sales");
        limited.cards = limited.cards.filter(c => c.key === "overdue");
        limited.cards[0].value = 17;
        limited.tables = limited.tables.filter(t => t.doctype === "Sales Order");
        limited.chart_data.charts = limited.chart_data.charts.filter(c => c.key === "sales");
        limited.links = limited.links.slice(0, 1);
        await context.route("**/api/method/warehouse_core.dashboard.get_overview", route =>
            route.fulfill({ json: { message: limited } }));
        await page.setViewportSize({ width: 1440, height: 1000 });
        await page.goto(`${base}/desk/operations-overview`);
        await page.waitForFunction(() => document.querySelector("#vo-attention-count")?.textContent.trim() === "17");
        assert.deepEqual(await overviewItems.locator(".sidebar-item-label").allTextContents(),
            ["Потребує уваги", "Продажі", "Швидкий доступ"]);
        limited.cards[0].value = 0;
        await page.locator(".page-head:visible .primary-action").click();
        await page.waitForFunction(() => document.querySelector("#vo-attention-count")?.textContent.trim() === "0");
        limited.sections = limited.cards = limited.tables = limited.links = [];
        limited.chart_data.charts = [];
        await page.locator(".page-head:visible .primary-action").click();
        await page.waitForFunction(() => document.querySelectorAll("[data-overview-section]").length === 0);
        assert.equal(await page.locator("#vo-attention-count").count(), 0);
        assert.deepEqual(errors, []);
        const guest = await browser.newContext();
        const denied = await guest.request.get(`${base}/api/method/warehouse_core.dashboard.get_overview`);
        assert.equal(denied.status(), 403);
        console.log("PASS: root and Desk home, no company selector, drilldown, desktop/tablet/mobile, guest access");
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exit(1); });
