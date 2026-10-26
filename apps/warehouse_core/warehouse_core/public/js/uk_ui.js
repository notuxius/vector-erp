/* Localized rendering for v16 controls that do not call Frappe's translator.
 * Keep routes, document names, step identity and saved configurations intact.
 */
(() => {
    if (frappe.boot.lang !== "uk") return;

    // Frappe translates Link controls, but not the names/data columns in lists.
    // Translate only configuration labels; never change doc.name, filter values,
    // checkbox identities, routes, or editable controls' underlying values.
    const configurationFields = {
        Warehouse: ["name", "warehouse_name", "parent_warehouse"],
        Domain: ["name", "domain"],
        Department: ["name", "department_name", "parent_department"],
        Designation: ["name", "designation_name"],
        Role: ["name", "role_name"],
        "Role Profile": ["name", "role_profile"],
        "Letter Head": ["name", "letter_head_name"],
        "Print Format": ["name"],
        "Print Style": ["name", "print_style_name"],
        "Price List": ["name", "price_list_name"],
        "Email Template": ["name"],
        "Sales Taxes and Charges Template": ["name", "title"],
    };
    frappe.boot.translated_doctypes = [...new Set([
        ...(frappe.boot.translated_doctypes || []), ...Object.keys(configurationFields),
    ])];
    const list = frappe.views?.ListView?.prototype;
    const columnHtml = list?.get_column_html;
    if (columnHtml && !columnHtml.vectorUkrainian) {
        list.get_column_html = function (column, doc, ...args) {
            const html = columnHtml.call(this, column, doc, ...args);
            const guestName = this.doctype === "User" && doc.name === "Guest" &&
                ["name", "full_name"].includes(column.df?.fieldname) && doc[column.df.fieldname] === "Guest";
            if (!guestName && !configurationFields[this.doctype]?.includes(column.df?.fieldname)) return html;
            const value = doc[column.df.fieldname];
            const translated = typeof value === "string" ? __(value, null, this.doctype) : value;
            if (typeof value !== "string" || translated === value) return html;
            const container = document.createElement("div");
            container.innerHTML = html;
            const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
            while (walker.nextNode()) {
                const node = walker.currentNode;
                if (node.textContent.trim() === value) {
                    node.textContent = node.textContent.replace(value, translated);
                }
            }
            for (const element of container.querySelectorAll("[title]")) {
                const title = element.getAttribute("title");
                if (title === value) element.setAttribute("title", translated);
                else if (title.endsWith(`: ${value}`)) {
                    element.setAttribute("title", title.slice(0, -value.length) + translated);
                }
            }
            return container.innerHTML;
        };
        list.get_column_html.vectorUkrainian = true;
    }

    // User captions bypass the catalog, including avatars and timeline authors.
    const userInfo = frappe.user_info;
    if (userInfo && !userInfo.vectorUkrainian) {
        frappe.user_info = function (uid, ...args) {
            const info = userInfo.call(this, uid, ...args);
            if ((uid || frappe.session.user) !== "Guest" || info.fullname !== "Guest") return info;
            const fullname = __("Guest");
            return { ...info, fullname, abbr: frappe.get_abbr(fullname) };
        };
        frappe.user_info.vectorUkrainian = true;
    }

    // Version history bypasses field formatters. Translate Select and Price List values in
    // a render-only copy, using field metadata so free text is left untouched.
    const footer = frappe.ui.form.Footer?.prototype;
    const makeTimeline = footer?.make_timeline;
    if (makeTimeline && !makeTimeline.vectorUkrainian) {
        footer.make_timeline = function (...args) {
            const result = makeTimeline.apply(this, args);
            const timeline = this.frm.timeline;
            const render = timeline.get_version_timeline_contents;
            timeline.get_version_timeline_contents = function (...renderArgs) {
                const translateChanges = (changes, doctype) => {
                    for (const change of changes || []) {
                        const df = frappe.meta.get_docfield(doctype, change[0], this.frm.docname);
                        const priceList = df?.fieldtype === "Link" && df.options === "Price List";
                        if (!priceList && df?.fieldtype !== "Select") continue;
                        for (const index of [1, 2]) {
                            if (typeof change[index] === "string") {
                                change[index] = __(change[index], null, priceList ? "Price List" : df.parent);
                            }
                        }
                    }
                };
                const versions = (this.doc_info.versions || []).map((version) => {
                    if (!version.data) return version;
                    const data = JSON.parse(version.data);
                    translateChanges(data.changed, this.frm.doctype);
                    for (const row of data.row_changed || []) {
                        const doctype = this.frm.fields_dict[row[0]]?.grid?.doctype;
                        if (doctype) translateChanges(row[3], doctype);
                    }
                    return { ...version, data: JSON.stringify(data) };
                });
                const context = Object.create(this);
                context.doc_info = { ...this.doc_info, versions };
                return render.apply(context, renderArgs);
            };
            return result;
        };
        footer.make_timeline.vectorUkrainian = true;
    }

    const tree = frappe.ui.Tree?.prototype;
    const nodeLabel = tree?.get_node_label;
    if (nodeLabel && !nodeLabel.vectorUkrainian) {
        tree.get_node_label = function (node) {
            if (this.args?.doctype === "Warehouse") {
                const label = __(node.label, null, "Warehouse");
                if (label !== node.label) return frappe.utils.escape_html(label);
            }
            return nodeLabel.call(this, node);
        };
        tree.get_node_label.vectorUkrainian = true;
    }

    // The pinned Air Datepicker bundle has no Ukrainian locale and falls back
    // to English independently of Frappe's translation catalog.
    if ($.fn.datepicker?.language) {
        $.fn.datepicker.language.uk = {
            days: ["Неділя", "Понеділок", "Вівторок", "Середа", "Четвер", "П’ятниця", "Субота"],
            daysShort: ["Нд", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб"],
            daysMin: ["Нд", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб"],
            months: ["Січень", "Лютий", "Березень", "Квітень", "Травень", "Червень",
                "Липень", "Серпень", "Вересень", "Жовтень", "Листопад", "Грудень"],
            monthsShort: ["Січ", "Лют", "Бер", "Кві", "Тра", "Чер",
                "Лип", "Сер", "Вер", "Жов", "Лис", "Гру"],
            today: "Сьогодні",
            clear: "Очистити",
            dateFormat: "dd.mm.yyyy",
            timeFormat: "hh:ii",
            firstDay: 1,
        };
    }

    // These upstream methods render synchronously. Restore the original values
    // before event handlers run, so dismiss/progress keys and routes never change.
    function translateDuringRender(prototype, method, selectObject, fields) {
        const original = prototype?.[method];
        if (!original || original.vectorUkrainian) return;
        const wrapped = function (...args) {
            const object = selectObject.call(this, args);
            if (!object) return original.apply(this, args);
            const saved = fields.map((field) => [field, object[field]]);
            try {
                for (const [field, value] of saved) {
                    if (typeof value === "string") object[field] = __(value);
                }
                return original.apply(this, args);
            } finally {
                for (const [field, value] of saved) object[field] = value;
            }
        };
        wrapped.vectorUkrainian = true;
        prototype[method] = wrapped;
    }

    const dock = frappe.ui.Dock?.prototype;
    translateDuringRender(dock, "make_dock_item", (args) => args[0], ["label"]);

    // Recent-page search translates "<b>Asset</b>" instead of "Asset", so
    // catalog lookup fails. Translate the name before adding display markup.
    const search = frappe.search?.utils;
    for (const method of ["get_recent_pages", "get_frequent_links"]) {
        const original = search?.[method];
        if (!original || original.vectorUkrainian) continue;
        const viewLabels = {
            List: "List", Tree: "Tree", Workspaces: "Workspace", "query-report": "Report",
        };
        search[method] = function (...args) {
            return original.apply(this, args).map((option) => {
                const route = typeof option.route === "string" ? option.route.split("/") : option.route || [];
                const [view, name] = route;
                if (!name) return option;
                // Saved report names may be custom; translate only known catalog entries.
                if (view === "List" && route[2] === "Report" && route[3]) return option;
                if (viewLabels[view]) {
                    const title = __(name);
                    const suffix = __(viewLabels[view]);
                    return {
                        ...option,
                        label: `<b>${frappe.utils.escape_html(title)}</b> ${frappe.utils.escape_html(suffix)}`,
                        value: `${title} ${suffix}`,
                    };
                }
                if (view === "dashboard-view") {
                    const title = `${__("Dashboard")}: ${__(name)}`;
                    return { ...option, label: `<b>${frappe.utils.escape_html(title)}</b>`, value: title };
                }
                return option;
            });
        };
        search[method].vectorUkrainian = true;
    }

    // ControlButton translates its caption but leaves the native hover title
    // in English. Refresh both together, including dynamically relabeled buttons.
    const button = frappe.ui.form.ControlButton?.prototype;
    const setButtonLabel = button?.set_label;
    if (setButtonLabel) {
        button.set_label = function (...args) {
            const result = setButtonLabel.apply(this, args);
            this.$input?.attr("title", __(this.df.title || this.df.label, null, this.df.parent));
            return result;
        };
    }

    const onboarding = frappe.widget?.widget_factory?.onboarding?.prototype;
    for (const method of ["make_step", "make_step_content"]) {
        translateDuringRender(onboarding, method, (args) => args[0], [
            "title", "description", "action_label",
        ]);
    }
    for (const method of ["set_title", "update_progress"]) {
        translateDuringRender(onboarding, method, function () { return this; }, ["title"]);
    }

    // Desk can render the initial dock before app_include_js finishes loading.
    // Rebuild only its visual entries; retain its current app and navigation state.
    const liveDock = frappe.app?.sidebar?.dock;
    if (liveDock?.$items) liveDock.render_entries();
})();
