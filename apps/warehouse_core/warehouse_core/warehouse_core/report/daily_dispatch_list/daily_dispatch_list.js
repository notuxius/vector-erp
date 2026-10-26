frappe.query_reports["Daily Dispatch List"] = {
    filters: [
        {fieldname: "company", label: "Компанія", fieldtype: "Link", options: "Company", reqd: 1,
            default: frappe.defaults.get_user_default("Company")},
        {fieldname: "date", label: "Дата відправлення", fieldtype: "Date", reqd: 1, default: frappe.datetime.get_today()},
        {fieldname: "warehouse", label: "Склад", fieldtype: "Link", options: "Warehouse"},
        {fieldname: "include_overdue", label: "Включити прострочені", fieldtype: "Check", default: 1}
    ]
};
