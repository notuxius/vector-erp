"""Rollback-only integration checks for the confirmed CNC client workflows."""
import json
import uuid
from io import BytesIO

import frappe
from pypdf import PdfReader
from frappe.utils import flt, nowdate

from warehouse_core.demo import COMPANY, MAIN, SECONDARY, CUSTOMER
from warehouse_core.warehouse_core.report.daily_dispatch_list.daily_dispatch_list import execute as dispatch
from warehouse_core.warehouse_core.report.machine_service_history.machine_service_history import execute as history


def run():
    if not frappe.conf.get("warehouse_demo") or not frappe.db.exists("Company", COMPANY):
        frappe.throw("Run these checks only on the explicitly configured demo site")
    from erpnext.manufacturing.doctype.work_order.work_order import make_stock_entry
    from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note

    suffix = uuid.uuid4().hex[:10]
    prefix = "VERIFY-CNC-" + suffix
    original_user, original_test = frappe.session.user, frappe.flags.in_test
    frappe.set_user("Administrator")
    frappe.flags.in_test = True
    checks = []

    def submit(doc):
        doc.insert()
        doc.submit()
        return doc

    def reject(call, exception=frappe.ValidationError):
        point = "reject_" + uuid.uuid4().hex[:8]
        frappe.db.savepoint(point)
        try:
            call()
        except exception:
            frappe.db.rollback(save_point=point)
        else:
            raise AssertionError("Invalid operation was accepted")

    def qty(item, warehouse=MAIN):
        return flt(frappe.db.get_value("Bin", {"item_code": item, "warehouse": warehouse}, "actual_qty"))

    try:
        component = frappe.get_doc({"doctype": "Item", "item_code": prefix + "-PART", "item_group": "Products",
            "stock_uom": "Nos", "is_stock_item": 1}).insert()
        machine = frappe.get_doc({"doctype": "Item", "item_code": prefix, "item_group": "Products",
            "stock_uom": "Nos", "is_stock_item": 1, "custom_is_machine": 1,
            "serial_no_series": prefix + "-.####"}).insert()
        assert machine.has_serial_no and not component.has_serial_no
        receipt = submit(frappe.get_doc({"doctype": "Stock Entry", "stock_entry_type": "Material Receipt",
            "company": COMPANY, "items": [{"item_code": component.name, "qty": 10, "t_warehouse": MAIN, "basic_rate": 100}]}))
        bom = submit(frappe.get_doc({"doctype": "BOM", "item": machine.name, "quantity": 1,
            "company": COMPANY, "currency": "UAH", "items": [{"item_code": component.name, "qty": 2, "rate": 100}]}))
        work = frappe.get_doc({"doctype": "Work Order", "company": COMPANY, "production_item": machine.name,
            "bom_no": bom.name, "qty": 1, "source_warehouse": MAIN, "wip_warehouse": SECONDARY,
            "fg_warehouse": MAIN, "skip_transfer": 1, "from_wip_warehouse": 0})
        work.get_items_and_operations_from_bom()
        submit(work)
        manufactured = submit(frappe.get_doc(make_stock_entry(work.name, "Manufacture", 1)))
        assert qty(component.name) == 8 and qty(machine.name) == 1
        serial_name = frappe.db.get_value("Serial No", {"item_code": machine.name}, "name")
        assert serial_name and frappe.db.get_value("Serial No", serial_name, "warehouse") == MAIN
        work.reload()
        assert flt(work.produced_qty) == 1
        checks.append("BOM → work order → manufacture consumes components and creates one serialized machine")

        serial = frappe.get_doc("Serial No", serial_name)
        serial.custom_machine_configuration = "Тестова комплектація: блок керування, кабель"
        serial.save()
        salesperson = frappe.get_doc({"doctype": "Sales Person", "sales_person_name": prefix,
            "parent_sales_person": frappe.db.get_value("Sales Person", {"is_group": 1, "parent_sales_person": ["is", "not set"]}), "is_group": 0}).insert()
        service = submit(frappe.get_doc({"doctype": "Maintenance Visit", "customer": CUSTOMER,
            "company": COMPANY, "mntc_date": nowdate(), "completion_status": "Fully Completed",
            "maintenance_type": "Unscheduled", "purposes": [{"item_code": machine.name,
                "serial_no": serial_name, "service_person": salesperson.name, "work_done": "Перевірено підключення"}]}))
        rows = history({"company": COMPANY, "serial_no": serial_name})[1]
        assert any(row["visit"] == service.name for row in rows)
        invalid = frappe.copy_doc(service)
        invalid.purposes[0].item_code = component.name
        reject(invalid.insert)
        checks.append("Serial configuration and service history; mismatched machine serial is rejected")

        sale = submit(frappe.get_doc({"doctype": "Sales Order", "customer": CUSTOMER, "company": COMPANY,
            "delivery_date": nowdate(), "selling_price_list": "Standard Selling",
            "items": [{"item_code": component.name, "qty": 4, "rate": 150, "warehouse": MAIN}]}))
        filters = {"company": COMPANY, "date": nowdate(), "include_overdue": 1}
        assert next(row for row in dispatch(filters)[1] if row["sales_order"] == sale.name)["qty"] == 4
        delivery = make_delivery_note(sale.name)
        delivery.items[0].qty = 1
        delivery.custom_nova_poshta_ttn = "20400000000001"
        submit(delivery)
        assert next(row for row in dispatch(filters)[1] if row["sales_order"] == sale.name)["qty"] == 3
        assert not any(row["sales_order"] == sale.name for row in dispatch({**filters, "warehouse": SECONDARY})[1])
        delivery.custom_nova_poshta_ttn = "20400000000002"
        delivery.save()
        delivery.custom_nova_poshta_ttn = "invalid"
        reject(delivery.save)
        delivery.reload()
        assert delivery.custom_nova_poshta_ttn == "20400000000002"
        for format_name, doc, heading in (("Vector Payment Request", sale, "Рахунок на оплату"),
                                          ("Vector Delivery Note", delivery, "Видаткова накладна")):
            rendered = frappe.get_print(doc.doctype, doc.name, print_format=format_name)
            assert heading in rendered and doc.name in rendered
            assert frappe.db.get_value("Print Format", format_name, "pdf_generator") == "chrome"
            pdf = frappe.get_print(doc.doctype, doc.name, print_format=format_name, as_pdf=True)
            assert pdf.startswith(b"%PDF-")
            pdf_text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(pdf)).pages)
            assert heading in pdf_text and doc.name in pdf_text
        checks.append("Daily dispatch list subtracts partial shipments, filters warehouses; TTN and Ukrainian print forms work")

        email = f"verify-client-{suffix}@example.test"
        user = frappe.get_doc({"doctype": "User", "email": email, "first_name": "Verification",
            "enabled": 1, "send_welcome_email": 0, "user_type": "System User",
            "roles": [{"role": "Stock User"}]}).insert()
        issue = frappe.get_doc({"doctype": "Stock Entry", "stock_entry_type": "Material Issue",
            "company": COMPANY, "items": [{"item_code": component.name, "qty": 1, "s_warehouse": MAIN}]})
        issue.insert()  # Creator and submitting actor intentionally differ.
        frappe.set_user(user.name)
        issue.submit()
        assert not frappe.has_permission("Warehouse Operation Log", "read")
        reject(lambda: dispatch(filters), frappe.PermissionError) if not frappe.has_permission("Sales Order", "read") else None
        frappe.set_user("Administrator")
        log = frappe.get_doc("Warehouse Operation Log", {"reference_name": issue.name, "action": "Submit"})
        assert log.actor == user.name and issue.owner == "Administrator"
        assert json.loads(log.details)["items"][0]["s_warehouse"] == MAIN
        log.actor = "forged@example.test"
        reject(log.save, frappe.PermissionError)
        reject(log.delete, frappe.PermissionError)
        issue.cancel()
        assert frappe.db.exists("Warehouse Operation Log", {"reference_name": issue.name, "action": "Cancel", "actor": "Administrator"})
        assert qty(component.name) == 7
        checks.append("Audit records actual submit/cancel actor and warehouse; ordinary users cannot read or alter audit logs")

        # A warehouse-restricted user must never see another warehouse in the report.
        user.append("roles", {"role": "Sales User"})
        user.save()
        frappe.get_doc({"doctype": "User Permission", "user": user.name, "allow": "Warehouse",
                        "for_value": SECONDARY, "apply_to_all_doctypes": 1}).insert()
        frappe.clear_cache(user=user.name)
        frappe.set_user(user.name)
        assert not any(row["sales_order"] == sale.name for row in dispatch(filters)[1])
        checks.append("Dispatch respects warehouse User Permissions")
        frappe.set_user("Administrator")
        from warehouse_core.staff import seed_examples, apply_roster, example_email
        seed_examples()
        replacement = f"verify-real-{suffix}@example.org"
        roster = [{"slot": 2, "email": replacement, "first_name": "Реальний працівник (тест)",
                   "profiles": ["Warehouse Sales"], "enabled": False}]
        assert apply_roster(roster)["dry_run"] and not frappe.db.exists("User", replacement)
        apply_roster(roster, dry_run=False)
        apply_roster(roster, dry_run=False)
        assert frappe.db.get_value("User", example_email(2), "custom_replaced_by") == replacement
        assert not frappe.db.get_value("User", example_email(2), "enabled")
        assert "Sales User" in frappe.get_roles(replacement) and "System Manager" not in frappe.get_roles(replacement)
        reject(lambda: apply_roster([{**roster[0], "email": "different@example.org"}], dry_run=False))
        checks.append("Staff preview is read-only; replacement is repeatable, preserves old identity and grants only explicit roles")
    finally:
        frappe.set_user(original_user)
        frappe.db.rollback()
        frappe.flags.in_test = original_test
    assert not frappe.db.exists("Item", prefix)
    assert not frappe.db.exists("Warehouse Operation Log", {"reference_name": issue.name})
    return {"passed": len(checks), "checks": checks, "test_data": "rolled back"}
