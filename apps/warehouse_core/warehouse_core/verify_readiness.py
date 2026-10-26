"""Real transaction checks for Order Readiness; fixtures always roll back."""
import json
import uuid

import frappe
from frappe.utils import add_days, nowdate

from warehouse_core.demo import COMPANY, MAIN, SECONDARY, CUSTOMER
from warehouse_core.warehouse_core.report.order_readiness.order_readiness import execute


def run():
    if not frappe.conf.get("warehouse_demo") or not frappe.db.exists("Company", COMPANY):
        frappe.throw("Run these checks only on the explicitly configured demo site")
    from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note, make_sales_invoice
    from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

    original_user, original_test = frappe.session.user, frappe.flags.in_test
    prefix = "VERIFY-READY-" + uuid.uuid4().hex[:8]
    checks = []
    frappe.set_user("Administrator")
    frappe.flags.in_test = True
    filters = {"company": COMPANY, "date": nowdate()}

    def submit(doc):
        doc.insert()
        doc.submit()
        return doc

    def rows(**extra):
        return [row for row in execute({**filters, **extra})[1] if row["item_code"] == prefix]

    def row(order, **extra):
        return next(result for result in rows(**extra) if result["sales_order"] == order.name)

    try:
        uom = frappe.get_doc({"doctype": "UOM", "uom_name": prefix, "must_be_whole_number": 1}).insert()
        frappe.get_doc({"doctype": "Item", "item_code": prefix, "item_group": "Products", "is_stock_item": 1,
            "stock_uom": "Nos", "uoms": [{"uom": "Nos", "conversion_factor": 1}, {"uom": uom.name, "conversion_factor": 2}]}).insert()
        submit(frappe.get_doc({"doctype": "Stock Entry", "stock_entry_type": "Material Receipt", "company": COMPANY,
            "items": [{"item_code": prefix, "qty": 10, "t_warehouse": MAIN, "basic_rate": 10}]}))

        def order(date, qty, unit="Nos", factor=1, warehouse=MAIN, status=None):
            doc = submit(frappe.get_doc({"doctype": "Sales Order", "customer": CUSTOMER, "company": COMPANY,
                "transaction_date": add_days(nowdate(), -2), "delivery_date": date, "selling_price_list": "Standard Selling",
                "items": [{"item_code": prefix, "qty": qty, "uom": unit, "conversion_factor": factor,
                    "rate": 100, "warehouse": warehouse}]}))
            if status:
                doc.update_status(status)
            return doc

        earlier = order(add_days(nowdate(), -1), 4)
        current = order(nowdate(), 4, uom.name, 2)
        future = order(add_days(nowdate(), 1), 1)
        held = order(nowdate(), 1, status="On Hold")
        assert row(earlier)["due_status"] == "Прострочено"
        assert row(current)["remaining_qty"] == 8
        assert row(current)["available_qty"] == 6 and row(current)["shortage_qty"] == 2
        assert not any(r["sales_order"] in (future.name, held.name) for r in rows())
        assert row(current, attention="Бракує товару")["shortage_qty"] == 2
        assert len(rows(attention="На сьогодні")) == 1
        assert row(future, date=add_days(nowdate(), 1))["shortage_qty"] == 1
        checks.append("Due/overdue/future/held orders, stock UOM conversion, competing orders, filter-stable shortages")

        delivery = make_delivery_note(earlier.name)
        delivery.items[0].qty = 1
        submit(delivery)
        assert row(earlier)["remaining_qty"] == 3
        assert row(earlier)["delivery_note"] == delivery.name
        assert row(current)["shortage_qty"] == 2
        invoice = make_sales_invoice(current.name)
        invoice.items[0].qty = 1
        submit(invoice)
        assert row(current)["payment_status"] == "Очікується оплата"
        payment = get_payment_entry("Sales Invoice", invoice.name)
        payment.reference_no, payment.reference_date = prefix, nowdate()
        submit(payment)
        assert row(current)["payment_status"] == "Виставлені рахунки погашено; решта не виставлена"
        advance = get_payment_entry("Sales Order", future.name)
        advance.reference_no, advance.reference_date = prefix, nowdate()
        submit(advance)
        assert row(future, date=add_days(nowdate(), 1))["payment_status"] == "Є аванс; рахунок не виставлено"
        checks.append("Partial deliveries and delivery links, unpaid/settled partial invoices, order advances")

        frappe.db.set_single_value("Stock Settings", "enable_stock_reservation", 1)
        frappe.db.set_value("Sales Order Item", future.items[0].name, "reserve_stock", 1)
        future.reload()
        future.create_stock_reservation_entries(items_details=[{"sales_order_item": future.items[0].name,
            "warehouse": MAIN, "qty_to_reserve": 1}], notify=False)
        assert row(current)["shortage_qty"] == 3
        assert row(future, date=add_days(nowdate(), 1))["shortage_qty"] == 0
        checks.append("Native reservation protects future order stock and credits its owning line")

        email = prefix.lower() + "@example.test"
        user = frappe.get_doc({"doctype": "User", "email": email, "first_name": "Readiness verification",
            "enabled": 1, "send_welcome_email": 0, "user_type": "System User",
            "roles": [{"role": "Sales User"}]}).insert()
        frappe.db.set_value("Sales Order", current.name, "_assign", json.dumps([user.name]))
        assert row(current, responsible=user.name)["responsible"] == user.name
        assert not any(r["sales_order"] == earlier.name for r in rows(responsible=user.name))
        assert row(current, responsible=user.name)["shortage_qty"] == 3
        summary = execute({**filters, "responsible": user.name})[4]
        assert summary[0]["value"] == 1 and summary[3]["value"] == 1
        checks.append("Assigned employee filter and summary count unique orders without changing stock allocation")

        frappe.get_doc({"doctype": "User Permission", "user": user.name, "allow": "Sales Invoice",
            "for_value": invoice.name, "apply_to_all_doctypes": 1}).insert()
        other_invoice = make_sales_invoice(earlier.name)
        submit(other_invoice)
        frappe.clear_cache(user=user.name)
        frappe.set_user(user.name)
        assert row(earlier)["payment_status"] == "Немає доступу до оплат"
        frappe.set_user("Administrator")
        frappe.get_doc({"doctype": "User Permission", "user": user.name, "allow": "Warehouse",
            "for_value": SECONDARY, "apply_to_all_doctypes": 1}).insert()
        frappe.clear_cache(user=user.name)
        frappe.set_user(user.name)
        assert not rows(), "Restricted warehouse leaked into the dashboard"
        frappe.set_user("Guest")
        try:
            execute(filters)
        except frappe.PermissionError:
            pass
        else:
            raise AssertionError("Guest could read dashboard")
        checks.append("Invoice and warehouse User Permissions, guest rejection")
    finally:
        frappe.set_user(original_user)
        frappe.db.rollback()
        frappe.flags.in_test = original_test
        frappe.clear_cache()
    assert not frappe.db.exists("Item", prefix)
    return {"passed": len(checks), "checks": checks, "test_data": "rolled back"}
