/* Localize POS metadata choices without changing field names or option keys. */
frappe.ui.form.on("POS Settings", {
    async get_invoice_fields(frm) {
        if (frappe.boot.lang !== "uk" || !frm.doc.invoice_type) return;
        const invoiceType = frm.doc.invoice_type;
        await frappe.model.with_doctype(invoiceType);
        if (frm.doc.invoice_type !== invoiceType) return;
        const fields = new Map(frappe.get_meta(invoiceType).fields.map((df) => [df.fieldname, df]));
        const labelFor = (value) => {
            const df = fields.get(value);
            return df?.label ? __(df.label, null, invoiceType) : value || "";
        };
        const grid = frm.fields_dict.invoice_fields.grid;
        const options = grid.get_docfield("fieldname").options || [];
        grid.update_docfield_property("fieldname", "options", options.map((option) => {
            if (!option || typeof option !== "object") return option;
            const df = fields.get(option.value);
            return df ? { ...option, label: `${labelFor(option.value)} (${__(df.fieldtype, null, "DocField")})` } : option;
        }));
        frappe.meta.get_docfield("POS Field", "fieldname").formatter =
            (value) => frappe.utils.escape_html(labelFor(value));
        frappe.meta.get_docfield("POS Field", "options").formatter = (value, df, options, row) => {
            const translated = ["Link", "Select"].includes(row?.fieldtype)
                ? (value || "").split("\n").map((line) => __(line)).join("\n") : value || "";
            return frappe.utils.escape_html(translated);
        };
        frm.refresh_field("invoice_fields");
    },

    async add_search_options(frm) {
        if (frappe.boot.lang !== "uk") return;
        await frappe.model.with_doctype("Item");
        const fields = new Map(frappe.get_meta("Item").fields.map((df) => [df.fieldname, df]));
        const labelFor = (value) => {
            const df = fields.get(value);
            return df?.label ? __(df.label, null, "Item") : value || "";
        };
        const optionLabel = (value) => labelFor(frm.searchable_item_fields?.[value]) || value || "";
        const grid = frm.fields_dict.pos_search_fields.grid;
        const options = grid.get_docfield("field").options || [];
        grid.update_docfield_property("field", "options", options.map((value) => ({
            value, label: optionLabel(value),
        })));
        frappe.meta.get_docfield("POS Search Fields", "field").formatter =
            (value) => frappe.utils.escape_html(optionLabel(value));
        frappe.meta.get_docfield("POS Search Fields", "fieldname").formatter =
            (value) => frappe.utils.escape_html(labelFor(value));
        frm.refresh_field("pos_search_fields");
    },
});
