"""Repeatable, company-independent configuration. Does not create business data."""
import json

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

SOURCE_DOCTYPES = (
    "Item", "Customer", "Supplier", "Warehouse", "Sales Order", "Purchase Order",
    "Delivery Note", "Purchase Receipt", "Sales Invoice", "Purchase Invoice",
    "Item Price", "Contact", "Address", "Serial No", "Stock Entry",
    "Stock Reconciliation", "Payment Entry", "Journal Entry", "BOM",
    "Work Order", "Maintenance Visit",
)
ROLE_PROFILES = {
    "Warehouse Sales": ["Sales User"],
    "Warehouse Purchasing": ["Purchase User"],
    "Warehouse Staff": ["Stock User"],
    "Warehouse Accounting": ["Accounts User"],
    "Warehouse Operations Manager": ["Sales Manager", "Purchase Manager", "Stock Manager"],
    "Warehouse Manufacturing": ["Manufacturing User", "Stock User"],
    "Warehouse Service": ["Maintenance User"],
}
CARDS = [
    ("Довідники", [
        ("Товари", "Item"), ("Групи товарів", "Item Group"),
        ("Одиниці виміру", "UOM"), ("Клієнти", "Customer"),
        ("Постачальники", "Supplier"), ("Контакти", "Contact"),
        ("Адреси", "Address"), ("Склади", "Warehouse"),
        ("Прайс-листи", "Price List"), ("Ціни товарів", "Item Price"),
    ]),
    ("Продажі", [
        ("Комерційні пропозиції", "Quotation"), ("Замовлення клієнтів", "Sales Order"),
        ("Відвантаження та повернення", "Delivery Note"),
        ("Рахунки продажу", "Sales Invoice"), ("Оплати", "Payment Entry"),
    ]),
    ("Закупівлі", [
        ("Замовлення постачальникам", "Purchase Order"),
        ("Надходження та повернення", "Purchase Receipt"),
        ("Рахунки постачальників", "Purchase Invoice"),
    ]),
    ("Складські операції", [
        ("Переміщення, оприбуткування, списання", "Stock Entry"),
        ("Інвентаризація", "Stock Reconciliation"), ("Потреба в товарах", "Material Request"),
    ]),
    ("Звіти", [
        ("Залишки товарів", "Stock Balance", "Report"),
        ("Рух товарів", "Stock Ledger", "Report"),
        ("Заборгованість клієнтів", "Accounts Receivable", "Report"),
        ("Заборгованість постачальникам", "Accounts Payable", "Report"),
    ]),
    ("Адміністрування", [
        ("Імпорт даних", "Data Import"), ("Користувачі", "User"),
        ("Профілі ролей", "Role Profile"), ("Обмеження доступу", "User Permission"),
    ]),
]


def apply():
    from warehouse_core.report_fixes import apply as apply_report_fixes
    from warehouse_core.knowledge_setup import apply as apply_knowledge

    migrate_legacy_names()
    apply_knowledge()
    create_custom_fields({doctype: [{
        "fieldname": "custom_moysklad_id",
        "label": "MoySklad ID",
        "fieldtype": "Data",
        "description": "Original immutable MoySklad entity ID. Leave empty for new ERPNext records.",
        "unique": 1,
        "no_copy": 1,
        "print_hide": 1,
        "insert_after": "amended_from" if frappe.get_meta(doctype).has_field("amended_from") else None,
    }] for doctype in SOURCE_DOCTYPES})
    for name, roles in ROLE_PROFILES.items():
        if not frappe.db.exists("Role Profile", name):
            profile = frappe.get_doc({"doctype": "Role Profile", "name": name, "role_profile": name,
                                      "roles": [{"role": role} for role in roles]})
            # A rolled-back install can leave a file lock for a nonexistent profile.
            # New profiles have no users to update; run that native action inline.
            profile.unlock()
            previous_install = frappe.flags.in_install
            try:
                frappe.flags.in_install = True
                profile.insert()
            finally:
                frappe.flags.in_install = previous_install
    # Preserve user edits to the workspace and existing role profiles on upgrades.
    if not frappe.db.exists("Workspace", "Warehouse Core"):
        content = [{"id": "warehouse-heading", "type": "header", "data": {
            "text": "<span class=\"h4\">Вектор ERP</span>", "col": 12}}]
        shortcuts = []
        for index, (label, target) in enumerate([
            ("Товари", "Item"), ("Клієнти", "Customer"),
            ("Замовлення клієнтів", "Sales Order"),
            ("Замовлення постачальникам", "Purchase Order"),
        ]):
            shortcuts.append({"label": label, "type": "DocType", "link_to": target, "doc_view": "List"})
            content.append({"id": f"warehouse-shortcut-{index}", "type": "shortcut",
                            "data": {"shortcut_name": label, "col": 3}})
        links = []
        for index, (label, entries) in enumerate(CARDS):
            content.append({"id": f"warehouse-card-{index}", "type": "card",
                            "data": {"card_name": label, "col": 4}})
            links.append({"type": "Card Break", "label": label, "link_count": len(entries)})
            for entry in entries:
                kind = entry[2] if len(entry) > 2 else "DocType"
                links.append({"type": "Link", "label": entry[0], "link_to": entry[1],
                              "link_type": kind, "is_query_report": int(kind == "Report")})
        frappe.get_doc({"doctype": "Workspace", "label": "Warehouse Core", "title": "Vector ERP",
                        "module": "Warehouse Core", "public": 1, "icon": "package",
                        "sequence_id": 1, "content": json.dumps(content, ensure_ascii=False),
                        "links": links, "shortcuts": shortcuts}).insert()
    apply_branding()
    apply_report_fixes()
    from warehouse_core.client_setup import apply as apply_client_setup
    apply_client_setup()
    from warehouse_core.navigation import apply as apply_navigation
    apply_navigation()
    from warehouse_core.configuration_localization import apply as localize_configuration
    localize_configuration()
    from warehouse_core.tutorial_media import apply as remove_tutorial_videos
    remove_tutorial_videos()
    frappe.clear_cache()


def apply_branding():
    """Apply product branding while preserving workspace layout and business records."""
    from warehouse_core.branding import MTECH_LOGO, VECTOR_LOGO

    workspace = frappe.get_doc("Workspace", "Warehouse Core")
    workspace.title = "Vector ERP"
    content = json.loads(workspace.content or "[]")
    for block in content:
        if block.get("id") == "warehouse-heading" and block.get("type") == "header":
            data = block.get("data", {})
            if data.get("text") == '<span class="h4">Склад і торгівля</span>':
                data["text"] = '<span class="h4">Вектор ERP</span>'
    workspace.content = json.dumps(content, ensure_ascii=False)
    workspace.save(ignore_permissions=True)
    settings = frappe.get_single("Website Settings")
    settings.app_name = "Vector ERP"
    settings.title_prefix = "Vector ERP"
    settings.brand_html = "Vector ERP"
    settings.app_logo = VECTOR_LOGO
    settings.splash_image = VECTOR_LOGO
    settings.favicon = MTECH_LOGO
    settings.save(ignore_permissions=True)


def migrate_legacy_names():
    """Rename existing configuration without recreating business records or permissions."""
    legacy_profiles = {
        "Sklad Sales": "Warehouse Sales",
        "Sklad Purchasing": "Warehouse Purchasing",
        "Sklad Warehouse": "Warehouse Staff",
        "Sklad Accounting": "Warehouse Accounting",
        "Sklad Operations Manager": "Warehouse Operations Manager",
    }
    for old, new in legacy_profiles.items():
        if frappe.db.exists("Role Profile", old):
            if frappe.db.exists("Role Profile", new):
                frappe.throw(f"Both {old} and {new} exist; resolve the naming conflict before migrating")
            frappe.rename_doc("Role Profile", old, new, force=True)
    if frappe.db.exists("Workspace", "Sklad Core"):
        if frappe.db.exists("Workspace", "Warehouse Core"):
            frappe.throw("Both old and new workspace names exist; refusing to overwrite either")
        frappe.rename_doc("Workspace", "Sklad Core", "Warehouse Core", force=True)
    if frappe.db.exists("Module Def", "Sklad Core"):
        # This is our own retired app module; Frappe only permits custom-module renames.
        frappe.db.set_value("Module Def", "Sklad Core", "custom", 1)
        frappe.rename_doc("Module Def", "Sklad Core", "Warehouse Core", force=True, merge=True)
        frappe.db.set_value("Module Def", "Warehouse Core", "app_name", "warehouse_core")
    if "sklad_demo" in frappe.conf:
        from frappe.installer import update_site_config
        if frappe.conf.get("warehouse_demo") is None and frappe.conf.get("sklad_demo") is not None:
            update_site_config("warehouse_demo", frappe.conf.sklad_demo)
        # Frappe's configuration helper uses the string "None" to remove a key.
        update_site_config("sklad_demo", "None")
        frappe.conf.pop("sklad_demo", None)
    if "sklad_core" in frappe.get_installed_apps():
        # Only remove the old registration, never uninstall/delete its data.
        from frappe.installer import remove_from_installed_apps
        remove_from_installed_apps("sklad_core")
