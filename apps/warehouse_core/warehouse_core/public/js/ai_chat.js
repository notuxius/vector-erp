// Server-owned context. Stock cards are rendered from ERP data, never model text.
(() => {
    if (!frappe.boot.vector_ai_chat || document.getElementById("vector-ai-launcher")) return;
    const make = (tag, className, text) => {
        const node = document.createElement(tag);
        node.className = className;
        if (text) node.textContent = text;
        return node;
    };
    const assistantName = __("Vectoria ✦ AI-assistant");
    const launcher = make("button", "vector-ai-launcher", assistantName);
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
    footer.append(hint, submit);
    form.append(label, input, footer);
    panel.append(header, modes, state, stockForm, log, form);
    document.body.append(launcher, panel);
    frappe.vector_tooltip?.(close, "Закрити чат");
    let busy = false, available = false, checking = false, optionsReady = false;
    const conversations = new Map();
    let previousMode = null, clarificationSubject = null;
    let warehouseOptions = [];
    function syncMode() {
        const combined = mode.value === "assistant";
        welcome.querySelector(".vector-ai-intro").textContent = combined
            ? "Запитайте про наявність товару, умови гарантії чи порядок дій. Можна поєднати обидва питання в одному повідомленні."
            : "Підготую відповідь клієнту за затвердженими статтями. Перевірте її перед надсиланням.";
        infoTip.set_text(combined
            ? "Кількості обчислює ERP. Для складніших запитань OpenAI отримує запит, попередні фільтри або контекст бази знань із дозволеними уривками."
            : "Пам’ятаю до 5 обмінів протягом 30 хв. Контекст і дозволені уривки надсилаються до OpenAI. Перевіряйте джерела.");
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
    function appendFollowUp(row, contactAdmin) {
        if (contactAdmin) {
            const button = make("button", "btn btn-default btn-sm", "Звернутись до адміністратора");
            button.type = "button";
            button.onclick = () => {
                hide();
                frappe.new_doc("ToDo", {
                    allocated_to: "Administrator", assigned_by: frappe.session.user,
                    description: "Векторія не може відповісти за базою знань. Перевірте, будь ласка, доступність ШІ.",
                });
            };
            row.append(button);
            return;
        }
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
            optionsReady = data?.ok === true;
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
        } catch (_) { optionsReady = false; stockHelp.textContent = "Не вдалося завантажити фільтри. Відкрийте панель знову."; }
        update();
    }
    async function clearChat() {
        busy = true;
        update();
        const tokens = [...conversations.values()];
        conversations.clear();
        previousMode = clarificationSubject = null;
        log.replaceChildren(welcome);
        for (const token of tokens) {
            await frappe.call({method: "warehouse_core.ai_chat.reset", type: "POST",
                args: {conversation_id: token}, silent: true, error: () => {}}).catch(() => {});
        }
        busy = false;
        update();
    }
    function update() {
        const sendUnavailable = busy || (mode.value === "draft" && !available);
        const command = /^\/(usage|help)$/i.test(input.value.trim());
        submit.disabled = busy || (!command && sendUnavailable) || !input.value.trim();
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
            available = response.message?.available === true;
            state.textContent = response.message?.message || "ШІ тимчасово недоступний.";
        } catch (_) {
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
    async function assistantPlan(message, extra) {
        const direct = extra.mode || (mode.value === "draft" ? "draft" : null);
        if (direct) return {requests: [{message, ...extra, mode: direct}]};
        const response = await frappe.call({method: "warehouse_core.ai_chat.route", type: "POST",
            args: {message, previous_mode: previousMode, count_clarification: 1}, silent: true, error: () => {}});
        return response.message;
    }
    function remember(mode, result) {
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
        } catch (_) {
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
    async function sendMessage(message, extra = {}) {
        const stockAction = extra.mode === "availability";
        const usage = !stockAction && /^\/(usage|help)$/i.test(message.trim());
        if (busy || (!usage && !stockAction && mode.value === "draft" && !available) || !message) return;
        busy = true;
        log.querySelectorAll(".vector-ai-follow-up[open]").forEach(picker => { picker.open = false; });
        update();
        addMessage("user", message);
        if (!stockAction) input.value = "";
        const pending = addMessage("pending", "Готую відповідь…");
        let contactAdmin = false;

        try {
            if (usage) {
                if (message.trim().toLowerCase() === "/help") {
                    addMessage("assistant", __("Vectoria uses the articles you can access to explain how to work in Vector ERP. You can also ask about stock. Use /usage to see how many questions you have left and when you can ask again."));
                    return;
                }
                const response = await frappe.call({method: "warehouse_core.ai_chat.usage", type: "POST", silent: true});
                addMessage("assistant", response.message.text);
                return;
            }
            const choice = message.trim().toLowerCase().replace(/[.!?]+$/u, "");
            let question = message;
            if (clarificationSubject && !Object.keys(extra).length) {
                if (["залишки", "залишок", "наявність"].includes(choice)) question = "залишки: " + clarificationSubject;
                if (["характеристики", "база знань"].includes(choice)) question = "знання: " + clarificationSubject + " — характеристики";
            }
            const planned = await assistantPlan(question, extra);
            if (planned.message) {
                addMessage("error", planned.message);
                input.value = message;
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
                const context = Object.fromEntries(requests.filter(q => conversations.has(q.mode))
                    .map(q => [q.mode, conversations.get(q.mode)]));
                const response = await frappe.call({method: "warehouse_core.ai_chat.combined", type: "POST",
                    args: {message: question, previous_mode: previousMode, conversations: JSON.stringify(context)},
                    silent: true, error: () => {}});
                if (!response.message.ok) {
                    addMessage("error", response.message.message);
                    input.value = message;
                    return;
                }
                results = response.message.results;
                results.forEach((result, i) => remember(requests[i].mode, result));
            } else {
                results = [await lookup(requests[0])];
            }
            if (results.some(result => result.reset_conversation)) {
                log.replaceChildren();
                addMessage("user", message);
            }
            contactAdmin = results.some(result => result.contact_admin);
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
                if (!stockAction) input.value = message;
                await refreshStatus();
            }
        } catch (_) {
            addMessage("error", "Не вдалося підготувати відповідь. Повторіть запит.");
            if (!stockAction) input.value = message;
        } finally {
            pending.remove();
            appendFollowUp(log.lastElementChild, contactAdmin);
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
