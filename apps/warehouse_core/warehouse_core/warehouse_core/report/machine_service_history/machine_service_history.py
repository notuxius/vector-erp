import frappe


def execute(filters=None):
    filters = frappe._dict(filters or {})
    if not filters.company or not filters.serial_no:
        frappe.throw("Виберіть компанію та серійний номер верстата.")
    frappe.has_permission("Maintenance Visit", "read", throw=True)
    columns = [
        {"fieldname": "visit", "label": "Сервісний виїзд", "fieldtype": "Link", "options": "Maintenance Visit", "width": 180},
        {"fieldname": "date", "label": "Дата", "fieldtype": "Date", "width": 110},
        {"fieldname": "customer", "label": "Клієнт", "fieldtype": "Data", "width": 180},
        {"fieldname": "item_code", "label": "Верстат", "fieldtype": "Link", "options": "Item", "width": 150},
        {"fieldname": "service_person", "label": "Виконавець", "fieldtype": "Data", "width": 150},
        {"fieldname": "work_done", "label": "Виконані роботи", "fieldtype": "Data", "width": 350},
    ]
    names = frappe.get_all("Maintenance Visit Purpose", filters={"serial_no": filters.serial_no}, pluck="parent")
    if not names:
        return columns, []
    visits = frappe.get_list("Maintenance Visit", filters={"name": ["in", names], "company": filters.company, "docstatus": 1},
                             fields=["name"], limit_page_length=0, order_by="mntc_date desc")
    data = []
    for visit in visits:
        doc = frappe.get_doc("Maintenance Visit", visit.name)
        for row in doc.purposes:
            if row.serial_no == filters.serial_no:
                data.append({"visit": doc.name, "date": doc.mntc_date, "customer": doc.customer_name,
                    "item_code": row.item_code, "service_person": row.service_person, "work_done": row.work_done})
    return columns, data
