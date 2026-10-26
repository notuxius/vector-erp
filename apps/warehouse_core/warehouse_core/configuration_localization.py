"""Localize untouched upstream prose on Ukrainian sites without renaming records."""
from pathlib import Path
import re

import frappe


SUPPORT_DEFAULTS = {
    "greeting_title": ("We're here to help", "Ми готові допомогти"),
    "greeting_subtitle": ("Browse help topics", "Перегляньте теми довідки"),
}
DISPATCH_LABELS = {
    "Dispatch Notification": "Повідомлення про відправлення",
    "Details:": "Подробиці:",
    "Customer's Name": "Назва клієнта",
    "Contact's Name": "Ім’я контактної особи",
    "Address Name": "Назва адреси",
    "Address Details": "Адреса доставки",
    "Order Number": "Номер замовлення",
    "Order Total": "Сума замовлення",
    "Departure Time": "Час виїзду",
    "Estimated Arrival": "Очікуваний час прибуття",
    "Driver's Name": "Ім’я водія",
    "Driver's Number": "Телефон водія",
    "Vehicle Number": "Номер транспортного засобу",
}
DISPATCH_SUBJECT = ("Your order is out for delivery!", "Ваше замовлення передано на доставку!")


def dispatch_response():
    original = Path(frappe.get_app_path(
        "erpnext", "stock", "doctype", "delivery_trip", "dispatch_notification_template.html"
    )).read_text(encoding="utf-8")
    translated = original
    for source, target in DISPATCH_LABELS.items():
        assert f">{source}<" in translated, f"Upstream dispatch template changed: {source}"
        translated = translated.replace(f">{source}<", f">{target}<")
    return original, translated


def normalized_dispatch_html(value):
    # The editor serializes this default style without spaces or a final semicolon.
    # Accept only that known cosmetic change, never arbitrary custom content.
    return re.sub(r'style="max-width:\s*500px;?"', 'style="max-width:500px"', value).strip()


def apply():
    if frappe.db.get_single_value("System Settings", "language") != "uk":
        return
    for field, (source, target) in SUPPORT_DEFAULTS.items():
        if frappe.db.get_single_value("Support Settings", field) == source:
            frappe.db.set_single_value("Support Settings", field, target)
    if not frappe.db.exists("Email Template", "Dispatch Notification"):
        return
    doc = frappe.get_doc("Email Template", "Dispatch Notification")
    changes = {}
    if doc.subject == DISPATCH_SUBJECT[0]:
        changes["subject"] = DISPATCH_SUBJECT[1]
    source, target = dispatch_response()
    if normalized_dispatch_html(doc.response or "") == normalized_dispatch_html(source):
        changes["response"] = target
    if changes:
        frappe.db.set_value("Email Template", doc.name, changes)
        frappe.clear_document_cache("Email Template", doc.name)


def verify():
    """Exercise default migration, repeatability, and custom-content preservation."""
    import re

    source, target = dispatch_response()
    assert re.findall(r"{{.*?}}", source) == re.findall(r"{{.*?}}", target)
    assert re.findall(r"<[^>]+>", source) == re.findall(r"<[^>]+>", target)
    if not frappe.db.exists("Email Template", "Dispatch Notification"):
        return {"dispatch_template": "not installed", "placeholders": "preserved"}
    frappe.db.savepoint("configuration_localization")
    try:
        frappe.db.set_single_value("System Settings", "language", "uk")
        for field, (original, _) in SUPPORT_DEFAULTS.items():
            frappe.db.set_single_value("Support Settings", field, original)
        frappe.db.set_value("Email Template", "Dispatch Notification", {
            "subject": DISPATCH_SUBJECT[0], "response": source,
        })
        frappe.clear_document_cache("Email Template", "Dispatch Notification")
        apply()
        apply()
        doc = frappe.get_doc("Email Template", "Dispatch Notification")
        assert doc.subject == DISPATCH_SUBJECT[1] and doc.response == target
        frappe.db.set_value("Email Template", doc.name, {
            "response": source.replace('style="max-width: 500px;"', 'style="max-width:500px"'),
        })
        frappe.clear_document_cache("Email Template", doc.name)
        apply()
        doc.reload()
        assert doc.response == target, "Editor-normalized default must also be translated"
        for field, (_, translated) in SUPPORT_DEFAULTS.items():
            assert frappe.db.get_single_value("Support Settings", field) == translated
        custom = "Custom wording / Власний текст {{ customer }}"
        for field in SUPPORT_DEFAULTS:
            frappe.db.set_single_value("Support Settings", field, custom)
        frappe.db.set_value("Email Template", doc.name, {"subject": custom, "response": custom})
        frappe.clear_document_cache("Email Template", doc.name)
        apply()
        doc.reload()
        assert doc.subject == custom and doc.response == custom
        for field in SUPPORT_DEFAULTS:
            assert frappe.db.get_single_value("Support Settings", field) == custom
    finally:
        frappe.db.rollback(save_point="configuration_localization")
        frappe.clear_document_cache("Email Template", "Dispatch Notification")
    return {"default_prose": "uk", "custom_content": "preserved", "placeholders": "preserved"}
