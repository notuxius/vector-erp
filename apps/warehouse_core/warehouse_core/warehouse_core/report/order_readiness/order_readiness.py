"""Permission-aware operational snapshot. Never reserves stock or blocks shipment."""
import json
from collections import defaultdict

import frappe
from frappe.utils import flt, getdate, nowdate


def batches(values):
    values = list(values)
    for offset in range(0, len(values), 400):
        yield values[offset:offset + 400]


def columns():
    def col(name, label, kind="Data", width=160, options=None):
        return dict(fieldname=name, label=label, fieldtype=kind, width=width, options=options)
    return [
        col("sales_order", "Замовлення", "Link", 180, "Sales Order"),
        col("customer_name", "Клієнт", width=180),
        col("delivery_date", "Дата відправлення", "Date", 125),
        col("due_status", "Термін"), col("stock_status", "Наявність", width=210),
        col("payment_status", "Оплата", width=250),
        col("responsible", "Відповідальний", width=220),
        col("item_code", "Товар", "Link", 170, "Item"),
        col("item_name", "Назва товару", width=200),
        col("warehouse", "Склад", "Link", 180, "Warehouse"),
        col("remaining_qty", "До відправлення", "Float", 130),
        col("stock_uom", "Складська од. виміру", width=140),
        col("actual_qty", "Фізичний залишок", "Float", 140),
        col("available_qty", "Доступно за розрахунком", "Float", 175),
        col("shortage_qty", "Бракує", "Float", 100),
        col("delivery_note", "Останнє відвантаження", "Link", 190, "Delivery Note"),
    ]


def linked_documents(orders, company):
    """Child rows discover links only; parent permissions govern all returned data."""
    invoice_links, delivery_links = defaultdict(set), defaultdict(set)
    for names in batches(orders):
        si, inv = frappe.qb.DocType("Sales Invoice Item"), frappe.qb.DocType("Sales Invoice")
        dn = frappe.qb.DocType("Delivery Note Item")
        rows = (frappe.qb.from_(si).join(inv).on(si.parent == inv.name)
            .left_join(dn).on(si.dn_detail == dn.name)
            .select(si.sales_order, dn.against_sales_order, si.parent)
            .where((inv.docstatus == 1) & (inv.company == company)
                & (si.sales_order.isin(names) | dn.against_sales_order.isin(names))).run(as_dict=True))
        for row in rows:
            for order in {row.sales_order, row.against_sales_order} & orders.keys():
                invoice_links[order].add(row.parent)
        delivery = frappe.qb.DocType("Delivery Note")
        for row in (frappe.qb.from_(dn).join(delivery).on(dn.parent == delivery.name)
                .select(dn.against_sales_order, dn.parent)
                .where((dn.against_sales_order.isin(names)) & (delivery.docstatus == 1)
                    & (delivery.company == company) & (delivery.is_return == 0)).run(as_dict=True)):
            delivery_links[row.against_sales_order].add(row.parent)
    invoices, deliveries = {}, {}
    for doctype, links, fields, destination in (
        ("Sales Invoice", invoice_links, ["name", "outstanding_amount"], invoices),
        ("Delivery Note", delivery_links, ["name", "posting_date", "posting_time", "creation"], deliveries),
    ):
        if not frappe.has_permission(doctype, "read"):
            continue
        for names in batches({name for linked in links.values() for name in linked}):
            for row in frappe.get_list(doctype, filters={"name": ["in", names], "company": company},
                    fields=fields, limit_page_length=0):
                destination[row.name] = row
    return invoice_links, invoices, delivery_links, deliveries


def payment_status(order, links, invoices):
    if not frappe.has_permission("Sales Invoice", "read") or any(name not in invoices for name in links):
        return "Немає доступу до оплат"
    if any(flt(invoices[name].outstanding_amount) > 0.005 for name in links):
        return "Очікується оплата"
    if any(flt(invoices[name].outstanding_amount) < -0.005 for name in links):
        return "Очікується повернення або залік коштів"
    if links:
        return "Рахунки погашено" if flt(order.per_billed) >= 99.999 else "Виставлені рахунки погашено; решта не виставлена"
    if flt(order.advance_paid) > 0:
        return "Є аванс; рахунок не виставлено"
    return "Рахунок не виставлено"


def execute(filters=None):
    filters = frappe._dict(filters or {})
    if not filters.company:
        frappe.throw("Виберіть компанію.")
    frappe.has_permission("Sales Order", "read", throw=True)
    frappe.get_doc("Company", filters.company).check_permission("read")
    cutoff, today = getdate(filters.date or nowdate()), getdate(nowdate())
    orders = {row.name: row for row in frappe.get_list("Sales Order", filters={
        "company": filters.company, "docstatus": 1, "status": ["not in", ["Closed", "Completed", "Cancelled", "On Hold"]]},
        fields=["name", "customer", "customer_name", "_assign", "advance_paid", "per_billed"], limit_page_length=0)}
    warehouses = set(frappe.get_list("Warehouse", filters={"company": filters.company}, pluck="name", limit_page_length=0))
    lines = []
    line = frappe.qb.DocType("Sales Order Item")
    for names in batches(orders):
        lines.extend((frappe.qb.from_(line).select(line.name, line.parent, line.idx, line.delivery_date,
            line.item_code, line.item_name, line.warehouse, line.qty, line.delivered_qty,
            line.conversion_factor, line.stock_uom)
            .where(line.parent.isin(names) & (line.docstatus == 1) & (line.qty > line.delivered_qty)
                & ((line.delivery_date <= cutoff) | line.delivery_date.isnull())).run(as_dict=True)))
    items = set()
    for codes in batches({row.item_code for row in lines}):
        items.update(frappe.get_list("Item", filters={"name": ["in", codes], "is_stock_item": 1},
            pluck="name", limit_page_length=0))
    lines = [row for row in lines if row.item_code in items and (not row.warehouse or row.warehouse in warehouses)]
    lines.sort(key=lambda row: (getdate(row.delivery_date) if row.delivery_date else getdate("9999-12-31"), row.parent, row.idx))
    orders = {name: orders[name] for name in {row.parent for row in lines}}
    invoice_links, invoices, delivery_links, deliveries = linked_documents(orders, filters.company)
    bins, existing_bins, reservations = {}, set(), defaultdict(float)
    can_stock = frappe.has_permission("Bin", "read")
    can_reserve = frappe.has_permission("Stock Reservation Entry", "read")
    if can_stock and warehouses:
        for codes in batches(items):
            criteria = {"item_code": ["in", codes], "warehouse": ["in", list(warehouses)]}
            # Distinguish no Bin (= zero stock) from a Bin hidden by row permissions.
            for row in frappe.get_all("Bin", filters=criteria, fields=["item_code", "warehouse"]):
                existing_bins.add((row.item_code, row.warehouse))
            for row in frappe.get_list("Bin", filters=criteria,
                    fields=["item_code", "warehouse", "actual_qty", "reserved_stock"], limit_page_length=0):
                bins[row.item_code, row.warehouse] = row
        if can_reserve:
            for names in batches(orders):
                for row in frappe.get_list("Stock Reservation Entry", filters={"voucher_type": "Sales Order",
                        "voucher_no": ["in", names], "docstatus": 1, "status": ["not in", ["Cancelled", "Delivered"]]},
                        fields=["voucher_detail_no", "item_code", "warehouse", "reserved_qty", "delivered_qty"], limit_page_length=0):
                    reservations[row.voucher_detail_no, row.item_code, row.warehouse] += max(0, flt(row.reserved_qty) - flt(row.delivered_qty))
    pools = {key: max(0, flt(value.actual_qty) - flt(value.reserved_stock)) for key, value in bins.items()}
    data = []
    for line in lines:
        order = orders[line.parent]
        assigned = json.loads(order._assign or "[]")
        required = flt((flt(line.qty) - flt(line.delivered_qty)) * flt(line.conversion_factor), 6)
        key = (line.item_code, line.warehouse)
        balance = bins.get(key, frappe._dict(actual_qty=0, reserved_stock=0))
        known = can_stock and bool(line.warehouse) and (key in bins or key not in existing_bins)
        # If reservation detail is unavailable, do not claim reserved stock is usable.
        known = known and (can_reserve or not flt(balance.reserved_stock))
        available = shortage = actual = None
        if known:
            actual = flt(balance.actual_qty)
            own = min(required, reservations[line.name, line.item_code, line.warehouse], max(0, actual))
            free = min(max(0, required - own), pools.get(key, 0))
            available = flt(own + free, 6)
            shortage = flt(max(0, required - available), 6)
            pools[key] = max(0, pools.get(key, 0) - free)
        due = getdate(line.delivery_date) if line.delivery_date else None
        visible_deliveries = [deliveries[name] for name in delivery_links[line.parent] if name in deliveries]
        latest = max(visible_deliveries, key=lambda doc: (doc.posting_date, str(doc.posting_time), doc.creation, doc.name), default=None)
        row = dict(sales_order=line.parent, customer_name=order.customer_name, delivery_date=due,
            due_status="Без дати" if not due else "Прострочено" if due < today else "На сьогодні" if due == today else "Заплановано",
            stock_status=("Не вказано склад" if not line.warehouse else "Немає доступу до залишку") if not known else
                "Бракує товару" if shortage > 0 else "Достатньо за розрахунком",
            payment_status=payment_status(order, invoice_links[line.parent], invoices),
            responsible=", ".join(assigned) if assigned else "Не призначено",
            item_code=line.item_code, item_name=line.item_name, warehouse=line.warehouse,
            remaining_qty=required, stock_uom=frappe._(line.stock_uom), actual_qty=actual,
            available_qty=available, shortage_qty=shortage, delivery_note=latest.name if latest else None)
        # Apply presentation filters after allocation so a filter cannot create stock.
        if filters.warehouse and line.warehouse != filters.warehouse:
            continue
        if filters.customer and order.customer != filters.customer:
            continue
        if filters.responsible and filters.responsible not in assigned:
            continue
        attention = filters.attention or "Усі"
        if attention != "Усі" and not {
            "На сьогодні": row["due_status"] == "На сьогодні",
            "Прострочені": row["due_status"] == "Прострочено",
            "Бракує товару": row["stock_status"] == "Бракує товару",
            "Очікується оплата": row["payment_status"] == "Очікується оплата",
            "Не призначено відповідального": not assigned,
        }.get(attention, False):
            continue
        data.append(row)
    def count(predicate):
        return len({row["sales_order"] for row in data if predicate(row)})
    summary = [dict(label=label, value=count(predicate), indicator=color, datatype="Int") for label, color, predicate in (
        ("Замовлення до відправлення", "Blue", lambda row: True),
        ("На сьогодні", "Orange", lambda row: row["due_status"] == "На сьогодні"),
        ("Прострочені", "Red", lambda row: row["due_status"] == "Прострочено"),
        ("Бракує товару", "Red", lambda row: row["stock_status"] == "Бракує товару"),
        ("Очікується оплата", "Orange", lambda row: row["payment_status"] == "Очікується оплата"),
    )]
    message = ("Поточний стан, не історичний зріз. Кількості — у складських одиницях. "
        "Вільний залишок розподілено розрахунково між доступними вам позиціями до вибраної дати "
        "за датою відправлення, номером замовлення та рядком; чинні резерви враховано окремо. "
        "Фільтри не змінюють цей розрахунок. Це не резервування й не гарантія готовності. "
        "Оплата стосується пов’язаних рахунків у межах ваших прав і не блокує відвантаження. "
        "Відповідальні — користувачі, призначені в замовленні. Картки рахують унікальні замовлення у вибірці.")
    return columns(), data, message, None, summary
