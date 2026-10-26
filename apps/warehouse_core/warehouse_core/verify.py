"""Real ERPNext integration checks, rolled back and restricted to the demo site."""
import uuid

import frappe
from frappe.utils import flt, nowdate

from warehouse_core.demo import COMPANY, MAIN, SECONDARY, CUSTOMER, SUPPLIER
from warehouse_core.install import ROLE_PROFILES


def run():
    if not frappe.conf.get("warehouse_demo") or not frappe.db.exists("Company", COMPANY):
        frappe.throw("Run these checks only on the explicitly configured demo site")
    from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt
    from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_invoice
    from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
    from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_invoice, make_sales_return
    from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

    suffix = uuid.uuid4().hex[:10]
    code = f"VERIFY-{suffix}"
    checks = []
    original_user = frappe.session.user
    original_test = frappe.flags.in_test
    frappe.flags.in_test = True
    frappe.set_user("Administrator")

    from warehouse_core.dashboard_charts import get_charts

    def chart_totals():
        return {chart["key"]: [sum(dataset["values"]) for dataset in chart["datasets"]]
                for chart in get_charts([COMPANY])["charts"]}

    initial_charts = chart_totals()

    def submit(doc):
        doc.insert()
        doc.submit()
        return doc

    def quantity(warehouse):
        return flt(frappe.db.get_value("Bin", {"item_code": code, "warehouse": warehouse}, "actual_qty"))

    def pay(invoice):
        payment = get_payment_entry(invoice.doctype, invoice.name)
        payment.reference_no = "DEMO-VERIFY"
        payment.reference_date = nowdate()
        submit(payment)
        invoice.reload()
        assert flt(invoice.outstanding_amount) == 0, "Invoice was not settled"

    try:
        item = frappe.get_doc({"doctype": "Item", "item_code": code, "item_name": code,
            "item_group": "Products", "stock_uom": "Nos", "is_stock_item": 1,
            "custom_moysklad_id": f"verify-{suffix}",
            "item_defaults": [{"company": COMPANY, "default_warehouse": MAIN}]}).insert()
        assert not frappe.copy_doc(item, ignore_no_copy=False).custom_moysklad_id, "Source ID was copied to another product"
        frappe.db.savepoint("duplicate_source")
        try:
            frappe.get_doc({"doctype": "Item", "item_code": code + "-duplicate",
                "item_group": "Products", "stock_uom": "Nos",
                "custom_moysklad_id": f"verify-{suffix}"}).insert()
        except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
            frappe.db.rollback(save_point="duplicate_source")
        else:
            raise AssertionError("Duplicate source ID was accepted")
        checks.append("Migration source IDs are unique and are not copied")

        purchase = submit(frappe.get_doc({"doctype": "Purchase Order", "supplier": SUPPLIER,
            "company": COMPANY, "schedule_date": nowdate(), "buying_price_list": "Standard Buying",
            "items": [{"item_code": code, "qty": 10, "rate": 100, "warehouse": MAIN}]}))
        receipt = submit(make_purchase_receipt(purchase.name))
        assert quantity(MAIN) == 10, "Purchase receipt did not add 10 units"
        purchase_invoice = submit(make_purchase_invoice(receipt.name))
        assert flt(purchase_invoice.grand_total) == 1000
        pay(purchase_invoice)
        checks.append("Purchase order → receipt → supplier invoice → payment")

        sale = submit(frappe.get_doc({"doctype": "Sales Order", "customer": CUSTOMER,
            "company": COMPANY, "delivery_date": nowdate(), "selling_price_list": "Standard Selling",
            "items": [{"item_code": code, "qty": 4, "rate": 150, "warehouse": MAIN}]}))
        delivery = submit(make_delivery_note(sale.name))
        assert quantity(MAIN) == 6, "Delivery did not deduct 4 units"
        invoice = submit(make_sales_invoice(delivery.name))
        assert flt(invoice.grand_total) == 600
        pay(invoice)
        assert quantity(MAIN) == 6, "Invoice duplicated stock movement"
        checks.append("Sales order → delivery → customer invoice → payment; no double stock deduction")

        totals = chart_totals()
        for key, expected in {"sales": [600], "purchases": [1000], "payments": [600, 1000], "stock": [600]}.items():
            for index, delta in enumerate(expected):
                assert abs(totals[key][index] - initial_charts[key][index] - delta) < 0.01, (key, totals)
        from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return as make_credit_note
        credit = make_credit_note(invoice.name)
        credit.items[0].qty = -1
        credit.update_stock = 0
        submit(credit)
        assert abs(chart_totals()["sales"][0] - initial_charts["sales"][0] - 450) < 0.01, "Chart did not deduct sales return"
        credit.cancel()
        assert abs(chart_totals()["sales"][0] - initial_charts["sales"][0] - 600) < 0.01, "Cancelled return still counted"
        draft = frappe.get_doc({"doctype": "Sales Invoice", "company": COMPANY, "customer": CUSTOMER,
            "items": [{"item_code": code, "qty": 1, "rate": 99999, "warehouse": MAIN}]}).insert()
        assert draft.docstatus == 0
        assert abs(chart_totals()["sales"][0] - initial_charts["sales"][0] - 600) < 0.01, "Draft invoice counted as sales"
        checks.append("Chart totals reconcile sales, purchases, receipts, payments and stock; returns deducted; drafts/cancellations excluded")

        submit(frappe.get_doc({"doctype": "Stock Entry", "stock_entry_type": "Material Transfer",
            "company": COMPANY, "items": [{"item_code": code, "qty": 2,
                "s_warehouse": MAIN, "t_warehouse": SECONDARY}]}))
        assert (quantity(MAIN), quantity(SECONDARY)) == (4, 2)
        returned = make_sales_return(delivery.name)
        returned.items[0].qty = -1
        submit(returned)
        assert (quantity(MAIN), quantity(SECONDARY)) == (5, 2)
        checks.append("Warehouse transfer and partial sales return update balances")

        submit(frappe.get_doc({"doctype": "Stock Reconciliation", "company": COMPANY,
            "purpose": "Stock Reconciliation", "items": [{"item_code": code,
                "warehouse": MAIN, "qty": 7, "valuation_rate": 100}]}))
        assert quantity(MAIN) == 7
        checks.append("Stock reconciliation sets the counted quantity")

        from erpnext.stock.stock_ledger import NegativeStockError
        frappe.db.savepoint("negative_stock")
        try:
            submit(frappe.get_doc({"doctype": "Stock Entry", "stock_entry_type": "Material Issue",
                "company": COMPANY, "items": [{"item_code": code, "qty": 99999,
                    "s_warehouse": MAIN}]}))
        except NegativeStockError:
            frappe.db.rollback(save_point="negative_stock")
        else:
            raise AssertionError("Stock issue allowed negative inventory")
        checks.append("Insufficient-stock issue is rejected")

        for profile, expected_doc in [("Warehouse Sales", "Sales Order"),
                                      ("Warehouse Purchasing", "Purchase Order"),
                                      ("Warehouse Staff", "Stock Entry")]:
            email = f"verify-{profile.split()[-1].lower()}-{suffix}@example.test"
            user = frappe.get_doc({"doctype": "User", "email": email, "first_name": "Verification",
                "enabled": 1, "send_welcome_email": 0, "user_type": "System User",
                "role_profile_name": profile,
                "roles": [{"role": role} for role in ROLE_PROFILES[profile]]}).insert()
            frappe.set_user(user.name)
            assert frappe.has_permission(expected_doc, "create"), f"{profile} cannot create its document"
            assert not frappe.has_permission("User", "create"), f"{profile} can create users"
            assert not frappe.has_permission("User", "write", doc="Administrator"), f"{profile} can administer other users"
            assert not frappe.has_permission("Payment Entry", "submit"), f"{profile} can submit payments"
            frappe.set_user("Administrator")
        checks.append("Sales, purchasing and warehouse roles work without user administration or payment submission")
    finally:
        frappe.set_user(original_user)
        frappe.db.rollback()
        frappe.flags.in_test = original_test
    assert not frappe.db.exists("Item", code), "Verification data was not rolled back"
    return {"passed": len(checks), "checks": checks, "test_data": "rolled back"}
