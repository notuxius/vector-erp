"""Use the site's language for the public sign-in and password recovery pages."""
import frappe
from frappe.translate import get_lang_code


def before_request():
    # The native guest resolver prefers browser headers and old language cookies
    # over System Settings. These entry pages should match the site's interface.
    # Keep explicit _lang requests and signed-in users' preferences authoritative.
    request = frappe.local.request
    if (request.method not in {"GET", "HEAD"}
            or request.path.rstrip("/") not in {"/login", "/update-password"}
            or frappe.session.user != "Guest"):
        return
    explicit_language = frappe.form_dict.get("_lang")
    if explicit_language and get_lang_code(explicit_language):
        return
    frappe.local.lang = frappe.get_system_settings("language") or "en"
