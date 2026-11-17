// Exercise the deployed Desk widget. Only structured stock lookups reach ERP.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

async function checkPanelFade(page) {
    const launcher = page.locator("#vector-ai-launcher");
    const panel = page.locator("#vector-ai-panel");
    await launcher.waitFor();
    const checkTransition = async (button, from, to) => {
        const sample = await button.evaluate(el => {
            el.click();
            const panel = document.querySelector("#vector-ai-panel");
            const transitions = panel.getAnimations();
            const fade = transitions.find(a => a.transitionProperty === "opacity");
            const slide = transitions.find(a => a.transitionProperty === "transform");
            if (!fade || !slide) return null;
            const positions = [0.25, 0.75].map(fraction => {
                slide.currentTime = slide.effect.getTiming().duration * fraction;
                return panel.getBoundingClientRect().top;
            });
            fade.currentTime = fade.effect.getTiming().duration / 2;
            const style = getComputedStyle(panel);
            return {frames: fade.effect.getKeyframes().map(k => k.opacity), opacity: Number(style.opacity),
                positions, clip: style.clipPath, height: panel.offsetHeight, inert: panel.inert};
        });
        assert.ok(sample, "panel starts a slide and fade");
        const [early, late] = sample.positions;
        assert.ok(to === "1" ? early > late : late > early, "panel rises on open and lowers on close");
        assert.deepEqual(sample.frames, [from, to]);
        assert.ok(sample.opacity > 0 && sample.opacity < 1 && sample.height > 0);
        assert.equal(sample.clip, "none");
        assert.equal(sample.inert, to === "0");
        await page.waitForFunction(() => !document.querySelector("#vector-ai-panel").getAnimations().length);
    };
    const enabled = await page.evaluate(() => frappe.vector_animations.enabled());
    try {
        await page.emulateMedia({reducedMotion: "no-preference"});
        await page.evaluate(() => frappe.vector_animations.set(true));
        await page.mouse.move(0, 0);
        const idle = await launcher.boundingBox();
        assert.equal(idle.width, idle.height);
        const opacity = () => launcher.evaluate(el => Number(getComputedStyle(el).opacity));
        const idleOpacity = await opacity();
        assert.ok(idleOpacity > 0 && idleOpacity < 1);
        assert.equal(await launcher.getAttribute("aria-label"), await page.evaluate(() => __("Vectoria ✦ AI-assistant")));
        await launcher.hover();
        assert.equal(await launcher.innerText(), "✦");
        assert.deepEqual(await launcher.boundingBox(), idle);
        assert.equal(await opacity(), 1);
        assert.equal(await launcher.evaluate(el => el.getAnimations({subtree: true}).length), 0);
        await checkTransition(launcher, "0", "1");
        await checkTransition(panel.getByRole("button", {name: "Закрити чат"}), "1", "0");
        assert.ok(await panel.isHidden());
        assert.ok(await launcher.evaluate(el => el === document.activeElement));
        await page.emulateMedia({reducedMotion: "reduce"});
        await launcher.press("Enter");
        assert.equal(await panel.evaluate(el => el.getAnimations().length), 0);
        await page.keyboard.press("Escape");
        assert.ok(await panel.isHidden());
    } finally {
        await page.emulateMedia({reducedMotion: null});
        await page.evaluate(value => frappe.vector_animations.set(value), enabled);
    }
}

async function checkDisclosure(details, motion) {
    await details.scrollIntoViewIfNeeded();
    const heights = await details.evaluate(async (el, motion) => {
        const samples = [el.offsetHeight];
        for (const open of [true, false]) {
            el.querySelector("summary").click();
            if (el.open !== open) throw Error("Summary did not toggle");
            const transitions = el.getAnimations({subtree: true});
            const slide = transitions.find(a => a.transitionProperty === "grid-template-rows");
            if (Boolean(slide) !== motion) throw Error("Unexpected disclosure motion: " + el.className);
            if (slide) {
                await slide.ready;
                slide.pause();
                slide.currentTime = slide.effect.getTiming().duration / 2;
                await new Promise(requestAnimationFrame);
                samples.push(el.offsetHeight);
                slide.play();
                await Promise.all(transitions.map(a => a.finished));
            }
            samples.push(el.offsetHeight);
        }
        return samples;
    }, motion);
    const closed = heights[0], open = Math.max(...heights);
    assert.ok(open > closed);
    assert.equal(heights.at(-1), closed);
    if (motion) for (const mid of [heights[1], heights[3]]) assert.ok(closed < mid && mid < open, heights.join(", "));
}

async function checkNewChat(page) {
    const reset = page.getByRole("button", {name: "Новий чат", exact: true});
    const input = page.locator("#vector-ai-input");
    const opacity = () => reset.evaluate(button => Number(getComputedStyle(button).opacity));
    const idleOpacity = await opacity();
    for (const ok of [true, false]) {
        await page.route("**/api/method/warehouse_core.ai_chat.route", route => route.fulfill({
            json: {message: {requests: [{mode: "knowledge", message: "Перевірка"}]}},
        }), {times: 1});
        let release;
        const held = new Promise(resolve => { release = resolve; });
        await page.route("**/api/method/warehouse_core.ai_chat.send*", async route => {
            await held;
            await route.fulfill({json: {message: {ok, text: "Готово", message: "Помилка"}}});
        }, {times: 1});
        await input.fill("Перевірка");
        const sending = page.waitForRequest("**/api/method/warehouse_core.ai_chat.send*");
        await input.press("Enter");
        await sending;
        await page.getByRole("button", {name: "Закрити чат"}).click();
        await page.locator("#vector-ai-launcher").click();
        assert.ok(await reset.isDisabled());
        assert.ok(await opacity() < idleOpacity);
        await reset.evaluate(button => button.click());
        assert.ok(await page.locator(".vector-ai-message.is-pending").isVisible());
        release();
        await page.locator(".vector-ai-message.is-pending").waitFor({state: "detached"});
        assert.ok(await reset.isEnabled());
        await reset.click();
        assert.equal(await page.locator(".vector-ai-message").count(), 0);
    }
}

async function checkChatPopovers(page, motion) {
    for (const selector of ["#vector-ai-mode", ".vector-stock-toggle"]) {
        await page.locator(selector).evaluate(async (button, motion) => {
            const panel = document.getElementById(button.getAttribute("aria-controls"));
            for (const open of [true, false]) {
                button.click();
                const transitions = panel.getAnimations();
                if (transitions.some(a => a.transitionProperty === "opacity") !== motion ||
                    panel.inert !== !open || button.getAttribute("aria-expanded") !== String(open)) {
                    throw Error("Unexpected transition or interactive state: " + panel.id);
                }
                // display:none can cancel sibling transitions on the final frame.
                await Promise.allSettled(transitions.map(a => a.finished));
                if ((panel.offsetHeight > 0) !== open) throw Error("Unexpected final size: " + panel.id);
            }
        }, motion);
    }
}

async function checkConsecutiveQuestions(browser, base, cookies) {
    const page = await browser.newPage();
    try {
        await page.context().addCookies(cookies);
        await page.goto(base + "/desk/operations-overview");
        const launcher = page.locator("#vector-ai-launcher");
        const input = page.locator("#vector-ai-input");
        const send = page.locator('.vector-ai-compose-footer [type="submit"]');
        const quotaResponse = () => page.waitForResponse("**/api/method/warehouse_core.ai_chat.question_usage*");
        const initial = quotaResponse();
        await launcher.click();
        let {remaining} = (await (await initial).json()).message;
        assert.ok(remaining > 2, "Need two questions and a spare slot in the laptop allowance");
        // Clarifications exercise the deployed allowance without calling the model.
        for (const question of ["шпиндельний картридж", "сервопривід"]) {
            await input.fill(question);
            assert.ok(await send.isEnabled());
            const quota = quotaResponse();
            await send.click();
            const state = (await (await quota).json()).message;
            assert.equal(state.remaining, --remaining);
            assert.equal(state.retry_after, 0);
            assert.ok(await page.getByRole("button", {name: "Характеристики", exact: true}).last().isEnabled());
        }
    } finally {
        await page.close();
    }
}

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
        let retryAfter = 0;
        await page.route("**/api/method/warehouse_core.ai_chat.question_usage*", route =>
            route.fulfill({json: {message: {retry_after: retryAfter}}}));
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
                : failKnowledge ? {ok: false, message: "ШІ тимчасово недоступний. Зверніться до адміністратора.", contact_admin: true, incident: "provider"}
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
                const response = await route.fetch();
                const result = (await response.json()).message;
                assert.ok(result.ok, result.message);
                stock = result.availability;
            }
            await route.fulfill({json: {message: fixture(args)}});
        });
        const cleared = [];
        const incidents = [];
        await page.route("**/api/method/**.vectoria_feedback.report_error", route => {
            incidents.push(route.request().postDataJSON());
            return incidents.length === 1 ? route.abort() : route.fulfill({json: {message: "MTECH-1"}});
        });
        await page.route("**/api/method/warehouse_core.ai_chat.reset*", async route => {
            cleared.push(route.request().postDataJSON().conversation_id);
            await route.fulfill({json: {message: {ok: true}}});
        });
        await page.goto(base + "/desk/operations-overview");
        const chart = page.locator(".vo-chart-data").first();
        await chart.waitFor();
        await checkDisclosure(chart, true);
        await checkPanelFade(page);
        await page.locator("#vector-ai-launcher").click();
        await checkChatPopovers(page, true);
        await page.evaluate(() => frappe.vector_animations.set(false));
        await checkChatPopovers(page, false);
        await page.evaluate(() => frappe.vector_animations.set(true));
        await checkNewChat(page);
        const input = page.locator("#vector-ai-input");
        const send = page.locator('.vector-ai-compose-footer button[type="submit"]');
        assert.ok(await send.isDisabled());
        await input.click();
        await input.fill("  \n  ");
        await input.press("Enter");
        assert.ok(await send.isDisabled());
        assert.equal(await page.locator(".vector-ai-message.is-user").count(), 0);
        await input.fill("/help");
        assert.ok(await send.isEnabled());
        await input.fill("");
        assert.ok(await send.isDisabled());
        const modePicker = page.locator("#vector-ai-mode");
        const modeOptions = page.locator("#vector-ai-mode-options");
        assert.equal(await modePicker.innerText(), "Залишки та база знань");
        await modePicker.press("Enter");
        assert.deepEqual(await modeOptions.locator("small").allTextContents(), [
            "Залишки, інструкції з ERP і робочі процеси", "Підготувати текст за затвердженими статтями",
        ]);
        await modePicker.press("Escape");
        await modeOptions.waitFor({state: "hidden"});
        const ask = async message => {
            await input.fill(message);
            await page.waitForFunction(() => !document.querySelector('.vector-ai-compose-footer [type="submit"]').disabled);
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
        assert.equal(incidents.length, 0);
        failKnowledge = true;
        await ask("А які умови для іншого товару?");
        assert.equal(calls.at(-1).conversation_id, tokens.knowledge);
        const error = page.locator(".vector-ai-message.is-error").last();
        assert.match(await error.innerText(), /ШІ тимчасово недоступний/);
        await page.waitForFunction(() => Object.keys(localStorage).some(k => k.endsWith("/send:knowledge:provider")));
        await page.waitForTimeout(150);
        await page.evaluate(() => window.dispatchEvent(new Event("online")));
        await page.waitForFunction(() => !Object.keys(localStorage).some(k => k.endsWith("/send:knowledge:provider")));
        const {diagnostics, ...incident} = incidents[0];
        assert.deepEqual(incident, {operation: "send", mode: "knowledge", code: "provider"});
        const snapshot = JSON.parse(diagnostics);
        assert.deepEqual(Object.keys(snapshot).sort(), ["build", "connection", "observed_at", "page", "viewport"]);
        assert.match(snapshot.observed_at, /Z$/);
        assert.match(snapshot.build, /^[a-f0-9]{12}$/);
        assert.match(snapshot.viewport, /^\d+x\d+$/);
        assert.deepEqual(incidents[1], incidents[0]);
        const reportLabel = "Надіслати адміністратору";
        assert.ok(await error.getByRole("button", {name: reportLabel}).isVisible());
        assert.equal(await error.locator("summary").count(), 0);
        assert.equal(await error.locator(".vector-stock-stamp").count(), 0);
        await ask("Чи є DM542 на складі і яка гарантія?");
        assert.equal(await reply.locator(".vector-stock-stamp").count(), 1);
        assert.equal(await reply.getByRole("button", {name: /^Повторити/}).count(), 0);
        assert.ok(await reply.getByRole("button", {name: reportLabel}).isVisible());
        assert.equal(await reply.locator(".vector-ai-follow-up").count(), 0);
        const admin = page.locator(".vector-ai-compose-footer").getByRole("button", {name: "Надіслати адміністратору"});
        assert.ok((await admin.boundingBox()).x < (await send.boundingBox()).x);
        const dialog = page.locator(".modal:visible").filter({has: page.getByRole("radiogroup", {name: "Тип звернення"})});
        const excerpt = dialog.locator('textarea[data-fieldname="context"]');
        const receipt = "MTECH-2";
        const attempts = [];
        let report;
        await page.route("**/api/method/**.vectoria_feedback.submit", route => {
            report = route.request().postDataJSON();
            attempts.push(report);
            return route.fulfill({json: {message: attempts.length === 1 ? "" : receipt}});
        });
        await input.fill("Моя чернетка");
        const inlineReport = error.getByRole("button", {name: reportLabel});
        await inlineReport.press("Enter");
        const popup = page.locator(".modal:visible");
        await popup.getByText(/Не вдалося підтвердити надсилання/).waitFor();
        assert.ok(await inlineReport.isEnabled());
        await popup.locator(".btn-modal-close").click();
        await popup.waitFor({state: "hidden"});
        await inlineReport.click();
        const confirmation = popup.getByRole("status").filter({hasText: receipt});
        const reload = popup.getByRole("button", {name: /^(Оновити|Reload)$/});
        async function checkReceipt() {
            await confirmation.waitFor();
            assert.match(await confirmation.innerText(), /^✓ (Адміністратор отримав ваше звернення|The administrator received your report)\. MTECH-2$/);
            assert.ok(await reload.isEnabled());
        }
        await checkReceipt();
        assert.ok((await popup.innerText()).includes(report.message));
        assert.deepEqual(attempts, [report, report]);
        const sent = error.getByRole("button", {name: "✓ Надіслано адміністратору"});
        assert.ok(await sent.isDisabled());
        await popup.locator(".btn-modal-close").click();
        await popup.waitFor({state: "hidden"});
        assert.equal(await input.inputValue(), "Моя чернетка");
        await input.fill("");
        const disabledStyle = el => {
            const {pointerEvents, backgroundColor, color, boxShadow, cursor} = getComputedStyle(el);
            return {pointerEvents, backgroundColor, color, boxShadow, cursor};
        };
        assert.equal((await sent.evaluate(disabledStyle)).pointerEvents, "none");
        assert.deepEqual(await sent.evaluate(disabledStyle), await send.evaluate(disabledStyle));
        await sent.scrollIntoViewIfNeeded();
        const bounds = await sent.boundingBox();
        await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
        await page.mouse.down();
        assert.equal(await sent.evaluate(el => el.matches(":active") || el.getAnimations().length > 0), false);
        await page.mouse.up();
        assert.equal(attempts.length, 2);
        assert.equal(report.category, "Помилка");
        assert.equal(report.message, await error.locator("p.vector-ai-text").innerText());
        assert.match(report.context, /knowledge · provider$/);
        assert.doesNotMatch(report.context, /DM542|Моя чернетка/);
        await admin.press("Enter");
        const disclosure = dialog.getByText("Контекст для аналізу", {exact: true});
        await disclosure.waitFor();
        assert.equal(await dialog.getByRole("radio").count(), 2);
        assert.ok(await dialog.getByRole("radio", {name: "Пропозиція"}).isChecked());
        assert.ok(await excerpt.isHidden());
        const details = dialog.locator("details");
        await checkDisclosure(details, true);
        await page.evaluate(() => frappe.vector_animations.set(false));
        await checkDisclosure(details, false);
        await page.evaluate(() => frappe.vector_animations.set(true));
        await page.emulateMedia({reducedMotion: "reduce"});
        await checkDisclosure(details, false);
        await page.emulateMedia({reducedMotion: "no-preference"});
        await disclosure.press("Enter");
        assert.match(await excerpt.inputValue(), /DM542/);
        assert.match(await excerpt.inputValue(), /Версія вебчату: [a-f0-9]{12}/);
        assert.match(await excerpt.inputValue(), /Останній збій: .*provider/);
        assert.match(await excerpt.inputValue(), /ШІ тимчасово недоступний/);
        await dialog.locator('textarea[data-fieldname="message"]').fill("Моя пропозиція");
        await excerpt.fill("");
        await disclosure.click();
        await dialog.locator(".btn-modal-primary").click();
        await dialog.waitFor({state: "hidden"});
        await checkReceipt();
        await popup.locator(".btn-modal-close").click();
        await popup.waitFor({state: "hidden"});
        assert.equal(report.message, "Моя пропозиція");
        assert.equal(report.category, "Пропозиція");
        assert.ok(!report.context);
        await admin.click();
        await dialog.getByRole("radio", {name: "Помилка"}).check();
        assert.equal(await dialog.locator('textarea[data-fieldname="message"]').inputValue(), "");
        await dialog.locator(".btn-modal-primary").click();
        await dialog.waitFor({state: "hidden"});
        assert.equal(report.category, "Помилка");
        assert.ok(!report.message);
        assert.match(report.context, /DM542/);
        await checkReceipt();
        await popup.locator(".btn-modal-close").click();
        await popup.waitFor({state: "hidden"});
        failKnowledge = false;
        const quotaMessage = "Залишилося запитань: 0 зі 100 за останні 60 хв.";
        await page.route("**/api/method/warehouse_core.ai_chat.send*", route => {
            retryAfter = 3599;
            return route.fulfill({json: {message: {ok: false, message: quotaMessage}}});
        }, {times: 1});
        await ask("Які умови гарантії?");
        for (const width of [320, 1200]) {
            await page.setViewportSize({width, height: 800});
            await input.fill("/help");
            const idle = await send.boundingBox();
            await input.fill("Чернетка");
            assert.ok(await send.isDisabled());
            assert.equal(await send.innerText(), "Надіслати");
            assert.deepEqual(await send.boundingBox(), idle);
        }
        retryAfter = 4;
        await page.getByRole("button", {name: "Закрити чат"}).click();
        await page.locator("#vector-ai-launcher").click();
        assert.equal(await send.innerText(), "Надіслати");
        const shortWidth = (await send.boundingBox()).width;
        assert.equal(await error.locator("p.vector-ai-text").innerText(), quotaMessage);
        assert.equal(await error.getByRole("button").count(), 0);
        await input.fill("/help");
        assert.ok(await send.isEnabled());
        assert.equal((await send.boundingBox()).width, shortWidth);
        await input.fill("Наступне запитання");
        assert.ok(await send.isDisabled());
        const beforeCountdown = calls.length;
        await input.press("Enter");
        await page.waitForFunction(() => !document.querySelector('.vector-ai-compose-footer [type="submit"]').disabled);
        assert.equal(calls.length, beforeCountdown);
        assert.equal(await input.inputValue(), "Наступне запитання");
        retryAfter = 0;
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
        await filters.waitFor({state: "hidden"});
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
            await modeOptions.waitFor({state: "hidden"});
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
                    await checkDisclosure(followUp, true);
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
        await checkConsecutiveQuestions(browser, base, await page.context().cookies());
        console.log("Passed: consecutive questions, hourly limit, combined mode, live stock, replies, citations, follow-ups, reset, and all quick inputs in both modes. Paid calls: 0.");
    } finally {
        await browser.close();
    }
}
module.exports = {checkNewChat, checkPanelFade};
if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
