"""Rollback-only acceptance checks for the confirmed sales rules."""
import uuid

import frappe
from frappe.utils import flt, nowdate

from warehouse_core.demo import COMPANY, MAIN, CUSTOMER
from warehouse_core.sales_completion import get_completion_check
from warehouse_core.warehouse_core.report.order_readiness.order_readiness import payment_status


def run():
    if not frappe.conf.get("warehouse_demo") or not frappe.db.exists("Company", COMPANY):
        frappe.throw("Run these checks only on the explicitly configured demo site")
    from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note, make_sales_invoice
    from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_return
    from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
    from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return as make_credit

    original_user, original_test = frappe.session.user, frappe.flags.in_test
    frappe.set_user("Administrator")
    frappe.flags.in_test = True
    prefix = "VERIFY-COMPLETE-" + uuid.uuid4().hex[:8]
    checks = []

    def submit(doc):
        doc.insert()
        doc.submit()
        return doc

    def state(order):
        return get_completion_check(order.name)

    def pay(doctype, name, amount=None):
        doc = get_payment_entry(doctype, name)
        if amount is not None:
            doc.paid_amount = doc.received_amount = amount
            doc.references[0].allocated_amount = amount
        doc.reference_no, doc.reference_date = prefix, nowdate()
        return submit(doc)

    def order(qty):
        return frappe.get_doc({"doctype": "Sales Order", "company": COMPANY, "customer": CUSTOMER,
            "delivery_date": nowdate(), "items": [{"item_code": prefix, "qty": qty,
                "rate": 100, "warehouse": MAIN}]})

    try:
        frappe.get_doc({"doctype": "Item", "item_code": prefix, "item_group": "Products",
            "stock_uom": "Nos", "is_stock_item": 1}).insert()
        submit(frappe.get_doc({"doctype": "Stock Entry", "stock_entry_type": "Material Receipt",
            "company": COMPANY, "items": [{"item_code": prefix, "qty": 10,
                "t_warehouse": MAIN, "basic_rate": 10}]}))
        sale = order(4).insert()
        assert state(sale)["conclusion"] == "Чернетка — замовлення не проведено"
        sale.submit()
        assert flt(frappe.db.get_value("Bin", {"item_code": prefix, "warehouse": MAIN}, "reserved_qty")) == 4
        delivery = make_delivery_note(sale.name)
        delivery.items[0].qty = 1
        submit(delivery)
        assert state(sale)["shipment"] == "Відвантажено частково"
        assert flt(frappe.db.get_value("Bin", {"item_code": prefix, "warehouse": MAIN}, "reserved_qty")) == 3
        submit(make_delivery_note(sale.name))
        invoice = submit(make_sales_invoice(sale.name))
        sale.reload()
        assert sale.status == "Completed", "Fixture must reproduce native billed/shipped completion"
        assert state(sale)["payment"] == "Очікується оплата"
        assert state(sale)["conclusion"] == "Не завершено: є неврегульовані розрахунки"
        checks.append("Draft, submission reservation, partial shipment, native Completed with unpaid invoice")

        pay("Sales Invoice", invoice.name, 100)
        assert state(sale)["payment"] == "Очікується оплата"
        pay("Sales Invoice", invoice.name)
        assert state(sale)["payment"] == "Рахунки погашено"
        assert state(sale)["conclusion"] == "Перевірте оплату та інші умови завершення"
        checks.append("Partial/full payment distinct from billing; no unsupported automatic completion")

        returned = make_sales_return(delivery.name)
        returned.items[0].qty = -1
        # Preserve only return_against to exercise historical imports without SO links.
        returned.items[0].against_sales_order = None
        returned.items[0].so_detail = None
        returned.insert()
        assert state(sale)["conclusion"] == "Завершення потребує перевірки повернень"
        returned.submit()
        assert state(sale)["returns"] == "Є повернення — перевірте товар і кошти"
        credit = make_credit(invoice.name)
        credit.items[0].qty = -1
        submit(credit)
        credit.reload()
        assert flt(credit.outstanding_amount) < 0
        assert payment_status(sale, {credit.name}, {credit.name: credit}) == "Очікується повернення або залік коштів"
        pay("Sales Invoice", credit.name)
        assert state(sale)["conclusion"] == "Завершення потребує перевірки повернень"
        checks.append("Draft/submitted goods returns via original delivery; refund debt and refund remain reviewable")

        advance_sale = submit(order(2))
        pay("Sales Order", advance_sale.name, 50)
        assert state(advance_sale)["payment"] == "Є аванс; рахунок не виставлено"
        assert state(advance_sale)["shipment"] == "Не відвантажено"
        cancelled = submit(order(1))
        reserved = flt(frappe.db.get_value("Bin", {"item_code": prefix, "warehouse": MAIN}, "reserved_qty"))
        cancelled.cancel()
        assert flt(frappe.db.get_value("Bin", {"item_code": prefix, "warehouse": MAIN}, "reserved_qty")) == reserved - 1
        assert state(cancelled)["conclusion"] == "Замовлення скасовано"
        checks.append("Order advance is not full completion; cancellation releases native demand reservation")

        frappe.db.set_single_value("Stock Settings", "enable_stock_reservation", 1)
        reserved_sale = order(1)
        reserved_sale.reserve_stock = reserved_sale.items[0].reserve_stock = 1
        submit(reserved_sale)
        assert flt(frappe.db.get_value("Bin", {"item_code": prefix, "warehouse": MAIN}, "reserved_stock")) == 1
        reserved_sale.cancel()
        assert flt(frappe.db.get_value("Bin", {"item_code": prefix, "warehouse": MAIN}, "reserved_stock")) == 0
        closed_sale = submit(order(1))
        closed_sale.update_status("Closed")
        assert state(closed_sale)["conclusion"] == "Замовлення закрито — перевірте розрахунки та причину закриття"
        checks.append("Native physical reservation releases on cancellation; short closure is not business completion")

        user = frappe.get_doc({"doctype": "User", "email": prefix.lower() + "@example.test",
            "first_name": "Completion verification", "enabled": 1, "send_welcome_email": 0,
            "roles": [{"role": "Sales User"}]}).insert()
        frappe.get_doc({"doctype": "User Permission", "user": user.name, "allow": "Sales Invoice",
            "for_value": credit.name, "apply_to_all_doctypes": 1}).insert()
        frappe.clear_cache(user=user.name)
        frappe.set_user(user.name)
        assert state(sale)["payment"] == "Немає доступу до оплат"
        frappe.set_user("Guest")
        try:
            state(sale)
        except frappe.PermissionError:
            pass
        else:
            raise AssertionError("Guest accessed completion check")
        checks.append("Restricted invoice visibility and guest rejection")
    finally:
        frappe.set_user(original_user)
        frappe.db.rollback()
        frappe.flags.in_test = original_test
        frappe.clear_cache()
    assert not frappe.db.exists("Item", prefix)
    return {"passed": len(checks), "checks": checks, "test_data": "rolled back"}
