/* Domestic Nova Poshta workflows. API keys and carrier requests stay server-side. */
(() => {
    const method = "warehouse_core.nova_poshta.";
    const group = "Нова пошта";
    const staff = () => ["Stock User", "Stock Manager", "System Manager"].some(role => frappe.user.has_role(role));
    const saved = frm => {
        if (frm.is_new() || frm.is_dirty()) {
            frappe.msgprint("Спочатку збережіть документ.");
            return false;
        }
        return true;
    };
    async function pick(frm, kind, title, fields, extra = {}) {
        if (!saved(frm)) return;
        let rows = [], page = 1;
        const context = frm.doctype === "Nova Poshta Settings" ? {company: frm.doc.company} : {delivery_note: frm.doc.delivery_note};
        const dialog = new frappe.ui.Dialog({title, fields: [
            {fieldname: "query", label: "Пошук за назвою або номером", fieldtype: "Data"},
            {fieldname: "search", label: "Знайти", fieldtype: "Button", click: () => {page = 1; search();}},
            {fieldname: "choice", label: "Результат", fieldtype: "Select", options: [], reqd: 1},
            {fieldname: "next", label: "Наступна сторінка", fieldtype: "Button", click: () => {page += 1; search();}},
        ], primary_action_label: "Вибрати", primary_action: async values => {
            const row = rows.find(item => item.value === values.choice);
            if (!row) return;
            await frm.set_value(fields.ref, row.value);
            await frm.set_value(fields.label, row.label);
            if (fields.phone && row.phone) await frm.set_value(fields.phone, row.phone);
            for (const field of fields.clear || []) await frm.set_value(field, "");
            dialog.hide();
        }});
        async function search() {
            const response = await frappe.call({method: method + "lookup", args: {
                ...context, ...extra, kind, page, query: dialog.get_value("query") || ""
            }, freeze: true});
            rows = response.message || [];
            dialog.set_df_property("choice", "options", rows.map(row => ({value: row.value, label: frappe.utils.escape_html(row.label)})));
            dialog.set_value("choice", rows.length ? rows[0].value : "");
            if (!rows.length) frappe.show_alert("Нічого не знайдено. Уточніть пошук.");
        }
        dialog.show();
        if (kind === "senders" || kind === "contacts") await search();
    }
    function locationButtons(frm, prefix) {
        frm.add_custom_button("Вибрати місто", () => pick(frm, "cities", "Місто", {
            ref: prefix + "_city", label: prefix + "_city_name",
            clear: [prefix + "_warehouse", prefix + "_warehouse_name"]
        }), group);
        frm.add_custom_button("Вибрати відділення", () => {
            if (!frm.doc[prefix + "_city"]) return frappe.msgprint("Спочатку виберіть місто та збережіть документ.");
            pick(frm, "warehouses", "Відділення", {
                ref: prefix + "_warehouse", label: prefix + "_warehouse_name"
            }, {city: frm.doc[prefix + "_city"]});
        }, group);
    }
    frappe.ui.form.on("Nova Poshta Settings", {refresh(frm) {
        if (!frappe.user.has_role("System Manager")) return;
        frm.set_intro("Збережіть компанію, API-ключ і прапорець «Увімкнено». Далі виберіть відправника, контакт, місто та відділення через меню «Нова пошта».");
        frm.add_custom_button("Вибрати відправника", () => pick(frm, "senders", "Відправник", {
            ref: "sender_ref", label: "sender_name", clear: ["sender_contact", "sender_contact_name", "sender_phone"]
        }), group);
        frm.add_custom_button("Вибрати контакт", () => {
            if (!frm.doc.sender_ref) return frappe.msgprint("Спочатку виберіть і збережіть відправника.");
            pick(frm, "contacts", "Контакт відправника", {
                ref: "sender_contact", label: "sender_contact_name", phone: "sender_phone"
            }, {counterparty: frm.doc.sender_ref});
        }, group);
        locationButtons(frm, "sender");
    }});
    frappe.ui.form.on("Delivery Note", {refresh(frm) {
        if (frm.is_new() || !staff() || !frm.perm[0]?.write) return;
        frm.add_custom_button("Відправлення", async () => {
            if (!saved(frm)) return;
            const result = await frappe.db.get_list("Nova Poshta Shipment", {
                filters: {delivery_note: frm.doc.name}, fields: ["name"], limit: 1
            });
            if (result.length) return frappe.set_route("Form", "Nova Poshta Shipment", result[0].name);
            if (frm.doc.docstatus !== 1 || frm.doc.is_return || frm.doc.custom_nova_poshta_ttn)
                return frappe.msgprint("Для нової ТТН потрібне проведене відвантаження без ТТН, яке не є поверненням.");
            frappe.new_doc("Nova Poshta Shipment", {delivery_note: frm.doc.name, company: frm.doc.company,
                declared_value: frm.doc.currency === "UAH" ? frm.doc.grand_total : undefined});
        }, group);
        if (frm.doc.custom_nova_poshta_ttn && frm.doc.docstatus !== 2) {
            frm.add_custom_button("Оновити статус ТТН", async () => {
                if (!saved(frm)) return;
                await frappe.call({method: method + "refresh_tracking", args: {delivery_note: frm.doc.name}, freeze: true});
                await frm.reload_doc();
            }, group);
        }
    }});
    frappe.ui.form.on("Nova Poshta Shipment", {refresh(frm) {
        if (!staff()) return;
        if (frm.doc.state === "Draft") {
            frm.set_intro("Посилка по Україні, відділення → відділення, отримувач — фізична особа. Без післяплати, поштоматів і адресної доставки. Перевірте вагу та оголошену вартість у гривнях.");
            locationButtons(frm, "recipient");
            frm.add_custom_button("Створити ТТН", () => {
                if (!saved(frm)) return;
                frappe.confirm("Створити справжню ТТН у кабінеті Нової пошти та передати дані отримувача?", async () => {
                    const result = await frappe.call({method: method + "create_waybill", args: {name: frm.doc.name}, freeze: true,
                        freeze_message: "Створення ТТН…"});
                    await frm.reload_doc();
                    if (result.message?.error) frappe.msgprint(result.message.error);
                });
            }, group);
        } else if (frm.doc.state === "Uncertain") {
            frm.set_intro("Результат запиту невідомий. Повторне створення заблоковано. Адміністратор має перевірити кабінет Нової пошти та виконати звірку.", "orange");
            if (frappe.user.has_role("System Manager")) frm.add_custom_button("Звірити з кабінетом", () => {
                const dialog = new frappe.ui.Dialog({title: "Звірка з Новою поштою", fields: [
                    {fieldname: "waybill", label: "ТТН із кабінету перевізника", fieldtype: "Data"},
                    {fieldname: "confirmed_absent", label: "Я перевірив кабінет: ТТН не створено", fieldtype: "Check"}
                ], primary_action_label: "Застосувати", primary_action: async values => {
                    await frappe.call({method: method + "reconcile", args: {name: frm.doc.name, ...values}, freeze: true});
                    dialog.hide(); await frm.reload_doc();
                }}); dialog.show();
            }, group);
        } else {
            frm.set_intro("ТТН створено. Відстеження доступне у відвантаженні. Друк, зміни й скасування ТТН — у бізнес-кабінеті Нової пошти.");
        }
        if (frm.doc.delivery_note) frm.add_custom_button("Відкрити відвантаження", () => frappe.set_route("Form", "Delivery Note", frm.doc.delivery_note));
    }});
})();
