frappe.listview_settings["Knowledge Article"] = {
    add_fields: ["status"],
    get_indicator(doc) {
        const states = {Draft: ["Чернетка", "gray"], Review: ["На перевірці", "orange"],
            Published: ["Опубліковано", "green"], Archived: ["В архіві", "gray"]};
        const state = states[doc.status] || states.Draft;
        return [...state, `status,=,${doc.status}`];
    }
};
