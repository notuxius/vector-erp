// Exercise the deployed Desk widget. Only structured stock lookups reach ERP.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

async function main() {
    const env = Object.fromEntries(fs.readFileSync(path.join(__dirname, "../.env"), "utf8")
        .split(/\r?\n/).filter(line => line && !line.startsWith("#")).map(line => {
            const i = line.indexOf("=");
            return [line.slice(0, i), line.slice(i + 1)];
        }));
    const base = `http://localhost:${env.HTTP_PORT || 8080}`;
    const browser = await chromium.launch();
    try {
        const page = await browser.newPage();
        if (process.env.VECTOR_TEST_LOCAL_ASSETS) {
            await page.route("**/assets/warehouse_core/js/ai_chat.js*", route => route.fulfill({
                path: path.join(__dirname, "../apps/warehouse_core/warehouse_core/public/js/ai_chat.js"),
                contentType: "application/javascript",
            }));
            await page.route("**/assets/warehouse_core/css/ai_chat.css*", route => route.fulfill({
                path: path.join(__dirname, "../apps/warehouse_core/warehouse_core/public/css/ai_chat.css"),
                contentType: "text/css",
            }));
        }
        const login = await page.request.post(base + "/api/method/login", {
            form: {usr: "Administrator", pwd: env.ADMIN_PASSWORD},
        });
        assert.equal(login.status(), 200);
        const calls = [];
        let stock, failKnowledge = false;
        await page.route("**/api/method/warehouse_core.ai_chat.status*", route =>
            route.fulfill({json: {message: {available: !failKnowledge}}}));
        const tokens = {availability: "1".repeat(32), knowledge: "2".repeat(32)};
        const plans = new Map();
        await page.route("**/api/method/warehouse_core.ai_chat.route", async route => {
            const args = route.request().postDataJSON();
            const port = process.env.VECTOR_TEST_LOCAL_ASSETS && Number(process.env.VECTOR_TEST_ROUTE_PORT);
            const options = {postData: new URLSearchParams({...args, count_clarification: "0"}).toString()};
            if (port) {
                options.url = `http://127.0.0.1:${port}/api/method/warehouse_core.ai_chat.route`;
                options.headers = {...route.request().headers(), "X-Frappe-Site-Name": "frontend"};
            }
            const response = await route.fetch(options);
            assert.equal(response.status(), 200);
            plans.set(args.message, (await response.json()).message.requests);
            await route.fulfill({response});
        });
        function fixture(args) {
            calls.push(args);
            const result = args.mode === "availability" ? {ok: true, text: "Перевірено в ERP", availability: stock}
                : failKnowledge ? {ok: false, message: "ШІ тимчасово недоступний. Зверніться до адміністратора.", contact_admin: true}
                    : {ok: true, text: "Умови гарантії [1]", sources: [{name: "warranty", title: "Гарантія", revision: 1}]};
            if (result.ok) result.conversation_id = tokens[args.mode];
            return result;
        }
        await page.route("**/api/method/warehouse_core.ai_chat.combined", async route => {
            const args = route.request().postDataJSON(), context = JSON.parse(args.conversations);
            const results = plans.get(args.message).map(q => fixture({...q, conversation_id: context[q.mode]}));
            await route.fulfill({json: {message: {ok: true, results}}});
        });
        await page.route("**/api/method/warehouse_core.ai_chat.send*", async route => {
            const args = route.request().postDataJSON();
            if (!stock && args.message === "SERVO-1") {
                const quota = await page.request.post(base + "/api/method/warehouse_core.ai_chat.question_usage", {
                    headers: {"X-Frappe-CSRF-Token": await page.evaluate(() => frappe.csrf_token)},
                });
                const state = (await quota.json()).message;
                assert.ok(state.remaining && !state.busy, "Administrator allowance is in use");
                if (state.retry_at) await new Promise(resolve => setTimeout(resolve, Math.max(0, state.retry_at * 1000 - Date.now())));
                const response = await route.fetch();
                const result = (await response.json()).message;
                assert.ok(result.ok, result.message);
                stock = result.availability;
            }
            await route.fulfill({json: {message: fixture(args)}});
        });
        const cleared = [];
        await page.route("**/api/method/warehouse_core.ai_chat.reset*", async route => {
            cleared.push(route.request().postDataJSON().conversation_id);
            await route.fulfill({json: {message: {ok: true}}});
        });
        await page.goto(base + "/desk");
        await page.locator("#vector-ai-launcher").click();
        const modePicker = page.locator("#vector-ai-mode");
        const modeOptions = page.locator("#vector-ai-mode-options");
        assert.equal(await modePicker.innerText(), "Залишки та база знань");
        await modePicker.press("Enter");
        assert.deepEqual(await modeOptions.locator("small").allTextContents(), [
            "Залишки, інструкції з ERP і робочі процеси", "Підготувати текст за затвердженими статтями",
        ]);
        await modePicker.press("Escape");
        assert.equal(await modeOptions.isHidden(), true);
        const ask = async message => {
            const input = page.locator("#vector-ai-input");
            await input.fill(message);
            await input.press("Enter");
            await page.waitForFunction(() => !document.querySelector("#vector-ai-input").disabled);
        };
        await ask("SERVO-1");
        assert.equal(calls.length, 1);
        assert.ok(stock.checked_at);
        assert.equal(stock.filters.stock_state, "all");
        await ask("Чи є сервопривід і яка гарантія?");
        assert.deepEqual(calls.slice(1).map(args => args.mode), ["availability", "knowledge"]);
        assert.ok(calls[2].message.includes("сервопривід"));
        assert.ok(!calls[1].message.includes("гарантія"));
        assert.equal(calls[1].conversation_id, tokens.availability);
        assert.equal(calls[2].conversation_id, undefined);
        const reply = page.locator(".vector-ai-message.is-assistant").last();
        assert.equal(await reply.locator(".vector-stock-stamp").count(), 1);
        assert.equal(await reply.locator(".vector-ai-sources a").getAttribute("href"), "/desk/knowledge-article/warranty");
        failKnowledge = true;
        await ask("А які умови для іншого товару?");
        assert.equal(calls.at(-1).conversation_id, tokens.knowledge);
        const error = page.locator(".vector-ai-message.is-error").last();
        assert.match(await error.innerText(), /ШІ тимчасово недоступний/);
        const contact = {name: "Звернутись до адміністратора", exact: true};
        assert.equal(await error.getByRole("button", contact).count(), 1);
        assert.equal(await error.locator("summary").count(), 0);
        assert.equal(await error.locator(".vector-stock-stamp").count(), 0);
        await ask("Чи є DM542 на складі і яка гарантія?");
        assert.equal(await reply.locator(".vector-stock-stamp").count(), 1);
        assert.equal(await reply.getByRole("button", contact).count(), 1);
        assert.equal(await reply.locator("summary").count(), 0);
        await reply.getByRole("button", contact).click();
        await page.waitForFunction(() => frappe.quick_entry?.doctype === "ToDo" && frappe.quick_entry.doc);
        const request = await page.evaluate(() => frappe.quick_entry.doc);
        assert.equal(request.allocated_to, "Administrator");
        assert.match(request.description, /Векторія/);
        assert.ok(!request.description.includes("DM542"));
        await page.evaluate(() => frappe.quick_entry.hide());
        await page.locator("#vector-ai-launcher").click();
        failKnowledge = false;
        const item = "НАВЧАННЯ: шпиндельний картридж 2,2 кВт";
        let before = calls.length;
        await ask(item + " - характеристики");
        assert.deepEqual(calls.slice(before).map(q => q.mode), ["knowledge"]);
        assert.equal(await reply.locator(".vector-stock-stamp").count(), 0);
        before = calls.length;
        await ask("Скільки шпиндельних картриджів 2,2 кВт вільно на складі?");
        assert.deepEqual(calls.slice(before).map(q => q.mode), ["availability"]);
        before = calls.length;
        await ask(item);
        assert.equal(calls.length, before);
        assert.match(await reply.innerText(), /Перевірити залишок чи знайти характеристики/);
        await ask("Залишок");
        assert.equal(JSON.parse(calls.at(-1).availability_filters).query, item);
        await page.getByRole("button", {name: "Новий чат", exact: true}).click();
        await page.waitForFunction(() => !document.querySelector("#vector-ai-input").disabled);
        assert.deepEqual(cleared.sort(), Object.values(tokens).sort());
        assert.equal(await page.locator(".vector-ai-message").count(), 0);
        const toggle = page.getByRole("button", {name: "Фільтри залишків"});
        const filters = page.locator("#vector-stock-filters");
        await toggle.click();
        await filters.getByLabel("Назва або артикул").fill("SERVO-1");
        await filters.getByRole("button", {name: "Показати залишки"}).click();
        assert.equal(await filters.isHidden(), true);
        assert.equal(await toggle.getAttribute("aria-expanded"), "false");
        await reply.locator(".vector-stock-stamp").waitFor();
        assert.equal(JSON.parse(calls.at(-1).availability_filters).query, "SERVO-1");
        await toggle.click();
        assert.equal(await filters.getByLabel("Назва або артикул").inputValue(), "SERVO-1");
        await toggle.click();
        await page.getByRole("button", {name: "Новий чат", exact: true}).click();
        await page.waitForFunction(() => !document.querySelector("#vector-ai-input").disabled);
        failKnowledge = false;
        const modeHeights = new Map();
        for (const mode of ["assistant", "draft"]) {
            await modePicker.click();
            await modeOptions.locator(`button[value="${mode}"]`).click();
            await page.waitForFunction(() => !document.querySelector("#vector-ai-mode").disabled);
            assert.equal(await modeOptions.isHidden(), true);
            assert.equal(await toggle.isVisible(), true);
            for (const width of [1200, 441, 390, 320]) {
                await page.setViewportSize({width, height: 800});
                const captionFits = await modePicker.locator('span[aria-hidden="false"]').evaluate(caption => {
                    const range = document.createRange();
                    range.selectNodeContents(caption);
                    return range.getClientRects().length === 1 &&
                        range.getBoundingClientRect().width <= caption.clientWidth + 1;
                });
                assert.ok(captionFits, `Mode caption should fit on one line at ${width}px`);
                const height = (await modePicker.boundingBox()).height;
                if (mode === "assistant") modeHeights.set(width, height);
                else assert.equal(height, modeHeights.get(width));
            }
            await page.setViewportSize({width: 1200, height: 800});
            if (mode === "draft") {
                const input = page.locator("#vector-ai-input");
                await input.fill("Моя чернетка");
                await toggle.click();
                await filters.getByRole("button", {name: "Показати залишки"}).click();
                await reply.locator(".vector-stock-stamp").waitFor();
                assert.equal(calls.at(-1).mode, "availability");
                assert.equal(await input.inputValue(), "Моя чернетка");
                assert.equal(await modePicker.innerText(), "Чернетка відповіді клієнту");
                await page.getByRole("button", {name: "Новий чат", exact: true}).click();
                await page.waitForFunction(() => !document.querySelector("#vector-ai-input").disabled);
            }
            const suggestions = page.locator(".vector-ai-suggestion");
            const catalog = require("../apps/warehouse_core/warehouse_core/assistant_content/quick_inputs.json");
            const prompts = catalog.filter(p => (p.kind === "draft") === (mode === "draft"));
            assert.deepEqual(await suggestions.allTextContents(), prompts.map(p => p.label));
            for (const {question: prompt, kind} of prompts) {
                const before = calls.length;
                await page.getByRole("button", {name: prompt, exact: true}).click();
                await reply.waitFor();
                const expectedModes = {stock: ["availability"], knowledge: ["knowledge"],
                    combined: ["availability", "knowledge"], draft: ["draft"]}[kind];
                assert.deepEqual(calls.slice(before).map(args => args.mode), expectedModes);
                if (kind === "combined") {
                    assert.ok(calls.slice(before).every(q => q.message.includes("RECIW4")));
                    assert.ok(!calls[before].message.includes("потужність"));
                } else assert.equal(calls[before].message, prompt);
                assert.deepEqual(await page.locator(".vector-ai-message.is-user .vector-ai-text").allTextContents(), [prompt]);
                if (prompt === "Покажи залишки") assert.equal(await reply.locator(".vector-stock-stamp").count(), 1);
                if (prompt === prompts[0].question) {
                    const followUp = reply.locator(".vector-ai-follow-up");
                    const summary = followUp.locator("summary");
                    assert.equal(await summary.textContent(), mode === "draft" ? "Готові звернення" : "Готові запитання");
                    const beforeFollowUp = calls.length;
                    await summary.press("Enter");
                    await followUp.locator(".vector-ai-suggestion").first().waitFor();
                    assert.deepEqual(await followUp.locator("button").allTextContents(), prompts.map(p => p.label));
                    assert.equal(calls.length, beforeFollowUp);
                    const next = prompts.find(p => p.kind === (mode === "draft" ? "draft" : "knowledge"));
                    await followUp.getByRole("button", {name: next.question, exact: true}).click();
                    await page.waitForFunction(() => !document.querySelector("#vector-ai-input").disabled);
                    assert.deepEqual(calls.slice(beforeFollowUp).map(q => [q.mode, q.message]), [[next.kind, next.question]]);
                    assert.deepEqual(await page.locator(".vector-ai-message.is-user .vector-ai-text").allTextContents(), [prompt, next.question]);
                    assert.equal(await page.locator(".vector-ai-follow-up").count(), 2);
                }
                await page.getByRole("button", {name: "Новий чат", exact: true}).click();
                await page.waitForFunction(() => !document.querySelector("#vector-ai-input").disabled);
            }
        }
        console.log("Passed: combined mode, live stock, replies, citations, follow-ups, reset, and all quick inputs in both modes. Paid calls: 0.");
    } finally {
        await browser.close();
    }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
