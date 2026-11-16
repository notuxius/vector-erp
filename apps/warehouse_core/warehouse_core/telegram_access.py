"""Restrict the Telegram service identity to read-only assistant endpoints."""
import frappe

USER = 'telegram.bot@vector-erp.invalid'
OPTIONS = '/api/method/warehouse_core.availability.options'
SEND = '/api/method/warehouse_core.ai_chat.send'


def restrict_api():
    # auth_hooks run after API-token authentication; before_request is too early.
    if frappe.session.user != USER:
        return
    request = frappe.local.request
    allowed = (request.path == OPTIONS and request.method == 'GET') or (
        request.path in ('/api/method/warehouse_core.ai_chat.route', '/api/method/warehouse_core.ai_chat.combined',
                         '/api/method/warehouse_core.ai_chat.question_usage') and request.method == 'POST') or (
        request.path == SEND and request.method == 'POST'
        and frappe.form_dict.get('mode') in ('availability', 'knowledge', 'draft'))
    # Frappe's legacy cmd dispatch must not override the checked request path.
    if not allowed or 'cmd' in frappe.form_dict:
        frappe.throw('Обліковий запис бота має лише обмежений доступ для читання.', frappe.PermissionError)
