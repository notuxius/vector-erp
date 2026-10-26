"""Live expectations from authorized work orders; never write a stock counter."""
from collections import defaultdict

import frappe
from frappe.utils import flt


def column(name, label, kind="Data", options=None, width=160):
    return dict(fieldname=name, label=label, fieldtype=kind, options=options, width=width)


def execute(filters=None):
    filters = frappe._dict(filters or {})
    if not filters.company:
        frappe.throw("Виберіть компанію.")
    frappe.has_permission("Work Order", "read", throw=True)
    frappe.get_doc("Company", filters.company).check_permission("read")
    detailed = filters.view == "Виробничі замовлення"
    base = [column("item_code", "Товар", "Link", "Item"), column("item_name", "Назва товару", width=210),
            column("warehouse", "Склад готової продукції", "Link", "Warehouse", 200),
            column("stock_uom", "Складська од. виміру")]
    columns = ([column("work_order", "Виробниче замовлення", "Link", "Work Order", 190)] + base + [
        column("status", "Статус"), column("planned_end_date", "Планове завершення", "Datetime", width=180),
        column("qty", "Замовлено", "Float"), column("produced_qty", "Випущено", "Float"),
        column("process_loss_qty", "Втрати процесу", "Float"),
        column("expected_qty", "Очікується з виробництва", "Float", width=190),
    ] if detailed else base + [
        column("actual_qty", "Фізичний залишок", "Float"),
        column("reserved_stock", "Зарезервовано фізично", "Float", width=180),
        column("ordered_qty", "Очікується із закупівель", "Float", width=190),
        column("expected_qty", "Очікується з виробництва", "Float", width=190),
        column("order_count", "Виробничих замовлень", "Int", width=180),
    ])
    warehouses = set(frappe.get_list("Warehouse", filters={"company": filters.company, "is_group": 0},
                                     pluck="name", limit_page_length=0))
    if filters.warehouse:
        warehouses &= {filters.warehouse}
    criteria = {"company": filters.company, "docstatus": 1,
                "status": ["not in", ["Stopped", "Closed", "Completed", "Cancelled"]]}
    if filters.item_code:
        criteria["production_item"] = filters.item_code
    if filters.warehouse:
        criteria["fg_warehouse"] = filters.warehouse
    orders = frappe.get_list("Work Order", filters=criteria, fields=[
        "name", "production_item", "fg_warehouse", "qty", "produced_qty", "process_loss_qty",
        "status", "planned_end_date", "stock_uom", "track_semi_finished_goods"], limit_page_length=0)
    items = {}
    codes = sorted({order.production_item for order in orders})
    for offset in range(0, len(codes), 400):
        items.update({item.name: item for item in frappe.get_list("Item",
            filters={"name": ["in", codes[offset:offset + 400]]},
            fields=["name", "item_name"], limit_page_length=0)})
    data, grouped = [], defaultdict(lambda: {"expected_qty": 0, "order_count": 0})
    skipped = False
    for order in orders:
        if order.fg_warehouse not in warehouses or order.production_item not in items:
            continue
        # Operation-level semi-finished tracking has different output semantics.
        # Explicitly flag it rather than silently mixing it with standard WOs.
        if order.track_semi_finished_goods:
            skipped = True
            continue
        remaining = max(0, flt(order.qty) - flt(order.produced_qty) - flt(order.process_loss_qty))
        if not remaining:
            continue
        row = dict(item_code=order.production_item, item_name=items[order.production_item].item_name,
                   warehouse=order.fg_warehouse, stock_uom=order.stock_uom)
        if detailed:
            data.append({**row, "work_order": order.name, "status": frappe._(order.status),
                         "planned_end_date": order.planned_end_date, "qty": order.qty,
                         "produced_qty": order.produced_qty, "process_loss_qty": order.process_loss_qty,
                         "expected_qty": remaining})
        else:
            group = grouped[(order.production_item, order.fg_warehouse)]
            group.update(row)
            group["expected_qty"] += remaining
            group["order_count"] += 1
    if not detailed:
        # Bin access is checked separately. Never substitute zero for hidden stock.
        bins = {}
        if grouped and frappe.has_permission("Bin", "read"):
            codes = sorted({item for item, _ in grouped})
            for offset in range(0, len(codes), 400):
                for row in frappe.get_list("Bin", filters={"item_code": ["in", codes[offset:offset + 400]],
                    "warehouse": ["in", sorted(warehouses)]}, fields=["item_code", "warehouse", "actual_qty",
                    "reserved_stock", "ordered_qty"], limit_page_length=0):
                    bins[(row.item_code, row.warehouse)] = row
        for key, row in grouped.items():
            stock = bins.get(key)
            for field in ("actual_qty", "reserved_stock", "ordered_qty"):
                row[field] = flt(stock[field]) if stock else None
            data.append(row)
    data.sort(key=lambda row: (row["item_code"], row["warehouse"], row.get("work_order", "")))
    message = ("Поточний знімок доступних виробничих замовлень: замовлено мінус випущено та втрати процесу. "
               "Чернетки, зупинені, закриті й скасовані замовлення не враховуються. "
               "Очікування не збільшує фізичний залишок і не резервує товар. "
               "Залишки та закупівлі показані для всього товару на складі; порожні значення означають "
               "відсутній або недоступний складський запис. Для джерел очікування виберіть «Виробничі замовлення».")
    if skipped:
        message += " УВАГА: є замовлення з поопераційним обліком напівфабрикатів; вони не включені й потребують окремої перевірки."
    return columns, data, message
