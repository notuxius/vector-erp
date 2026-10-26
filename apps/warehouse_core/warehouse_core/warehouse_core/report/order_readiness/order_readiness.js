frappe.query_reports["Order Readiness"] = {
    filters: [
        {fieldname: "company", label: "Компанія", fieldtype: "Link", options: "Company", reqd: 1,
            default: frappe.defaults.get_user_default("Company")},
        {fieldname: "date", label: "Відправлення до", fieldtype: "Date", reqd: 1,
            default: frappe.datetime.get_today()},
        {fieldname: "warehouse", label: "Склад", fieldtype: "Link", options: "Warehouse",
            get_query: () => ({filters: {company: frappe.query_report.get_filter_value("company"), is_group: 0}})},
        {fieldname: "customer", label: "Клієнт", fieldtype: "Link", options: "Customer"},
        {fieldname: "responsible", label: "Відповідальний", fieldtype: "Link", options: "User"},
        {fieldname: "attention", label: "Показати", fieldtype: "Select", default: "Усі",
            options: ["Усі", "На сьогодні", "Прострочені", "Бракує товару", "Очікується оплата", "Не призначено відповідального"]}
    ],
    formatter(value, row, column, data, default_formatter) {
        const formatted = default_formatter(value, row, column, data);
        if (!data) return formatted;
        const colors = {"Бракує товару": "red", "Прострочено": "red", "На сьогодні": "orange",
            "Достатньо за розрахунком": "green", "Очікується оплата": "orange"};
        if (["stock_status", "due_status", "payment_status"].includes(column.fieldname) && colors[value]) {
            return `<span class="indicator-pill ${colors[value]}">${frappe.utils.escape_html(value)}</span>`;
        }
        return formatted;
    }
};
