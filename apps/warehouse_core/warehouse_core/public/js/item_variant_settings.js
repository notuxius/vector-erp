/* Display Item labels while keeping Variant Field.field_name as an internal ID. */
frappe.ui.form.on("Item Variant Settings", {
    async refresh(frm) {
        if (frappe.boot.lang !== "uk") return;

        await frappe.model.with_doctype("Item");
        const fields = new Map(frappe.get_meta("Item").fields.map((df) => [df.fieldname, df]));
        const labelFor = (value) => {
            const df = fields.get(value);
            // ERPNext leaves the UOM conversion table's label empty.
            const label = df?.label || (value === "uoms" ? "UOM Conversion Details" : null);
            return label ? __(label, null, df?.parent || "Item") : value || "";
        };

        // Frappe's grid and expanded row both use the child DocField formatter.
        frappe.meta.get_docfield("Variant Field", "field_name").formatter =
            (value) => frappe.utils.escape_html(labelFor(value));

        const grid = frm.fields_dict.fields.grid;
        // Upstream supplies only unused fields. Include saved selections too:
        // Autocomplete needs them to render labels and round-trip existing IDs.
        const values = new Set([
            ...frm.doc.fields.map((row) => row.field_name),
            ...(grid.get_docfield("field_name").options || []).map((option) => option.value),
        ].filter(Boolean));
        const labels = new Map();
        for (const value of values) {
            const label = labelFor(value);
            labels.set(label, (labels.get(label) || 0) + 1);
        }
        grid.update_docfield_property("field_name", "options", [...values].map((value) => {
            const label = labelFor(value);
            return {
                value,
                // Custom fields may share labels; keep those choices unambiguous.
                label: labels.get(label) > 1 ? `${label} (${value})` : label,
            };
        }));
        frm.refresh_field("fields");
    },
});
