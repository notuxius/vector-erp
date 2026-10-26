frappe.query_reports["Expected Production"] = {
    filters: [
        { fieldname: "company", label: "Компанія", fieldtype: "Link", options: "Company", reqd: 1,
            default: frappe.defaults.get_user_default("Company") },
        { fieldname: "warehouse", label: "Склад готової продукції", fieldtype: "Link", options: "Warehouse",
            get_query: () => ({ filters: { company: frappe.query_report.get_filter_value("company"), is_group: 0 } }) },
        { fieldname: "item_code", label: "Товар", fieldtype: "Link", options: "Item" },
        { fieldname: "view", label: "Подання", fieldtype: "Select",
            options: "Товари та склади\nВиробничі замовлення", default: "Товари та склади", reqd: 1 },
    ],
    onload(report) {
        report.page.add_inner_button("Виробничі замовлення", () => frappe.set_route("List", "Work Order"));
    },
};
