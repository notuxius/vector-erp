// Server-owned context. Stock cards are rendered from ERP data, never model text.
(() => {
    if (!frappe.boot.vector_ai_chat || document.getElementById("vector-ai-launcher")) return;
    const feedbackApi = "warehouse_core.warehouse_core.doctype.vectoria_feedback.vectoria_feedback.";
    const queueKey = "vectoria-errors:" + encodeURIComponent(frappe.boot.user.name) + "/";
    const scriptUrl = document.currentScript?.src;
    const build = scriptUrl && new URL(scriptUrl).searchParams.get("v");
    function diagnostics() {
        const page = frappe.get_route?.()?.[0];
        return {
            observed_at: new Date().toISOString(),
            build: /^[a-f0-9]{12}$/.test(build) ? build : "",
            viewport: window.innerWidth + "x" + window.innerHeight,
            page: ["Form", "List", "Tree", "Workspaces", "query-report"].includes(page) ? page : "Desk",
            connection: navigator.onLine ? "online" : "offline",
        };
    }
    const queued = new Map();
    let flushing = false, retryTimer;
    try {
        for (const key of Object.keys(localStorage)) if (key.startsWith(queueKey)) {
            const saved = localStorage.getItem(key);
            queued.set(key.slice(queueKey.length), saved === "1" ? "" : saved);
        }
    } catch (_) {}
    const delivered = new Map();
    async function flushErrors() {
        if (flushing || !queued.size || !navigator.onLine) return;
        flushing = true;
        clearTimeout(retryTimer);
        try {
            for (const [key, snapshot] of queued) {
                const [operation, mode, code] = key.split(":");
                const response = await frappe.call({method: feedbackApi + "report_error", type: "POST",
                    args: {operation, mode, code, diagnostics: snapshot}, silent: true, error: () => {}});
                if (!response.message) break;
                queued.delete(key);
                delivered.set(key, Math.floor(Date.now() / 3600000));
                try { localStorage.removeItem(queueKey + key); } catch (_) {}
            }
        } catch (_) {
            // The ERP may be the service that is down. Keep these codes for the next connection.
        } finally {
            flushing = false;
            if (queued.size) retryTimer = setTimeout(flushErrors, 60000);
        }
    }
    let lastIncident;
    function autoReport(operation, code, chatMode = document.getElementById("vector-ai-mode")?.value || "assistant") {
        if (!code) return;
        const key = [operation, chatMode, code].join(":");
        const snapshot = diagnostics();
        lastIncident = `${snapshot.observed_at} · ${operation} · ${chatMode} · ${code}`;
        if (queued.has(key) || delivered.get(key) === Math.floor(Date.now() / 3600000)) return;
        const encoded = JSON.stringify(snapshot);
        queued.set(key, encoded);
        try { localStorage.setItem(queueKey + key, encoded); } catch (_) {}
        void flushErrors();
    }
    function reportFailure(operation, error, chatMode) {
        const status = error?.status;
        if (status === 0) autoReport(operation, "network", chatMode);
        else if (status >= 500 || status === 404 || status === 408) autoReport(operation, String(status), chatMode);
        else if (error instanceof Error || status === 200) autoReport(operation, "response", chatMode);
    }
    window.addEventListener("error", event => {
        if (scriptUrl && event.filename === scriptUrl) autoReport("render", "widget");
    });
    window.addEventListener("online", flushErrors);
    void flushErrors();
    const make = (tag, className, text) => {
        const node = document.createElement(tag);
        node.className = className;
        if (text) node.textContent = text;
        return node;
    };
    const assistantName = __("Vectoria ✦ AI-assistant");
    const launcher = make("button", "vector-ai-launcher");
    const sparkle = make("span", "vector-ai-launcher-sparkle", "✦");
    sparkle.setAttribute("aria-hidden", "true");
    launcher.append(sparkle, make("span", "vector-ai-launcher-label", assistantName));
    launcher.id = "vector-ai-launcher";
    launcher.type = "button";
    launcher.setAttribute("aria-haspopup", "dialog");
    launcher.setAttribute("aria-expanded", "false");
    launcher.setAttribute("aria-controls", "vector-ai-panel");
    const panel = make("section", "vector-ai-panel");
    panel.id = "vector-ai-panel";
    panel.hidden = true;
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-labelledby", "vector-ai-heading");
    const header = make("div", "vector-ai-header");
    const heading = make("h2", "vector-ai-heading", assistantName);
    heading.id = "vector-ai-heading";
    const reset = frappe.ui.button({label: "Новий чат"})[0];
    const info = frappe.ui.button({
        icon: "info", css_class: "vector-ai-info", attrs: {"aria-label": "Як помічник обробляє дані"},
    })[0];
    const infoTip = new frappe.ui.Tooltip(info, {side: "bottom", align: "end", text_align: "start"});
    info.onclick = () => infoTip.show();
    const close = frappe.ui.button({
        icon: "x", variant: "ghost", css_class: "vector-ai-close", attrs: {"aria-label": "Закрити чат"},
    })[0];
    header.append(heading, info, reset, close);
    const modes = make("div", "vector-ai-modes");
    const picker = make("div", "vector-ai-mode-picker");
    const modeLabel = make("label", "vector-ai-mode-label", "Режим помічника");
    modeLabel.id = "vector-ai-mode-label";
    modeLabel.htmlFor = "vector-ai-mode";
    const mode = make("button", "vector-ai-mode-trigger");
    mode.type = "button";
    mode.id = "vector-ai-mode";
    mode.value = "assistant";
    mode.setAttribute("aria-labelledby", "vector-ai-mode-label vector-ai-mode");
    const menu = make("div", "vector-ai-mode-menu");
    menu.id = "vector-ai-mode-options";
    mode.setAttribute("aria-controls", menu.id);
    function expandModes(open) {
        menu.hidden = !open;
        mode.setAttribute("aria-expanded", String(open));
    }
    for (const [value, name, description] of [
        ["assistant", "Залишки та база знань", "Залишки, інструкції з ERP і робочі процеси"],
        ["draft", "Чернетка відповіді клієнту", "Підготувати текст за затвердженими статтями"],
    ]) {
        const option = make("button", "vector-ai-mode-option");
        option.type = "button";
        option.value = value;
        option.append(make("strong", "", name), make("small", "", description));
        option.setAttribute("aria-pressed", String(value === mode.value));
        const caption = make("span", "", name);
        caption.setAttribute("aria-hidden", String(value !== mode.value));
        mode.append(caption);
        option.onclick = async () => {
            expandModes(false);
            mode.focus();
            if (busy || value === mode.value) return;
            mode.value = value;
            for (const item of mode.children) item.setAttribute("aria-hidden", String(item !== caption));
            for (const item of menu.children) item.setAttribute("aria-pressed", String(item === option));
            await clearChat();
            syncMode();
            update();
            if (!panel.hidden && document.activeElement === document.body) mode.focus();
        };
        menu.append(option);
    }
    mode.onclick = () => expandModes(menu.hidden);
    expandModes(false);
    picker.append(mode, menu);
    picker.addEventListener("focusout", event => {
        if (!picker.contains(event.relatedTarget)) expandModes(false);
    });
    document.addEventListener("pointerdown", event => {
        if (!menu.hidden && !picker.contains(event.target)) expandModes(false);
        if (!info.contains(event.target)) infoTip.hide();
    });
    const library = make("a", "", "База знань"); library.href = "/desk/knowledge-base";
    const filterToggle = make("button", "vector-stock-toggle", "Фільтри залишків");
    filterToggle.type = "button";
    filterToggle.setAttribute("aria-expanded", "false");
    const modeActions = make("div", "vector-ai-mode-actions");
    modeActions.append(filterToggle, library);
    modes.append(modeLabel, picker, modeActions);
    const state = make("div", "vector-ai-state", "Перевіряємо доступність…");
    state.setAttribute("role", "status");
    const stockForm = make("div", "vector-stock-filters");
    stockForm.id = "vector-stock-filters";
    filterToggle.setAttribute("aria-controls", stockForm.id);
    function setFiltersExpanded(expanded) {
        stockForm.hidden = !expanded;
        filterToggle.setAttribute("aria-expanded", String(expanded));
    }
    setFiltersExpanded(false);
    filterToggle.onclick = () => setFiltersExpanded(stockForm.hidden);
    const stockFields = make("div", "vector-stock-fields");
    const controls = {};
    for (const [key, caption] of [["query", "Назва або артикул"], ["company", "Компанія"],
        ["warehouse", "Склад"], ["item_group", "Група товарів"], ["stock_state", "Показати"]]) {
        const wrapper = make("label", "", caption);
        const field = make(key === "query" ? "input" : "select", "form-control");
        field.id = `vector-stock-${key}`;
        field.setAttribute("aria-label", caption);
        if (key === "query") { field.type = "search"; field.maxLength = 140; }
        wrapper.append(field); stockFields.append(wrapper); controls[key] = field;
    }
    function choices(field, rows, empty) {
        field.replaceChildren();
        if (empty) { const option = make("option", "", empty); option.value = ""; field.append(option); }
        rows.forEach(([value, label]) => { const option = make("option", "", label); option.value = value; field.append(option); });
    }
    choices(controls.company, [], "Усі доступні компанії");
    choices(controls.warehouse, [], "Усі доступні склади");
    choices(controls.item_group, [], "Усі доступні групи");
    choices(controls.stock_state, [["in_stock", "Є фізично"], ["free", "Є вільний залишок"],
        ["production", "Є виробнича потреба"], ["all", "Усі залишки, включно з нульовими"]]);
    const stockSearch = make("button", "btn btn-default btn-sm", "Показати залишки"); stockSearch.type = "button";
    const stockHelp = make("p", "vector-stock-help", "Показуємо облік ERP. Тимчасовий каталог MTech не є джерелом залишків.");
    stockForm.append(stockFields, stockSearch, stockHelp);
    const log = make("div", "vector-ai-messages");
    log.setAttribute("role", "log");
    log.setAttribute("aria-label", "Повідомлення чату");
    log.setAttribute("aria-live", "polite");
    const welcome = make("div", "vector-ai-welcome");
    welcome.append(make("div", "vector-ai-welcome-mark"),
        make("h3", "", "З чим допомогти?"),
        make("p", "vector-ai-intro", ""));
    const suggestions = make("div", "vector-ai-suggestions");
    welcome.append(suggestions);
    log.append(welcome);
    const form = make("form", "vector-ai-form");
    const label = make("label", "sr-only", "Ваше повідомлення");
    label.htmlFor = "vector-ai-input";
    const input = make("textarea", "form-control vector-ai-input");
    input.id = "vector-ai-input";
    input.rows = 3;
    input.maxLength = 4000;
    const footer = make("div", "vector-ai-compose-footer");
    const hint = make("span", "", "Enter — надіслати");
    hint.append(document.createElement("br"), "Ctrl+Enter — новий рядок");
    const submit = make("button", "btn btn-primary btn-sm", "Надіслати");
    submit.type = "submit";
    const feedback = make("button", "btn btn-default btn-sm", "Адміністратору");
    feedback.type = "button";
    feedback.setAttribute("aria-label", "Надіслати адміністратору");
    feedback.onclick = () => openFeedback();
    footer.append(hint, feedback, submit);
    form.append(label, input, footer);
    panel.append(header, modes, state, stockForm, log, form);
    document.body.append(launcher, panel);
    frappe.vector_tooltip?.(close, "Закрити чат");
    let busy = false, available = false, checking = false, optionsReady = false;
    const conversations = new Map();
    let previousMode = null, clarificationSubject = null;
    let warehouseOptions = [];
    function openFeedback() {
        if (busy) return;
        const original = input.value;
        const rows = [...log.querySelectorAll(".vector-ai-message:not([data-private])")].slice(-6);
        const excerpt = rows.map(row => [row.dataset.time, ...[...row.querySelectorAll(".vector-ai-speaker, .vector-ai-text, .vector-ai-sources a")]
            .map(el => el.textContent.trim())].filter(Boolean).join("\n").slice(0, 1100)).join("\n\n");
        const env = diagnostics();
        const stock = rows.findLast(row => row.querySelector(".vector-stock-stamp"));
        const context = ["Сторінка: " + location.pathname.split("/").slice(0, 3).join("/"),
            "Режим: " + mode.querySelector('[aria-hidden="false"]').textContent,
            `Час (UTC): ${env.observed_at} · Екран: ${env.viewport} · З’єднання браузера: ${env.connection}`,
            env.build && "Версія вебчату: " + env.build, lastIncident && "Останній збій: " + lastIncident,
            stock && [".vector-stock-stamp", ".vector-stock-scope"].map(s => stock.querySelector(s)?.textContent).filter(Boolean).join("\n"),
            excerpt].filter(Boolean).join("\n\n");
        const requestId = [...crypto.getRandomValues(new Uint8Array(16))].map(n => n.toString(16).padStart(2, "0")).join("");
        const dialog = new frappe.ui.Dialog({
            title: "Надіслати адміністратору",
            fields: [
                {fieldname: "message", fieldtype: "Small Text", label: "Повідомлення", reqd: 1,
                    default: original},
                {fieldname: "review", fieldtype: "HTML", options: `
                    <div class="d-flex mb-3" role="radiogroup" aria-label="Тип звернення">
                        <label class="mr-3"><input type="radio" name="${requestId}" value="Пропозиція" checked> Пропозиція</label>
                        <label><input type="radio" name="${requestId}" value="Помилка"> Помилка</label>
                    </div>
                    <details><summary>Контекст для аналізу</summary>
                        <textarea class="form-control mt-2" data-fieldname="context" aria-label="Контекст для аналізу" rows="4" maxlength="8000"></textarea>
                    </details>
                    <p class="text-muted small mt-2">Адміністратор і Codex отримають повідомлення та доданий контекст. Контекст можна змінити або видалити.</p>`},
            ],
            primary_action_label: "Надіслати адміністратору",
            primary_action: async values => {
                if (!values.message.trim()) { frappe.msgprint("Введіть повідомлення."); return; }
                dialog.disable_primary_action();
                try {
                    const response = await frappe.call({method: feedbackApi + "submit",
                        type: "POST", args: {message: values.message, category: review.querySelector("input:checked").value,
                            context: contextInput.value, request_id: requestId}, silent: true, error: () => {}});
                    if (!response.message) throw new Error();
                    frappe.show_alert({message: "Надіслано адміністратору: " + response.message, indicator: "green"});
                } catch (error) {
                    reportFailure("feedback", error);
                    frappe.msgprint("Не вдалося підтвердити надсилання. Ваш текст залишився у формі. Спробуйте ще раз.");
                    return;
                } finally {
                    dialog.enable_primary_action();
                }
                if (input.value === original) input.value = "";
                update();
                dialog.hide();
            },
        });
        dialog.fields_dict.message.$input.attr("maxlength", 4000);
        const review = dialog.fields_dict.review.wrapper;
        const contextInput = review.querySelector("textarea");
        contextInput.value = context.slice(0, 8000);
        dialog.show();
    }
    function syncMode() {
        const combined = mode.value === "assistant";
        welcome.querySelector(".vector-ai-intro").textContent = combined
            ? "Запитайте про наявність товару, умови гарантії чи порядок дій. Можна поєднати обидва питання в одному повідомленні."
            : "Підготую відповідь клієнту за затвердженими статтями. Перевірте її перед надсиланням.";
        infoTip.set_text((combined
            ? "Кількості обчислює ERP. Для складніших запитань OpenAI отримує запит, попередні фільтри або контекст бази знань із дозволеними уривками."
            : "Пам’ятаю до 5 обмінів протягом 30 хв. Контекст і дозволені уривки надсилаються до OpenAI. Перевіряйте джерела.")
            + " Звіт про збій містить операцію, режим, код помилки, час, версію вебчату, тип сторінки, розмір вікна й стан з’єднання.");
        input.placeholder = combined ? "Запитайте про товар або роботу у Вектор ERP" : "Запит на чернетку або /usage для лімітів";
        renderSuggestions(suggestions);
        state.textContent = combined ? (available ? "Готова допомогти" : "ШІ недоступний. Артикул і фільтри можна перевірити без ШІ.")
            : available ? "Готова допомогти" : "ШІ тимчасово недоступний.";
    }
    function renderSuggestions(list) {
        list.replaceChildren();
        const prompts = (frappe.boot.vector_ai_quick_inputs || []).filter(
            prompt => (prompt.kind === "draft") === (mode.value === "draft")
        );
        prompts.forEach(({label, question, kind}) => {
            const button = make("button", "vector-ai-suggestion", label); button.type = "button";
            button.setAttribute("aria-label", question);
            button.onclick = () => sendMessage(question, kind === "knowledge" ? {mode: "knowledge"} : {});
            list.append(button);
        });
    }
    function appendRetry(row, retry) {
        const action = make("div", "vector-ai-retry-action");
        const button = make("button", "btn btn-default btn-sm vector-ai-retry", "Повторити");
        const countdown = make("small", "vector-ai-retry-countdown", "10");
        countdown.setAttribute("role", "timer");
        countdown.setAttribute("aria-label", "Секунд до повторної спроби");
        button.type = "button";
        button.disabled = true;
        button.dataset.waiting = "true";
        button.onclick = () => {
            if (busy || button.disabled) return;
            action.remove();
            retry();
        };
        action.append(button, countdown);
        row.append(action);
        const readyAt = Date.now() + 10000;
        const timer = setInterval(() => {
            const seconds = Math.ceil((readyAt - Date.now()) / 1000);
            if (seconds > 0 && action.isConnected) {
                countdown.textContent = String(seconds);
                return;
            }
            clearInterval(timer);
            countdown.remove();
            delete button.dataset.waiting;
            button.disabled = busy;
        }, 1000);
    }
    function appendFollowUp(row) {
        if (!suggestions.childElementCount) return;
        const picker = make("details", "vector-ai-follow-up");
        picker.name = "vector-ai-follow-ups";
        const summary = make("summary", "", mode.value === "draft" ? "Готові звернення" : "Готові запитання");
        const list = make("div", "vector-ai-suggestions");
        picker.append(summary, list);
        picker.ontoggle = () => {
            if (picker.open) {
                renderSuggestions(list);
                update();
            } else list.replaceChildren();
        };
        row.append(picker);
    }
    function warehouseChoices() {
        choices(controls.warehouse, warehouseOptions.filter(w => !controls.company.value || w.company === controls.company.value)
            .map(w => [w.name, w.name]), "Усі доступні склади");
    }
    controls.company.onchange = warehouseChoices;
    async function loadOptions() {
        try {
            const response = await frappe.call({method: "warehouse_core.availability.options", type: "GET", silent: true, error: () => {}});
            const data = response.message;
            if (typeof data?.ok !== "boolean") throw new Error("Invalid stock options");
            optionsReady = data.ok;
            if (optionsReady) {
                const old = Object.fromEntries(Object.entries(controls).map(([k, v]) => [k, v.value]));
                choices(controls.company, data.companies.map(v => [v, v]), "Усі доступні компанії");
                controls.company.value = data.companies.includes(old.company) ? old.company : "";
                warehouseOptions = data.warehouses; warehouseChoices();
                if ([...controls.warehouse.options].some(o => o.value === old.warehouse)) controls.warehouse.value = old.warehouse;
                choices(controls.item_group, data.groups.map(v => [v, v]), "Усі доступні групи");
                if (data.groups.includes(old.item_group)) controls.item_group.value = old.item_group;
                stockHelp.textContent = "Лише доступні склади, без транзиту й браку. Дані — з ERP, не з тимчасового каталогу.";
            } else { stockHelp.textContent = data?.message || "Немає доступу до залишків."; }
        } catch (error) { reportFailure("options", error); optionsReady = false; stockHelp.textContent = "Не вдалося завантажити фільтри. Відкрийте панель знову."; }
        update();
    }
    async function clearChat() {
        lastIncident = null;
        busy = true;
        update();
        const tokens = [...conversations.values()];
        conversations.clear();
        previousMode = clarificationSubject = null;
        log.replaceChildren(welcome);
        for (const token of tokens) {
            await frappe.call({method: "warehouse_core.ai_chat.reset", type: "POST",
                args: {conversation_id: token}, silent: true, error: () => {}}).catch(error => reportFailure("reset", error));
        }
        busy = false;
        update();
    }
    function update() {
        const sendUnavailable = busy || (mode.value === "draft" && !available);
        const command = /^\/(usage|help)$/i.test(input.value.trim());
        submit.disabled = busy || (!command && sendUnavailable) || !input.value.trim();
        feedback.disabled = busy;
        log.querySelectorAll(".vector-ai-retry").forEach(button => { button.disabled = busy || button.dataset.waiting === "true"; });
        log.querySelectorAll(".vector-ai-suggestion").forEach(button => { button.disabled = sendUnavailable; });
        reset.disabled = busy;
        input.disabled = busy;
        mode.disabled = busy;
        stockSearch.disabled = busy || !optionsReady;
        Object.values(controls).forEach(control => { control.disabled = busy || !optionsReady; });
        log.querySelectorAll(".vector-stock-more").forEach(button => { button.disabled = busy || button.dataset.expired === "true"; });
        form.setAttribute("aria-busy", String(busy));
    }
    async function refreshStatus() {
        if (checking) return;
        checking = true;
        try {
            const response = await frappe.call({method: "warehouse_core.ai_chat.status", type: "GET", silent: true, error: () => {}});
            if (typeof response.message?.available !== "boolean") throw new Error("Invalid status");
            autoReport("status", response.message.incident);
            available = response.message?.available === true;
            state.textContent = response.message?.message || "ШІ тимчасово недоступний.";
        } catch (error) {
            reportFailure("status", error);
            available = false;
            state.textContent = "Не вдалося перевірити з’єднання. Відкрийте чат ще раз.";
        } finally {
            checking = false;
            syncMode();
            state.classList.toggle("is-unavailable", !available);
            update();
        }
    }
    function hide() {
        infoTip.hide();
        expandModes(false);
        panel.hidden = true;
        launcher.hidden = false;
        launcher.setAttribute("aria-expanded", "false");
        launcher.focus();
    }
    launcher.onclick = () => {
        panel.style.setProperty("--launcher-width", launcher.offsetWidth + "px");
        panel.hidden = false;
        launcher.hidden = true;
        launcher.setAttribute("aria-expanded", "true");
        input.focus();
        refreshStatus();
        loadOptions();
    };
    close.onclick = hide;
    panel.addEventListener("keydown", event => {
        if (event.key !== "Escape") return;
        event.stopPropagation();
        if (menu.hidden) hide();
        else { expandModes(false); mode.focus(); }
    });
    reset.onclick = async () => {
        if (busy) return;
        input.value = "";
        await clearChat();
        refreshStatus();
        input.focus();
    };
    function addMessage(kind, text) {
        welcome.remove();
        const row = make("div", `vector-ai-message is-${kind}`);
        row.dataset.time = new Date().toISOString();
        row.append(make("div", "vector-ai-speaker", kind === "user" ? "Ви" : kind === "assistant" ? assistantName : "Повідомлення"),
            make("div", "vector-ai-text", text));
        log.append(row);
        log.scrollTop = log.scrollHeight;
        return row;
    }
    input.addEventListener("input", update);
    input.addEventListener("keydown", event => {
        if (event.key !== "Enter" || event.isComposing) return;
        event.preventDefault();
        if (event.ctrlKey) {
            const {selectionStart, selectionEnd} = input;
            if (input.value.length - (selectionEnd - selectionStart) < input.maxLength) {
                input.setRangeText("\n", selectionStart, selectionEnd, "end");
                input.dispatchEvent(new Event("input", {bubbles: true}));
            }
        } else if (!event.shiftKey && !submit.disabled) {
            form.requestSubmit();
        }
    });
    function renderStock(row, data) {
        row.append(make("p", "vector-stock-stamp", `З ERP · ${data.checked_at} · ${data.timezone}`));
        const labels = {query: "Пошук", company: "Компанія", warehouse: "Склад", item_group: "Група", stock_state: "Вид"};
        const states = {in_stock: "є фізично", free: "є вільний залишок", all: "усі залишки", production: "виробнича потреба"};
        row.append(make("p", "vector-stock-scope", Object.entries(data.filters).filter(([, v]) => v)
            .map(([k, v]) => `${labels[k]}: ${k === "stock_state" ? states[v] : v}`).join(" · ")));
        const format = value => new Intl.NumberFormat("uk-UA", {maximumFractionDigits: 9}).format(value);
        data.rows.forEach(item => {
            const card = make("article", "vector-stock-card");
            const unit = __(item.stock_uom);
            const link = make("a", "vector-stock-name", item.item_name); link.href = "/desk/item/" + encodeURIComponent(item.item_code);
            card.append(link, make("p", "vector-stock-location", `${item.item_code} · ${item.company} · ${item.warehouse}`));
            const metrics = make("dl", "vector-stock-metrics");
            for (const [key, label] of [["actual_qty", "Фізично"], ["reserved_stock", "Фізичний резерв"], ["free_qty", "Вільно від резервів"]]) {
                const cell = make("div", ""); cell.append(make("dt", "", label), make("dd", "", `${format(item[key])} ${unit}`)); metrics.append(cell);
            }
            card.append(metrics, make("p", "vector-stock-status", item.status));
            const details = make("details", "vector-stock-demand"); details.append(make("summary", "", "Потреби та очікувані закупівлі"));
            for (const [key, label] of [["production_demand", "Виробничі замовлення"], ["planned_demand", "Виробничі плани"],
                ["sales_demand", "Замовлення покупців"], ["subcontract_demand", "Субпідряд"], ["ordered_qty", "Очікується за закупівлями"]]) {
                details.append(make("p", "", `${label}: ${format(item[key])} ${unit}`));
            }
            card.append(details); row.append(card);
        });
        row.append(make("p", "vector-stock-help", data.notice));
        if (data.has_more) {
            const more = make("button", "btn btn-default btn-sm vector-stock-more", "Показати ще"); more.type = "button";
            more.onclick = () => sendMessage("Показати ще", {mode: "availability", next_page: 1}); row.append(more);
        }
        const refresh = make("button", "btn btn-default btn-sm vector-stock-refresh", "Оновити ці залишки"); refresh.type = "button";
        refresh.onclick = () => sendMessage("Оновити залишки", {mode: "availability", availability_filters: data.filters});
        row.append(refresh);
    }
    async function assistantPlan(message, extra, routingMode) {
        const direct = extra.mode || (mode.value === "draft" ? "draft" : null);
        if (direct) return {requests: [{message, ...extra, mode: direct}]};
        const response = await frappe.call({method: "warehouse_core.ai_chat.route", type: "POST",
            args: {message, previous_mode: routingMode, count_clarification: 1}, silent: true, error: () => {}});
        return response.message;
    }
    function remember(mode, result) {
        if (typeof result?.ok !== "boolean" || (result.ok && typeof result.text !== "string")) throw new Error("Invalid chat reply");
        if (result.reset_conversation) conversations.delete(mode);
        if (result.conversation_id) conversations.set(mode, result.conversation_id);
    }
    async function lookup(args) {
        const token = conversations.get(args.mode);
        try {
            const response = await frappe.call({method: "warehouse_core.ai_chat.send", type: "POST",
                args: {...args, ...(token ? {conversation_id: token} : {})}, silent: true, error: () => {}});
            const result = response.message;
            remember(args.mode, result);
            return result;
        } catch (error) {
            reportFailure("send", error, args.mode);
            return {ok: false, message: "З’єднання перервано. Запит не повторювався автоматично."};
        }
    }
    function renderSources(row, sources) {
        if (!sources?.length) return;
        const list = make("div", "vector-ai-sources");
        list.append(make("strong", "", "Джерела"));
        sources.forEach((source, index) => {
            const link = make("a", "", "[" + (index + 1) + "] " + source.title + " · редакція " + source.revision);
            link.href = "/desk/knowledge-article/" + encodeURIComponent(source.name);
            list.append(link);
        });
        row.append(list);
    }
    async function sendMessage(message, extra = {}, retryContext) {
        const stockAction = extra.mode === "availability";
        const usage = !stockAction && /^\/(usage|help)$/i.test(message.trim());
        if (busy || (!retryContext && !usage && !stockAction && mode.value === "draft" && !available) || !message) return;
        busy = true;
        log.querySelectorAll(".vector-ai-follow-up[open]").forEach(picker => { picker.open = false; });
        update();
        addMessage("user", message);
        const useComposer = !stockAction && (!retryContext || !input.value || input.value === message);
        if (useComposer) input.value = "";
        const pending = addMessage("pending", "Готую відповідь…");
        let question = retryContext?.question ?? message;
        const routingMode = retryContext ? retryContext.routingMode : previousMode;
        let failed = false;
        let operation = usage ? "usage" : "route";

        try {
            if (usage) {
                if (message.trim().toLowerCase() === "/help") {
                    addMessage("assistant", __("Vectoria uses the articles you can access to explain how to work in Vector ERP. You can also ask about stock. Use /usage to see how many questions you have left and when you can ask again."));
                    return;
                }
                const response = await frappe.call({method: "warehouse_core.ai_chat.usage", type: "POST", silent: true});
                if (typeof response.message?.text !== "string") throw new Error("Invalid usage response");
                for (const code of response.message.incidents || []) autoReport("usage", code);
                addMessage("assistant", response.message.text).dataset.private = "usage";
                return;
            }
            const choice = message.trim().toLowerCase().replace(/[.!?]+$/u, "");
            if (!retryContext && clarificationSubject && !Object.keys(extra).length) {
                if (["залишки", "залишок", "наявність"].includes(choice)) question = "залишки: " + clarificationSubject;
                if (["характеристики", "база знань"].includes(choice)) question = "знання: " + clarificationSubject + " — характеристики";
            }
            const planned = await assistantPlan(question, extra, routingMode);
            if (planned.message) {
                addMessage("error", planned.message);
                if (useComposer) input.value = message;
                return;
            }
            const {requests, clarification} = planned;
            if (!requests.length) {
                clarificationSubject = message;
                const row = addMessage("assistant", clarification);
                for (const [label, prompt] of [["Залишок", "залишки: " + message],
                    ["Характеристики", "знання: " + message + " — характеристики"]]) {
                    const button = make("button", "btn btn-default btn-sm", label);
                    button.type = "button";
                    button.onclick = () => sendMessage(prompt);
                    row.append(button);
                }
                return;
            }
            clarificationSubject = null;
            let results;
            if (requests.length === 2) {
                operation = "combined";
                const context = Object.fromEntries(requests.filter(q => conversations.has(q.mode))
                    .map(q => [q.mode, conversations.get(q.mode)]));
                const response = await frappe.call({method: "warehouse_core.ai_chat.combined", type: "POST",
                    args: {message: question, previous_mode: routingMode, conversations: JSON.stringify(context)},
                    silent: true, error: () => {}});
                if (typeof response.message?.ok !== "boolean") throw new Error("Invalid combined reply");
                if (!response.message.ok) {
                    addMessage("error", response.message.message);
                    if (useComposer) input.value = message;
                    return;
                }
                results = response.message.results;
                if (results?.length !== requests.length) throw new Error("Missing combined reply");
                results.forEach((result, i) => remember(requests[i].mode, result));
            } else {
                operation = "send";
                results = [await lookup(requests[0])];
            }
            results.forEach((result, i) => autoReport(operation, result.incident, requests[i].mode));
            if (results.some(result => result.reset_conversation)) {
                log.replaceChildren();
                addMessage("user", message);
            }
            failed = results.some(result => !result.ok);
            const succeeded = results.some(result => result.ok);
            if (succeeded) previousMode = requests[results.findLastIndex(result => result.ok)].mode;
            const row = addMessage(succeeded ? "assistant" : "error", "");
            results.forEach((result, index) => {
                if (results.length > 1 || requests[index].mode === "knowledge") {
                    row.append(make("strong", "", requests[index].mode === "availability" ? "Товари та залишки" : "База знань"));
                }
                row.append(make("p", "vector-ai-text", result.ok ? result.text : result.message || "Не вдалося отримати відповідь."));
                if (result.ok) renderSources(row, result.sources);
            });
            const stock = results.find(result => result.ok && result.availability);
            if (stock) {
                log.querySelectorAll(".vector-stock-more").forEach(button => { button.dataset.expired = "true"; button.disabled = true; });
                renderStock(row, stock.availability);
            }
            log.scrollTop = row.offsetTop - log.offsetTop;
            if (!succeeded) {
                if (useComposer) input.value = message;
                void refreshStatus();
            } else if (retryContext && !available) await refreshStatus();
        } catch (error) {
            reportFailure(operation, error);
            addMessage("error", "Не вдалося підготувати відповідь. Повторіть запит.");
            if (useComposer) input.value = message;
        } finally {
            pending.remove();
            const row = log.lastElementChild;
            if (failed || row.classList.contains("is-error")) {
                appendRetry(row, () => sendMessage(message, extra, {question, routingMode}));
            } else appendFollowUp(row);
            busy = false;
            update();
            if (!panel.hidden) input.focus();
        }
    }
    form.onsubmit = event => { event.preventDefault(); sendMessage(input.value.trim()); };
    stockSearch.onclick = () => {
        setFiltersExpanded(false);
        filterToggle.focus();
        return sendMessage("Пошук залишків за фільтрами", {
            mode: "availability",
            availability_filters: Object.fromEntries(Object.entries(controls).map(([key, field]) => [key, field.value]))
        });
    };
    syncMode();
    update();
})();
