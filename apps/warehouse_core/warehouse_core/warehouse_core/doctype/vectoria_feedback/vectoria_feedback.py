"""Private Desk reports; pending and record_analysis are bench-only Codex operations."""
import hashlib
import json
import re
import sys
import time
import uuid

import frappe
from frappe.model.document import Document
from frappe.rate_limiter import rate_limit

from warehouse_core.ai_chat import require_desk_user


class VectoriaFeedback(Document):
    pass


def save_report(name, user, category, message, context):
    if frappe.db.exists("Vectoria Feedback", name):
        return name
    try:
        frappe.get_doc({
            "doctype": "Vectoria Feedback", "category": category, "reported_by": user,
            "message": message.strip(), "context": context.strip(),
        }).insert(ignore_permissions=True, set_name=name)
    except frappe.DuplicateEntryError:
        # Concurrent retries share the first report and its notification.
        return name
    frappe.get_doc({
        "doctype": "Notification Log", "type": "Alert", "for_user": "Administrator",
        "from_user": user, "subject": "Векторія: " + category,
        "document_type": "Vectoria Feedback", "document_name": name,
    }).insert(ignore_permissions=True)
    return name


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=30, seconds=600)
def submit(category: str, message: str, context: str, request_id: str):
    user = require_desk_user()
    if category not in ("Помилка", "Пропозиція", "Повідомлення") or not message.strip() or len(message) > 4000 or len(context) > 8000:
        frappe.throw("Перевірте тип звернення, повідомлення (до 4000 символів) і контекст (до 8000).")
    try:
        token = uuid.UUID(request_id).hex
    except ValueError:
        frappe.throw("Не вдалося розпізнати звернення. Відкрийте форму ще раз.")
    name = "VF-" + hashlib.sha256(f"{user}:{token}".encode()).hexdigest()[:20]
    return save_report(name, user, category, message, context)


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=90, seconds=600)
def report_error(operation: str, mode: str, code: str, diagnostics: str = ""):
    user = require_desk_user()
    operations = {"status", "options", "route", "send", "combined", "reset", "usage", "render", "feedback"}
    reasons = {
        "network": "З’єднання з ERP перервано.", "response": "ERP повернула некоректну відповідь.",
        "widget": "Сталася помилка інтерфейсу чату.", "lookup": "Не вдалося виконати пошук в ERP.",
        "gateway": "Шлюз ШІ не відповів коректно.", "provider": "Збій під час звернення шлюзу до постачальника ШІ.",
        "empty": "ШІ повернув порожню відповідь.",
        "cloud_usage": "Не вдалося отримати звіт про використання Cloudflare.",
        "stock_plan": "ШІ повернув некоректні фільтри пошуку залишків.",
    }
    if operation not in operations or mode not in {"assistant", "draft", "availability", "knowledge"}:
        frappe.throw("Некоректна операція Векторії.")
    if code in reasons:
        reason = reasons[code]
    elif code.isascii() and code.isdigit() and len(code) == 3 and (500 <= int(code) <= 599 or code in {"404", "408"}):
        reason = f"ERP повернула HTTP {code}."
    else:
        frappe.throw("Некоректний код помилки Векторії.")
    signature = f"auto:{int(time.time()) // 3600}:{operation}:{mode}:{code}"
    name = "VF-" + hashlib.sha256(signature.encode()).hexdigest()[:20]
    context = [f"Вебчат Векторії · операція: {operation} · режим: {mode} · код: {code}"]
    if diagnostics and len(diagnostics) <= 512:
        try:
            snapshot = json.loads(diagnostics)
        except ValueError:
            snapshot = None
        if isinstance(snapshot, dict):
            for field, label, pattern in [
                ("observed_at", "Час збою за годинником браузера (UTC)", r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z"),
                ("build", "Версія вебчату", r"[a-f0-9]{12}"),
                ("viewport", "Розмір вікна", r"[0-9]{1,5}x[0-9]{1,5}"),
                ("page", "Тип сторінки", r"Form|List|Tree|Workspaces|query-report|Desk"),
                ("connection", "З’єднання браузера", r"online|offline"),
            ]:
                value = snapshot.get(field)
                if isinstance(value, str) and re.fullmatch(pattern, value):
                    context.append(f"{label}: {value}")
    return save_report(name, user, "Автоматична помилка", reason, "\n".join(context))


def pending():
    reports = frappe.get_all("Vectoria Feedback", filters={"analysis": ["is", "not set"]},
                             fields=["name", "category", "message", "context", "creation"], order_by="creation asc", limit=5)
    return {"reports": reports, "site_timezone": frappe.utils.get_system_timezone()} if reports else {"reports": []}


def record_analysis(name: str, analysis: str):
    if not isinstance(analysis, str) or not analysis.strip() or len(analysis) > 8000:
        frappe.throw("Аналіз має містити від 1 до 8000 символів.")
    report = frappe.get_doc("Vectoria Feedback", name, for_update=True)
    if not report.analysis:
        report.db_set("analysis", analysis.strip())


def save_finding():
    record_analysis(**json.load(sys.stdin))
