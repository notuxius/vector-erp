"""Explicit, repeatable cleanup of the local sample site's legacy DEMO names.

Use native renames (never merges) to retain links, permissions and ledger rows.
Historical comments/version payloads and external MoySklad IDs remain evidence.
This is an administrator CLI operation, not an install hook or web endpoint.
"""
import re

import frappe


from warehouse_core.company_identity import COMPANY
COMPANY_TYPES = (
    "Department", "Account", "Cost Center", "Item Tax Template",
    "Sales Taxes and Charges Template", "Purchase Taxes and Charges Template",
)
SAMPLE_TYPES = (
    "Customer", "Supplier", "Item", "Sales Person", "Serial No", "BOM",
    "Work Order", "Stock Entry", "Sales Order", "Purchase Order",
    "Delivery Note", "Maintenance Visit",
)
CAPTION_FIELDS = {
    "item_name", "customer_name", "supplier_name", "account_name",
    "cost_center_name", "sales_person_name", "serial_no", "serial_no_series",
    "remarks", "against", "email_id",
}
NUMERIC_FIELDS = {
    "GL Entry": ["debit", "credit", "debit_in_account_currency", "credit_in_account_currency"],
    "Stock Ledger Entry": ["actual_qty", "qty_after_transaction", "stock_value", "stock_value_difference"],
    "Bin": ["actual_qty", "reserved_qty", "ordered_qty", "projected_qty", "stock_value"],
    "Sales Order": ["docstatus", "grand_total", "total_qty"],
    "Purchase Order": ["docstatus", "grand_total", "total_qty"],
    "Delivery Note": ["docstatus", "grand_total", "total_qty"],
    "Stock Entry": ["docstatus", "total_incoming_value", "total_outgoing_value"],
    "BOM": ["docstatus", "quantity", "total_cost"],
    "Work Order": ["docstatus", "qty", "produced_qty"],
    "Maintenance Visit": ["docstatus"],
}


def clean(value):
    if value == "DEMO — Склад і торгівля":
        return COMPANY
    value = value.replace(" - DEMO", " - MTech")
    value = re.sub(r"^DEMO(?: — |-|: | )", "", value)
    return re.sub(r"^demo-worker-(\d+)@example\.test$", r"worker-\1@example.test", value)


def plan():
    changes = []
    for doctype in COMPANY_TYPES + SAMPLE_TYPES:
        filters = {"name": ["like", "%DEMO%"]}
        if doctype in COMPANY_TYPES:
            filters["company"] = COMPANY
        for old in frappe.get_all(doctype, filters=filters, pluck="name"):
            new = clean(old)
            # The root cost center embeds the old company name as well.
            if old == "DEMO — Склад і торгівля - DEMO":
                new = COMPANY + " - MTech"
            if new != old:
                changes.append((doctype, old, new))
    for slot in range(1, 6):
        old = f"demo-worker-{slot}@example.test"
        if frappe.db.exists("User", old):
            if frappe.db.get_value("User", old, "custom_demo_slot") != slot:
                frappe.throw(f"Unexpected example user: {old}")
            if frappe.db.get_value("User", old, "enabled") or frappe.db.exists("Sessions", {"user": old}):
                frappe.throw(f"Example user must be disabled and logged out before renaming: {old}")
            changes.append(("User", old, clean(old)))
    targets = set()
    for doctype, old, new in changes:
        if frappe.db.exists(doctype, new) or (doctype, new) in targets:
            frappe.throw(f"{doctype} {new} already exists; refusing to merge records")
        targets.add((doctype, new))
    return changes


def snapshot(changes=()):
    """Compare each financial row, hierarchy edge and role assignment by identity."""
    mapping = {(dt, old): new for dt, old, new in changes}

    def mapped(dt, name):
        return mapping.get((dt, name), name)

    state = {}
    for dt in COMPANY_TYPES + SAMPLE_TYPES + ("User", "Has Role", "GL Entry", "Stock Ledger Entry", "Bin"):
        state[dt + ":count"] = frappe.db.count(dt)
    for dt, fields in NUMERIC_FIELDS.items():
        state[dt] = sorted((mapped(dt, row.name), tuple(row[f] for f in fields))
                           for row in frappe.get_all(dt, fields=["name", *fields]))
    state["roles"] = sorted((mapped("User", row.parent) if row.parenttype == "User" else row.parent,
                             row.parenttype, row.role)
                            for row in frappe.get_all("Has Role", fields=["parent", "parenttype", "role"]))
    for dt, parent in (("Department", "parent_department"), ("Account", "parent_account"),
                       ("Cost Center", "parent_cost_center")):
        state[dt + ":hierarchy"] = sorted((mapped(dt, row.name), mapped(dt, row[parent]), row.is_group)
                                         for row in frappe.get_all(dt, fields=["name", parent, "is_group"]))
    return state


def apply(dry_run=True):
    frappe.only_for("System Manager")
    if not frappe.conf.get("warehouse_demo") or not frappe.db.exists("Company", COMPANY):
        frappe.throw("Name cleanup requires the explicitly configured local sample company")
    changes = plan()
    if dry_run:
        return {"renames": changes, "count": len(changes)}
    before = snapshot(changes)
    for doctype, old, new in changes:
        actual = frappe.rename_doc(doctype, old, new, force=True, merge=False,
                                   show_alert=False, rebuild_search=False)
        if actual != new:
            raise AssertionError(f"Unexpected rename result: {doctype} {actual} != {new}")
        if doctype == "Serial No":
            frappe.db.set_value(doctype, new, "serial_no", new, update_modified=False)

    # Native Link/Dynamic Link fields are already migrated. Refresh copied
    # captions and the few non-Link serial/account fields used by printouts.
    changed_fields = 0
    for dt in frappe.get_all("DocType", filters={"issingle": 0, "is_virtual": 0}, pluck="name"):
        if dt in {"Version", "Comment", "Communication", "Activity Log", "Error Log", "Deleted Document"}:
            continue
        if not frappe.db.table_exists(dt) or not frappe.db.count(dt):
            continue
        for field in frappe.get_meta(dt).fields:
            if field.fieldname not in CAPTION_FIELDS or field.fieldtype in {"Link", "Dynamic Link"}:
                continue
            if not frappe.db.has_column(dt, field.fieldname):
                continue
            for row in frappe.get_all(dt, filters={field.fieldname: ["like", "%DEMO%"]},
                                     fields=["name", field.fieldname]):
                old = row[field.fieldname]
                new = clean(old)
                if new != old:
                    frappe.db.set_value(dt, row.name, field.fieldname, new, update_modified=False)
                    changed_fields += 1
    # The public BOM route is a slug rather than a Link field.
    for dt, old, new in changes:
        if dt == "BOM" and frappe.db.get_value(dt, new, "route") == old.lower():
            frappe.db.set_value(dt, new, "route", new.lower(), update_modified=False)
    if snapshot() != before:
        raise AssertionError("Name cleanup changed balances, quantities, record counts, roles or hierarchy")
    if plan():
        raise AssertionError("Name cleanup is incomplete")
    frappe.clear_cache()
    for dt in {row[0] for row in changes}:
        frappe.enqueue("frappe.utils.global_search.rebuild_for_doctype", doctype=dt, enqueue_after_commit=True)
    return {"renamed": len(changes), "updated_captions": changed_fields,
            "balances_quantities_roles_hierarchy": "preserved"}
