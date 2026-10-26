"""Read-only chart data, aggregated only after Frappe applies record permissions."""
from collections import defaultdict
from decimal import Decimal

import frappe
from frappe.utils import add_months, getdate, nowdate


def can_read(doctype, fields):
    return frappe.has_permission(doctype, "read") and set(fields).issubset(
        frappe.get_meta(doctype).get_permitted_fieldnames(user=frappe.session.user))


def number(value):
    return Decimal(str(value or 0))


def get_charts(companies, months=6):
    if str(months) not in {"3", "6", "12"}:
        frappe.throw("Оберіть період: 3, 6 або 12 місяців.")
    months = int(months)
    today = getdate(nowdate())
    start = getdate(add_months(today.replace(day=1), 1 - months))
    month_keys = [str(add_months(start, offset))[:7] for offset in range(months)]
    currencies = {frappe.get_cached_value("Company", name, "default_currency") for name in companies}
    result = dict(months=months, start=str(start), end=str(today), currency=None, charts=[])
    if len(currencies) != 1 or None in currencies:
        result["message"] = ("Графіки не об’єднують компанії з різними валютами."
                             if companies else "Немає доступних компаній для побудови графіків.")
        return result
    result["currency"] = currencies.pop()
    filters = {"company": ["in", companies], "docstatus": 1, "posting_date": ["between", [str(start), str(today)]]}

    def series(rows, field):
        values = defaultdict(Decimal)
        for row in rows:
            values[str(row.posting_date)[:7]] += number(row[field])
        return [float(values[key]) for key in month_keys]

    for key, title, doctype, color in [
        ("sales", "Продажі за місяцями", "Sales Invoice", "#4b80d0"),
        ("purchases", "Закупівлі за місяцями", "Purchase Invoice", "#cc873d"),
    ]:
        if not can_read(doctype, ["posting_date", "base_net_total"]):
            continue
        rows = frappe.get_list(doctype, filters=filters, fields=["posting_date", "base_net_total"], limit_page_length=0)
        result["charts"].append(dict(key=key, title=title, type="bar", labels=month_keys,
            datasets=[dict(name="Сума без податків", values=series(rows, "base_net_total"))],
            colors=[color], has_data=bool(rows),
            description="Проведені рахунки без податків. Повернення зменшують суму; поточний місяць — до сьогодні."))

    if can_read("Payment Entry", ["posting_date", "payment_type", "base_received_amount", "base_paid_amount"]):
        rows = frappe.get_list("Payment Entry", filters={**filters, "payment_type": ["in", ["Receive", "Pay"]]},
            fields=["posting_date", "payment_type", "base_received_amount", "base_paid_amount"], limit_page_length=0)
        result["charts"].append(dict(key="payments", title="Надходження та виплати", type="bar", labels=month_keys,
            datasets=[dict(name="Надходження", values=series([row for row in rows if row.payment_type == "Receive"], "base_received_amount")),
                      dict(name="Виплати", values=series([row for row in rows if row.payment_type == "Pay"], "base_paid_amount"))],
            colors=["#449780", "#9270c3"], has_data=bool(rows),
            description="Проведені платіжні документи у валюті компанії. Внутрішні перекази та журнальні проводки не включено."))

    if can_read("Bin", ["warehouse", "item_code", "stock_value"]) and frappe.has_permission("Warehouse", "read") and frappe.has_permission("Item", "read"):
        warehouses = frappe.get_list("Warehouse", filters={"company": ["in", companies], "is_group": 0}, pluck="name", limit_page_length=0)
        totals = defaultdict(Decimal)
        if warehouses:
            rows = frappe.get_list("Bin", filters={"warehouse": ["in", warehouses]},
                fields=["warehouse", "item_code", "stock_value"], limit_page_length=0)
            # Item permissions can further narrow visible stock, even when a warehouse is readable.
            codes = list({row.item_code for row in rows})
            permitted = set()
            for offset in range(0, len(codes), 400):
                permitted.update(frappe.get_list("Item", filters={"name": ["in", codes[offset:offset + 400]]},
                    pluck="name", limit_page_length=0))
            for row in rows:
                if row.item_code in permitted:
                    totals[row.warehouse] += number(row.stock_value)
        ranked = sorted(totals.items(), key=lambda row: (-abs(row[1]), row[0]))
        shown = ranked[:7]
        if len(ranked) > 7:
            shown.append(("Інші склади", sum((value for _, value in ranked[7:]), Decimal(0))))
        result["charts"].append(dict(key="stock", title="Вартість запасів за складами", type="bar",
            labels=[name for name, _ in shown], datasets=[dict(name="Вартість запасів", values=[float(value) for _, value in shown])],
            colors=["#449780"], has_data=any(value != 0 for _, value in ranked),
            description="Поточна облікова вартість доступних товарів і складів; не залежить від обраного періоду. До 7 складів, решта — разом."))
    order = {key: index for index, key in enumerate(["sales", "payments", "purchases", "stock"])}
    result["charts"].sort(key=lambda chart: order[chart["key"]])
    return result
