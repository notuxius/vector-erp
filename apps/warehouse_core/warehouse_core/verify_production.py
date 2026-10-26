"""Rollback-only checks using native submitted manufacturing and stock documents."""
import hashlib
import json
import uuid

import frappe
from frappe.utils import add_days, get_datetime, now_datetime, nowdate

from warehouse_core.demo import COMPANY, MAIN, SECONDARY
from warehouse_core.warehouse_core.report.expected_production.expected_production import execute
from warehouse_core.warehouse_core.doctype.machine_assembly_installation.machine_assembly_installation import remove_assembly
from warehouse_core.assemblies import get_assemblies, serial_query


def snapshot():
    """Stable deployment comparison: identities, quantities, accounting and roles."""
    payload = {}
    for dt, fields in {
        "Item": ["name", "has_serial_no"], "Serial No": ["name", "item_code", "company", "warehouse"],
        "Warehouse": ["name", "company"], "Company": ["name", "default_currency"],
        "Sales Order": ["name", "docstatus", "grand_total"],
        "Purchase Order": ["name", "docstatus", "grand_total"],
        "Stock Ledger Entry": ["name", "actual_qty", "stock_value_difference"],
        "GL Entry": ["name", "debit", "credit"],
        "Has Role": ["parent", "role"],
        "Work Order": ["name", "docstatus", "qty", "produced_qty"],
    }.items():
        rows = frappe.get_all(dt, fields=fields, order_by="name", limit_page_length=0)
        encoded = json.dumps(rows, sort_keys=True, default=str).encode()
        payload[dt] = {"count": len(rows), "sha256": hashlib.sha256(encoded).hexdigest()}
    return payload


def run():
    if not frappe.conf.get("warehouse_demo") or not frappe.db.exists("Company", COMPANY):
        frappe.throw("Run these checks only on the explicitly configured demo site")
    from erpnext.manufacturing.doctype.work_order.work_order import make_stock_entry, stop_unstop
    from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt
    from warehouse_core.demo import SUPPLIER, CUSTOMER

    original_user, original_test = frappe.session.user, frappe.flags.in_test
    frappe.set_user("Administrator")
    frappe.flags.in_test = True
    prefix = "VERIFY-PROD-" + uuid.uuid4().hex[:8]
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
            raise AssertionError("Invalid operation accepted")

    def rows(item, **extra):
        return execute({"company": COMPANY, "item_code": item, **extra})[1]

    def item(suffix, **extra):
        return frappe.get_doc({"doctype": "Item", "item_code": prefix + suffix,
            "item_group": "Products", "stock_uom": "Nos", "is_stock_item": 1, **extra}).insert()

    def install(machine, assembly, **extra):
        return frappe.get_doc({"doctype": "Machine Assembly Installation", "company": COMPANY,
            "machine_serial": machine, "assembly_serial": assembly,
            "installed_at": now_datetime(), "position": "Рама", **extra})

    try:
        component = item("-PART")
        assembly_item = item("-ASSEMBLY", custom_is_serialized_assembly=1, serial_no_series=prefix + "-A-.####")
        machine_item = item("-MACHINE", custom_is_machine=1, serial_no_series=prefix + "-M-.####")
        assert assembly_item.has_serial_no and machine_item.has_serial_no and not component.has_serial_no
        reject(lambda: item("-INVALID", custom_is_machine=1, custom_is_serialized_assembly=1))
        submit(frappe.get_doc({"doctype": "Stock Entry", "stock_entry_type": "Material Receipt", "company": COMPANY,
            "items": [{"item_code": component.name, "qty": 30, "t_warehouse": MAIN, "basic_rate": 10}]}))
        bom = submit(frappe.get_doc({"doctype": "BOM", "company": COMPANY, "item": assembly_item.name,
            "quantity": 1, "currency": "UAH", "items": [{"item_code": component.name, "qty": 2, "rate": 10}]}))

        def work(qty, warehouse=MAIN):
            doc = frappe.get_doc({"doctype": "Work Order", "company": COMPANY,
                "production_item": assembly_item.name, "bom_no": bom.name, "qty": qty,
                "source_warehouse": MAIN, "wip_warehouse": SECONDARY, "fg_warehouse": warehouse,
                "skip_transfer": 1, "from_wip_warehouse": 0})
            doc.get_items_and_operations_from_bom()
            return doc.insert()

        first = work(3)
        assert rows(assembly_item.name) == [], "Draft output was counted"
        first.submit()
        def production_reserved():
            return frappe.db.get_value("Bin", {"item_code": component.name, "warehouse": MAIN},
                "reserved_qty_for_production")
        assert production_reserved() == 6
        second = work(2)
        second.submit()
        assert production_reserved() == 10
        assert rows(assembly_item.name)[0]["expected_qty"] == 5
        assert rows(assembly_item.name)[0]["order_count"] == 2
        assert rows(assembly_item.name)[0]["actual_qty"] == 0
        details = rows(assembly_item.name, view="Виробничі замовлення")
        assert {r["work_order"] for r in details} == {first.name, second.name}
        assert sum(r["expected_qty"] for r in details) == 5
        second.cancel()
        assert production_reserved() == 6
        assert rows(assembly_item.name)[0]["expected_qty"] == 3
        manufactured = submit(frappe.get_doc(make_stock_entry(first.name, "Manufacture", 1)))
        assert rows(assembly_item.name)[0]["expected_qty"] == 2
        assert rows(assembly_item.name)[0]["actual_qty"] == 1
        assert frappe.db.get_value("Bin", {"item_code": component.name, "warehouse": MAIN}, "actual_qty") == 28
        assert production_reserved() == 4
        stop_unstop(first.name, "Stopped")
        assert production_reserved() == 0
        assert rows(assembly_item.name) == []
        stop_unstop(first.name, "Not Started")
        assert production_reserved() == 4
        assert rows(assembly_item.name)[0]["expected_qty"] == 2
        checks.append("Draft/submitted/cancelled/stopped/restarted work orders; grouped and detail totals; partial manufacture consumes stock once")
        checks.append("Production component demand is reserved on submit, reduced by output and released on cancellation/stop")

        po = submit(frappe.get_doc({"doctype": "Purchase Order", "company": COMPANY, "supplier": SUPPLIER,
            "transaction_date": nowdate(), "schedule_date": nowdate(),
            "items": [{"item_code": assembly_item.name, "qty": 4, "rate": 20, "warehouse": MAIN,
                       "schedule_date": nowdate()}]}))
        assert rows(assembly_item.name)[0]["ordered_qty"] == 4
        receipt = make_purchase_receipt(po.name)
        receipt.items[0].qty = 1
        submit(receipt)
        assert rows(assembly_item.name)[0]["ordered_qty"] == 3
        assert rows(assembly_item.name)[0]["actual_qty"] == 2
        assert rows(assembly_item.name)[0]["expected_qty"] == 2
        checks.append("Purchasing expectations stay separate, partial receipt changes physical/purchase quantities only")

        frappe.db.set_single_value("Stock Settings", "enable_stock_reservation", 1)
        sale = submit(frappe.get_doc({"doctype": "Sales Order", "company": COMPANY, "customer": CUSTOMER,
            "delivery_date": nowdate(), "items": [{"item_code": assembly_item.name, "qty": 1,
                "rate": 50, "warehouse": MAIN, "delivery_date": nowdate()}]}))
        frappe.db.set_value("Sales Order Item", sale.items[0].name, "reserve_stock", 1)
        sale.reload()
        sale.create_stock_reservation_entries(items_details=[{"sales_order_item": sale.items[0].name,
            "warehouse": MAIN, "qty_to_reserve": 1}], notify=False)
        assert rows(assembly_item.name)[0]["reserved_stock"] == 1
        assert rows(assembly_item.name)[0]["actual_qty"] == 2
        assert rows(assembly_item.name)[0]["expected_qty"] == 2
        checks.append("Physical reservation remains distinct from physical stock and future production")

        # Record a native process loss with a second manufacturing stock entry.
        loss_entry = frappe.get_doc(make_stock_entry(first.name, "Manufacture", 2))
        loss_entry.process_loss_qty = 1
        loss_entry.process_loss_percentage = 50
        for row in loss_entry.items:
            if row.is_finished_item:
                row.qty = 1
        submit(loss_entry)
        first.reload()
        assert first.produced_qty == 2 and first.process_loss_qty == 1
        assert rows(assembly_item.name) == [], "Completed production with loss is still expected"
        loss_entry.cancel()
        assert rows(assembly_item.name)[0]["expected_qty"] == 2
        checks.append("Native process loss completes remaining output; cancelling manufacture restores expected output")

        partial_loss = work(5)
        partial_loss.submit()
        partial_entry = frappe.get_doc(make_stock_entry(partial_loss.name, "Manufacture", 2))
        partial_entry.process_loss_qty, partial_entry.process_loss_percentage = 1, 50
        for row in partial_entry.items:
            if row.is_finished_item:
                row.qty = 1
        submit(partial_entry)
        detail = next(row for row in rows(assembly_item.name, view="Виробничі замовлення")
                      if row["work_order"] == partial_loss.name)
        assert detail["expected_qty"] == 3 and detail["process_loss_qty"] == 1
        partial_entry.cancel()
        partial_loss.reload().cancel()
        checks.append("Partial process losses reduce outstanding expectations before order completion")

        serials = frappe.get_all("Serial No", filters={"item_code": assembly_item.name, "warehouse": MAIN}, pluck="name")
        submit(frappe.get_doc({"doctype": "Stock Entry", "stock_entry_type": "Material Receipt", "company": COMPANY,
            "items": [{"item_code": machine_item.name, "qty": 2, "t_warehouse": MAIN, "basic_rate": 100}]}))
        machines = frappe.get_all("Serial No", filters={"item_code": machine_item.name}, pluck="name")
        assembly, machine, replacement = serials[0], machines[0], machines[1]
        machine_options = serial_query("Serial No", prefix, "name", 0, 50, {"company": COMPANY, "kind": "machine"})
        assembly_options = serial_query("Serial No", prefix, "name", 0, 50, {"company": COMPANY, "kind": "assembly"})
        assert {row[0] for row in machine_options} == set(machines)
        assert assembly in {row[0] for row in assembly_options}
        assert not (set(machines) & {row[0] for row in assembly_options})
        # Shared physical number never overwrites globally unique stock identities.
        for name in (assembly, machine):
            serial = frappe.get_doc("Serial No", name)
            serial.custom_manufacturer_serial_no = "FRAME-100"
            serial.save()
        assert assembly != machine
        link = submit(install(machine, assembly))
        assert link.installed_by == "Administrator"
        assert get_assemblies(machine)["rows"][0].assembly_serial == assembly
        assert get_assemblies(assembly)["rows"][0].machine_serial == machine
        reject(lambda: submit(install(replacement, assembly)))
        reject(lambda: submit(install(machine, machine)))
        reject(lambda: submit(install(assembly, serials[1])))
        reject(lambda: submit(install(machine, serials[1], installed_at=add_days(nowdate(), 1))))
        reject(lambda: remove_assembly(link.name, " "))
        forged = frappe.get_doc(link.doctype, link.name)
        forged.removed_at = now_datetime()
        reject(forged.save)
        remove_assembly(link.name, "Заміна під час ремонту")
        link.reload()
        assert link.removed_by == "Administrator" and link.removal_reason == "Заміна під час ремонту"
        reject(lambda: remove_assembly(link.name, "Повторне зняття"))
        reject(link.cancel)
        reject(lambda: submit(install(replacement, assembly, installed_at=add_days(nowdate(), -1))))
        next_link = submit(install(replacement, assembly))
        assert get_assemblies(machine)["rows"] == []
        assert get_assemblies(assembly)["rows"][0].machine_serial == replacement
        assert frappe.db.count(link.doctype, {"assembly_serial": assembly, "docstatus": 1}) == 2
        next_link.cancel()
        assert get_assemblies(assembly)["rows"] == []
        reject(next_link.delete)
        assembly_item.custom_is_serialized_assembly = 0
        reject(assembly_item.save)
        checks.append("Two-way serial links, duplicate/self/wrong-type/future-date rejection, removal/reinstallation history, immutable actors and completed history")

        user = frappe.get_doc({"doctype": "User", "email": prefix.lower() + "@example.test",
            "first_name": "Production verification", "send_welcome_email": 0, "enabled": 1,
            "roles": [{"role": "Stock User"}]}).insert()
        frappe.set_user(user.name)
        allowed = submit(install(machine, serials[1]))
        assert allowed.installed_by == user.name
        remove_assembly(allowed.name, "Перевірка прав")
        assert frappe.get_doc(allowed.doctype, allowed.name).removed_by == user.name
        assert rows(assembly_item.name)
        assert frappe.get_list(link.doctype, filters={"assembly_serial": assembly})
        frappe.set_user("Administrator")
        frappe.get_doc({"doctype": "User Permission", "user": user.name, "allow": "Warehouse",
            "for_value": SECONDARY, "apply_to_all_doctypes": 1}).insert()
        frappe.clear_cache(user=user.name)
        frappe.set_user(user.name)
        assert rows(assembly_item.name) == []
        assert frappe.get_list(link.doctype, filters={"assembly_serial": assembly}) == []
        assert not frappe.has_permission(link.doctype, "read", doc=link.name)
        assert not serial_query("Serial No", prefix, "name", 0, 50, {"company": COMPANY, "kind": "machine"})
        reject(lambda: get_assemblies(machine), frappe.PermissionError)
        reject(lambda: install(machine, assembly).insert(), frappe.PermissionError)
        frappe.set_user("Guest")
        reject(lambda: rows(assembly_item.name), frappe.PermissionError)
        reject(lambda: get_assemblies(machine), frappe.PermissionError)
        checks.append("Stock User can record installations/removals; warehouse restrictions cover reports, list, detail, APIs; guest rejected")
    finally:
        frappe.set_user(original_user)
        frappe.db.rollback()
        frappe.flags.in_test = original_test
        frappe.clear_cache()
    assert not frappe.db.exists("Item", prefix + "-ASSEMBLY")
    return {"passed": len(checks), "checks": checks, "test_data": "rolled back"}
