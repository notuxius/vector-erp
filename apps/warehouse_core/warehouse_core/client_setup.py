"""Repeatable configuration for the client's manufacturing and service workflows."""
import json
from pathlib import Path

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter

from warehouse_core.operations import AUDITED_DOCTYPES

CARDS = [
    ("Виробництво та сервіс", [
        ("Специфікації виробів", "BOM", "DocType"),
        ("Виробничі замовлення", "Work Order", "DocType"),
        ("Плани виробництва", "Production Plan", "DocType"),
        ("Серійні номери верстатів", "Serial No", "DocType"),
        ("Сервісні виїзди", "Maintenance Visit", "DocType"),
    ]),
    ("Відправлення та контроль", [
        ("Щоденний список для комірника", "Daily Dispatch List", "Report"),
        ("Журнал операцій", "Warehouse Operation Log", "DocType"),
        ("Історія сервісу верстата", "Machine Service History", "Report"),
    ]),
    ("Нова пошта", [
        ("Відправлення Нової пошти", "Nova Poshta Shipment", "DocType"),
        ("Налаштування Нової пошти", "Nova Poshta Settings", "DocType"),
    ]),
    ("Готовність замовлень", [
        ("Готовність замовлень до відправлення", "Order Readiness", "Report"),
    ]),
]


def apply():
    create_custom_fields({
        "Sales Order": [
            {"fieldname": "custom_completion_section", "label": "Контроль виконання",
             "fieldtype": "Section Break", "insert_after": "items"},
            {"fieldname": "custom_completion_check", "label": "Контроль виконання замовлення",
             "fieldtype": "HTML", "insert_after": "custom_completion_section"},
        ],
        "Item": [{"fieldname": "custom_is_machine", "label": "Верстат із серійним обліком",
                  "fieldtype": "Check", "insert_after": "item_group", "default": "0",
                  "description": "Для верстатів серійний облік обов’язковий. Не позначайте звичайні запчастини."},
                 {"fieldname": "custom_is_serialized_assembly", "label": "Вузол із серійним обліком",
                  "fieldtype": "Check", "insert_after": "custom_is_machine", "default": "0",
                  "description": "Окремо виготовлений серійний вузол або рама, які встановлюються у верстат."}],
        "Serial No": [{"fieldname": "custom_machine_configuration", "label": "Комплектація та видані комплектуючі",
                       "fieldtype": "Small Text", "insert_after": "description"},
                      {"fieldname": "custom_manufacturer_serial_no", "label": "Номер на виробі",
                       "fieldtype": "Data", "insert_after": "serial_no", "in_standard_filter": 1, "no_copy": 1,
                       "description": "Номер на табличці виробу або рами. Може збігатися; системні серійні номери залишаються унікальними."},
                      {"fieldname": "custom_technical_specifications", "label": "Технічні характеристики",
                       "fieldtype": "Small Text", "insert_after": "custom_machine_configuration"},
                      {"fieldname": "custom_installation_details", "label": "Монтаж і введення в експлуатацію",
                       "fieldtype": "Small Text", "insert_after": "custom_technical_specifications", "no_copy": 1},
                      {"fieldname": "custom_assembly_section", "label": "Серійні вузли та верстати",
                       "fieldtype": "Section Break", "insert_after": "custom_installation_details"},
                      {"fieldname": "custom_assembly_details", "label": "Встановлені вузли",
                       "fieldtype": "HTML", "insert_after": "custom_assembly_section"}],
        "Delivery Note": [
            {"fieldname": "custom_nova_poshta_ttn", "label": "ТТН Нової пошти",
             "fieldtype": "Data", "insert_after": "customer", "allow_on_submit": 1,
             "no_copy": 1, "in_standard_filter": 1,
             "description": "Внесіть існуючу ТТН або створіть її через меню «Нова пошта»."},
            {"fieldname": "custom_nova_poshta_status", "label": "Статус Нової пошти", "fieldtype": "Data",
             "insert_after": "custom_nova_poshta_ttn", "read_only": 1, "allow_on_submit": 1, "no_copy": 1},
            {"fieldname": "custom_nova_poshta_status_code", "label": "Код статусу Нової пошти", "fieldtype": "Data",
             "insert_after": "custom_nova_poshta_status", "read_only": 1, "allow_on_submit": 1, "no_copy": 1, "hidden": 1},
            {"fieldname": "custom_nova_poshta_checked_at", "label": "Статус перевірено", "fieldtype": "Datetime",
             "insert_after": "custom_nova_poshta_status_code", "read_only": 1, "allow_on_submit": 1, "no_copy": 1},
        ],
        "Company": [{"fieldname": "custom_payment_details", "label": "Платіжні реквізити для друку",
                     "fieldtype": "Small Text", "insert_after": "default_currency",
                     "description": "Назва отримувача, код ЄДРПОУ/РНОКПП, IBAN та банк — за реквізитами компанії."}],
        "User": [
            {"fieldname": "custom_demo_slot", "label": "Номер навчального працівника", "fieldtype": "Int", "read_only": 1, "no_copy": 1},
            {"fieldname": "custom_replaced_by", "label": "Замінено реальним користувачем", "fieldtype": "Link", "options": "User", "read_only": 1, "no_copy": 1},
        ],
    })
    for doctype in (*AUDITED_DOCTYPES, "Item", "Serial No", "BOM"):
        if not frappe.get_meta(doctype).track_changes:
            make_property_setter(doctype, None, "track_changes", "1", "Check", for_doctype=True)
    frappe.db.set_single_value("System Settings", "language", "uk")
    frappe.db.set_single_value("Stock Settings", "enable_serial_and_batch_no_for_item", 1)
    # Company country/currency and personal language choices are never overwritten.
    add_workspace_cards()
    install_print_formats()


def add_workspace_cards():
    workspace = frappe.get_doc("Workspace", "Warehouse Core")
    content = json.loads(workspace.content or "[]")
    changed = False
    for index, (label, entries) in enumerate(CARDS):
        marker = f"warehouse-client-card-{index}"
        if any(block.get("id") == marker for block in content):
            continue
        # An existing user-created card with the same title is also preserved.
        if any(link.type == "Card Break" and link.label == label for link in workspace.links):
            continue
        content.append({"id": marker, "type": "card", "data": {"card_name": label, "col": 4}})
        workspace.append("links", {"type": "Card Break", "label": label, "link_count": len(entries)})
        for title, target, kind in entries:
            workspace.append("links", {"type": "Link", "label": title, "link_to": target,
                                      "link_type": kind, "is_query_report": int(kind == "Report")})
        changed = True
    if changed:
        workspace.content = json.dumps(content, ensure_ascii=False)
        workspace.save(ignore_permissions=True)


def install_print_formats():
    root = Path(__file__).parent / "templates" / "print_formats"
    for name, doctype, filename in (
        ("Vector Payment Request", "Sales Order", "payment_request.html"),
        ("Vector Sales Invoice", "Sales Invoice", "payment_request.html"),
        ("Vector Delivery Note", "Delivery Note", "delivery_note.html"),
    ):
        # Do not overwrite a client's adjusted print form on subsequent migrations.
        if not frappe.db.exists("Print Format", name):
            frappe.get_doc({"doctype": "Print Format", "name": name, "doc_type": doctype,
                            "module": "Warehouse Core", "standard": "No", "custom_format": 1,
                            "default_print_language": "uk", "pdf_generator": "chrome",
                            "print_format_type": "Jinja", "html": (root / filename).read_text(encoding="utf-8"),
                            "disabled": 0}).insert(ignore_permissions=True)
        else:
            current = frappe.db.get_value(
                "Print Format", name, ["default_print_language", "pdf_generator"], as_dict=True
            )
            updates = {}
            if not current.default_print_language:
                updates["default_print_language"] = "uk"
            # Upgrade only the legacy renderer; preserve templates and explicit
            # choices of other engines on client-customized formats.
            if current.pdf_generator in (None, "", "wkhtmltopdf"):
                updates["pdf_generator"] = "chrome"
            if updates:
                frappe.db.set_value("Print Format", name, updates)
                frappe.clear_document_cache("Print Format", name)
