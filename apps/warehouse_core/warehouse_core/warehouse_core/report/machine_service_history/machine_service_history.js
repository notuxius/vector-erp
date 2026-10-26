frappe.query_reports["Machine Service History"] = {
    filters: [
        {fieldname: "company", label: "Компанія", fieldtype: "Link", options: "Company", reqd: 1,
            default: frappe.defaults.get_user_default("Company")},
        {fieldname: "serial_no", label: "Серійний номер", fieldtype: "Link", options: "Serial No", reqd: 1}
    ]
};
