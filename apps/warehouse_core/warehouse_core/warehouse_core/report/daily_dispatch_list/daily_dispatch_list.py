"""Outstanding stock order lines due for dispatch, without exposing prices."""
import frappe
from frappe.utils import cint, flt, getdate, nowdate


def execute(filters=None):
    filters = frappe._dict(filters or {})
    if not filters.company:
        frappe.throw("Виберіть компанію.")
    frappe.has_permission("Sales Order", "read", throw=True)
    columns = [
        {"fieldname": "sales_order", "label": "Замовлення", "fieldtype": "Link", "options": "Sales Order", "width": 170},
        {"fieldname": "customer_name", "label": "Клієнт", "fieldtype": "Data", "width": 180},
        {"fieldname": "delivery_date", "label": "Дата відправлення", "fieldtype": "Date", "width": 120},
        {"fieldname": "item_code", "label": "Код товару", "fieldtype": "Link", "options": "Item", "width": 150},
        {"fieldname": "item_name", "label": "Товар", "fieldtype": "Data", "width": 220},
        {"fieldname": "warehouse", "label": "Склад", "fieldtype": "Link", "options": "Warehouse", "width": 180},
        {"fieldname": "qty", "label": "До відправлення", "fieldtype": "Float", "width": 130},
        {"fieldname": "uom", "label": "Од. виміру", "fieldtype": "Data", "width": 100},
    ]
    date = getdate(filters.date or nowdate())
    line = frappe.qb.DocType("Sales Order Item")
    query = frappe.qb.from_(line).select(line.parent, line.delivery_date, line.item_code,
        line.item_name, line.warehouse, line.qty, line.delivered_qty, line.uom).where(
        (line.docstatus == 1) & (line.qty > line.delivered_qty))
    query = query.where(line.delivery_date <= date) if cint(filters.include_overdue) else query.where(line.delivery_date == date)
    if filters.warehouse:
        query = query.where(line.warehouse == filters.warehouse)
    # Candidate lines are internal only: authorize their parents before returning any data.
    # Do not load every historical order/document after a full-history migration.
    candidates = query.run(as_dict=True)
    names = sorted({row.parent for row in candidates})
    orders = {}
    for offset in range(0, len(names), 500):
        for order in frappe.get_list("Sales Order", filters={"company": filters.company, "docstatus": 1,
            "name": ["in", names[offset:offset + 500]], "status": ["not in", ["Closed", "Completed", "On Hold"]]},
            fields=["name", "customer_name"], limit_page_length=0):
            orders[order.name] = order
    allowed_warehouses = set(frappe.get_list("Warehouse", filters={"company": filters.company}, pluck="name", limit_page_length=0))
    data = []
    for row in candidates:
        if row.parent not in orders or row.warehouse not in allowed_warehouses:
            continue
        if not frappe.get_cached_value("Item", row.item_code, "is_stock_item"):
            continue
        data.append({"sales_order": row.parent, "customer_name": orders[row.parent].customer_name,
            "delivery_date": row.delivery_date, "item_code": row.item_code, "item_name": row.item_name,
            "warehouse": row.warehouse, "qty": flt(row.qty) - flt(row.delivered_qty), "uom": frappe._(row.uom)})
    data.sort(key=lambda row: (row["delivery_date"], row["sales_order"], row["item_code"]))
    return columns, data, "Неповністю відвантажені складські позиції проведених замовлень за датою відправлення. Список не резервує і не списує товар."
