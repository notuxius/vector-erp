"""Confirmed client rules, using the native stock and accounting ledgers."""
import json
import re

import frappe
from frappe.utils import now_datetime

AUDITED_DOCTYPES = (
    "Stock Entry", "Stock Reconciliation", "Purchase Receipt", "Delivery Note",
    "Sales Invoice", "Purchase Invoice", "Payment Entry", "Journal Entry",
    "Sales Order", "Purchase Order", "Work Order", "Maintenance Visit",
)


def validate_machine(doc, method=None):
    if doc.get("custom_is_machine") and doc.get("custom_is_serialized_assembly"):
        frappe.throw("Товар може бути верстатом або серійним вузлом, але не обома одночасно.")
    if doc.get("custom_is_machine") or doc.get("custom_is_serialized_assembly"):
        doc.has_serial_no = 1
        if not doc.is_stock_item:
            frappe.throw("Верстат або серійний вузол має бути складським товаром із серійним обліком.")
    if not doc.is_new() and frappe.db.exists("DocType", "Machine Assembly Installation"):
        for flag, field in (("custom_is_machine", "machine_serial"), ("custom_is_serialized_assembly", "assembly_serial")):
            if doc.has_value_changed(flag) and not doc.get(flag):
                linked = frappe.db.sql(f"""select installation.name
                    from `tabMachine Assembly Installation` installation
                    join `tabSerial No` serial on serial.name=installation.{field}
                    where serial.item_code=%s and installation.docstatus=1 limit 1""", doc.name)
                if linked:
                    frappe.throw("Не можна змінити тип товару, який має проведену історію встановлення вузлів.")


def validate_service(doc, method=None):
    for row in doc.purposes:
        if row.item_code and frappe.db.get_value("Item", row.item_code, "custom_is_machine") and not row.serial_no:
            frappe.throw("Для сервісного обслуговування верстата вкажіть серійний номер.")
        if row.serial_no:
            serial = frappe.db.get_value("Serial No", row.serial_no, ["item_code", "company"], as_dict=True)
            if not serial or serial.item_code != row.item_code or serial.company != doc.company:
                frappe.throw("Серійний номер має відповідати товару та компанії сервісного документа.")


def validate_shipping(doc, method=None):
    number = (doc.get("custom_nova_poshta_ttn") or "").strip()
    if number and not re.fullmatch(r"[0-9]{14}", number):
        frappe.throw("ТТН Нової пошти має містити 14 цифр.")
    doc.custom_nova_poshta_ttn = number
    from warehouse_core.nova_poshta import protect_delivery
    protect_delivery(doc, method)


def record_operation(doc, method=None):
    """Same transaction as the source event; never commit or enqueue separately.

    The actor is the session user, not the creator or a client-supplied field.
    Source references are text so an audit record survives source deletion.
    """
    if method not in ("on_submit", "on_cancel"):
        return
    details = {
        "purpose": doc.get("purpose") or doc.get("stock_entry_type"),
        "currency": doc.get("currency"), "grand_total": doc.get("grand_total"),
        "paid_amount": doc.get("paid_amount"),
        "items": [{key: row.get(key) for key in (
            "item_code", "qty", "stock_qty", "uom", "stock_uom", "warehouse",
            "s_warehouse", "t_warehouse", "serial_and_batch_bundle", "serial_no",
        ) if row.get(key) is not None} for row in (doc.get("items") or [])],
    }
    log = frappe.get_doc({
        "doctype": "Warehouse Operation Log", "reference_doctype": doc.doctype,
        "reference_name": doc.name, "company": doc.get("company"),
        "actor": frappe.session.user, "event_time": now_datetime(),
        "action": "Submit" if method == "on_submit" else "Cancel",
        "details": json.dumps(details, ensure_ascii=False, default=str),
    })
    log.flags.from_operation_hook = True
    log.insert(ignore_permissions=True)
