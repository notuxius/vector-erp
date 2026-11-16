frappe.pages["knowledge-base"].on_page_load = function (wrapper) {
    const page = frappe.ui.make_app_page({parent: wrapper, title: "База знань", single_column: true});
    const make = (tag, cls, text) => {
        const node = document.createElement(tag); node.className = cls;
        if (text) node.textContent = text; return node;
    };
    const root = make("div", "knowledge-base");
    root.style.cssText = "padding:24px;max-width:1080px;margin:auto";
    const intro = make("p", "text-muted", "Інструкції, товари та внутрішні правила. Тут відображаються лише матеріали, до яких ви маєте доступ.");
    const form = make("form", "");
    form.style.cssText = "display:flex;gap:12px;margin:20px 0";
    const input = make("input", "form-control");
    input.placeholder = "Назва, товар або ключові слова…"; input.maxLength = 4000;
    input.setAttribute("aria-label", "Пошук у базі знань");
    const button = make("button", "btn btn-primary", "Знайти"); button.type = "submit";
    form.append(input, button);
    const state = make("p", "text-muted"); state.setAttribute("role", "status");
    const results = make("div", "knowledge-results");
    root.append(intro, form, state, results); page.main[0].append(root);
    let sequence = 0;
    async function search() {
        const request = ++sequence;
        state.textContent = "Шукаємо…"; results.replaceChildren();
        try {
            const response = await frappe.call({method: "warehouse_core.knowledge.search", type: "GET", args: {query: input.value}});
            if (request !== sequence) return;
            const data = response.message;
            if (data.can_edit) page.set_primary_action("Створити статтю", () => frappe.new_doc("Knowledge Article"), "add");
            state.textContent = data.articles.length ? `Знайдено статей: ${data.articles.length}` : "Статей не знайдено. Спробуйте інші ключові слова або зверніться до видавця бази знань.";
            for (const article of data.articles) {
                const card = make("article", "");
                card.style.cssText = "border:1px solid var(--border-color);border-radius:12px;padding:20px;margin-bottom:14px;background:var(--card-bg)";
                const link = make("a", "", article.title); link.href = article.url;
                link.style.cssText = "font-size:17px;font-weight:600";
                const labels = {Draft: "Чернетка", Review: "На перевірці", Published: "Опубліковано", Archived: "В архіві"};
                card.append(link, make("p", "text-muted", `${article.category} · ${labels[article.status]} · Редакція ${article.revision} · ${article.cloud_allowed ? "Дозволено для ШІ" : "Лише локальний перегляд"}`), make("p", "", article.excerpt));
                results.append(card);
            }
        } catch (_) { if (request === sequence) state.textContent = "Не вдалося завантажити статті. Спробуйте ще раз."; }
    }
    form.onsubmit = event => { event.preventDefault(); search(); };
    wrapper.knowledge_refresh = search;
};
frappe.pages["knowledge-base"].on_page_show = wrapper => wrapper.knowledge_refresh?.();
