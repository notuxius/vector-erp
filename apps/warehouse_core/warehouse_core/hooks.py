app_name = "warehouse_core"
app_title = "Vector ERP"
app_publisher = "Vector ERP"
app_description = "Vector ERP stock, purchasing and sales"
app_email = "admin@example.test"
app_license = "MIT"
required_apps = ["erpnext"]
from warehouse_core.branding import VECTOR_LOGO as _VECTOR_LOGO
from warehouse_core.branding import MTECH_LOGO as _MTECH_LOGO

app_logo_url = _VECTOR_LOGO
# Render our logo in the initial HTML, before Desk JavaScript is loaded.
website_context = {"favicon": _MTECH_LOGO, "splash_image": _VECTOR_LOGO}
update_website_context = ["warehouse_core.splash.update_website_context"]
before_request = ["warehouse_core.website_localization.before_request"]
# Keep the internal package/module while consolidating its navigation in Vector ERP.
code_only_modules = {"Warehouse Core": ["Stock"]}
from hashlib import sha256 as _sha256
from pathlib import Path as _Path

# Plain app assets do not get Frappe's bundle hashes. Change the URL when the
# rendering adapter changes so existing browser sessions receive the new code.
_ui_version = _sha256((_Path(__file__).parent / "public/js/uk_ui.js").read_bytes()).hexdigest()[:12]
app_include_js = [f"/assets/warehouse_core/js/uk_ui.js?v={_ui_version}"]
_shipping_version = _sha256((_Path(__file__).parent / "public/js/nova_poshta.js").read_bytes()).hexdigest()[:12]
app_include_js.append(f"/assets/warehouse_core/js/nova_poshta.js?v={_shipping_version}")
# Keep essential navigation separate from onboarding display customization.
for _ui_asset in ("error_feedback", "interface_preferences", "chart_rendering", "styled_tooltips", "onboarding_labels", "navigation_logo", "user_navigation", "dashboard_navigation", "navigation_localization", "navigation_resize", "navigation_mobile", "navigation_position", "dock_labels", "navigation_scroll", "navigation_sections", "page_appearance", "dialog_navigation", "pos_navigation", "list_updates"):
    _asset_version = _sha256((_Path(__file__).parent / f"public/js/{_ui_asset}.js").read_bytes()).hexdigest()[:12]
    app_include_js.append(f"/assets/warehouse_core/js/{_ui_asset}.js?v={_asset_version}")
_tutorial_version = _sha256((_Path(__file__).parent / "public/js/tutorial_media.js").read_bytes()).hexdigest()[:12]
app_include_js.append(f"/assets/warehouse_core/js/tutorial_media.js?v={_tutorial_version}")
_navigation_version = _sha256((_Path(__file__).parent / "public/js/navigation_breadcrumbs.js").read_bytes()).hexdigest()[:12]
app_include_js.append(f"/assets/warehouse_core/js/navigation_breadcrumbs.js?v={_navigation_version}")
_navigation_css_version = _sha256((_Path(__file__).parent / "public/css/navigation_breadcrumbs.css").read_bytes()).hexdigest()[:12]
app_include_css = [f"/assets/warehouse_core/css/navigation_breadcrumbs.css?v={_navigation_css_version}"]
_dock_css_version = _sha256((_Path(__file__).parent / "public/css/dock_labels.css").read_bytes()).hexdigest()[:12]
app_include_css.append(f"/assets/warehouse_core/css/dock_labels.css?v={_dock_css_version}")
_position_css_version = _sha256((_Path(__file__).parent / "public/css/navigation_position.css").read_bytes()).hexdigest()[:12]
app_include_css.append(f"/assets/warehouse_core/css/navigation_position.css?v={_position_css_version}")
_dialog_css_version = _sha256((_Path(__file__).parent / "public/css/dialog_scrollbars.css").read_bytes()).hexdigest()[:12]
app_include_css.append(f"/assets/warehouse_core/css/dialog_scrollbars.css?v={_dialog_css_version}")
_preferences_css_version = _sha256((_Path(__file__).parent / "public/css/interface_preferences.css").read_bytes()).hexdigest()[:12]
app_include_css.append(f"/assets/warehouse_core/css/interface_preferences.css?v={_preferences_css_version}")

_settings_version = _sha256((_Path(__file__).parent / "public/js/settings_ui.js").read_bytes()).hexdigest()[:12]
app_include_js.append(f"/assets/warehouse_core/js/settings_ui.js?v={_settings_version}")

for _kind, _assets in (("js", app_include_js), ("css", app_include_css)):
    _chat_version = _sha256((_Path(__file__).parent / f"public/{_kind}/ai_chat.{_kind}").read_bytes()).hexdigest()[:12]
    _assets.append(f"/assets/warehouse_core/{_kind}/ai_chat.{_kind}?v={_chat_version}")

doctype_js = {
    "Sales Order": "public/js/sales_completion.js",
    "Domain Settings": "public/js/domain_settings.js",
    "Item Variant Settings": "public/js/item_variant_settings.js",
    "POS Settings": "public/js/pos_settings.js",
    "Serial No": "public/js/serial_assemblies.js",
}

after_install = "warehouse_core.install.apply"
after_migrate = "warehouse_core.install.apply"
auth_hooks = ["warehouse_core.telegram_access.restrict_api"]
boot_session = ["warehouse_core.branding.boot_session", "warehouse_core.user_identity.boot_session",
                "warehouse_core.dashboard.boot_session", "warehouse_core.ai_chat.boot_session"]
override_whitelisted_methods = {
    "frappe.desk.query_report.run": "warehouse_core.report_fixes.run",
}
get_website_user_home_page = "warehouse_core.dashboard.website_home"

from warehouse_core.operations import AUDITED_DOCTYPES as _AUDITED_DOCTYPES

doc_events = {doctype: {
    "on_submit": "warehouse_core.operations.record_operation",
    "on_cancel": "warehouse_core.operations.record_operation",
} for doctype in _AUDITED_DOCTYPES}
doc_events["Item"] = {"before_validate": "warehouse_core.operations.validate_machine"}
doc_events["Maintenance Visit"]["validate"] = "warehouse_core.operations.validate_service"
doc_events["Delivery Note"]["validate"] = "warehouse_core.operations.validate_shipping"
doc_events["Delivery Note"]["before_update_after_submit"] = "warehouse_core.operations.validate_shipping"
doc_events["Delivery Note"]["before_cancel"] = "warehouse_core.nova_poshta.protect_delivery"

has_permission = {"Nova Poshta Shipment": "warehouse_core.warehouse_core.doctype.nova_poshta_shipment.nova_poshta_shipment.has_permission"}
permission_query_conditions = {"Nova Poshta Shipment": "warehouse_core.warehouse_core.doctype.nova_poshta_shipment.nova_poshta_shipment.permission_query"}
has_permission["Machine Assembly Installation"] = "warehouse_core.warehouse_core.doctype.machine_assembly_installation.machine_assembly_installation.has_permission"
permission_query_conditions["Machine Assembly Installation"] = "warehouse_core.warehouse_core.doctype.machine_assembly_installation.machine_assembly_installation.permission_query"
has_permission["Knowledge Article"] = "warehouse_core.knowledge.article_permission"
permission_query_conditions["Knowledge Article"] = "warehouse_core.knowledge.article_query"
has_permission["Knowledge Attachment"] = "warehouse_core.knowledge.attachment_permission"
permission_query_conditions["Knowledge Attachment"] = "warehouse_core.knowledge.attachment_query"
permission_query_conditions["Knowledge Access User"] = "warehouse_core.knowledge.access_user_query"
permission_query_conditions["Knowledge Access Role"] = "warehouse_core.knowledge.access_role_query"
doc_events["File"] = {"before_insert": "warehouse_core.knowledge.reject_standard_attachment",
                      "validate": "warehouse_core.knowledge.reject_standard_attachment"}
doc_events["DocShare"] = {"before_insert": "warehouse_core.knowledge.reject_share",
                          "validate": "warehouse_core.knowledge.reject_share"}
