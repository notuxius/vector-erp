"""Permission-aware operational homepage. All counts use the user's list permissions."""
import frappe
from frappe.utils import nowdate, now_datetime


def boot_session(bootinfo):
    if frappe.is_setup_complete():
        bootinfo.home_page = "operations-overview"
        for app in bootinfo.get("app_data", []):
            if app.get("app_name") == "erpnext":
                app["app_route"] = "/desk/operations-overview"


def website_home(user):
    if user != "Guest" and frappe.get_cached_value("User", user, "user_type") == "System User":
        return "desk/operations-overview"


@frappe.whitelist()
def get_overview(company=None, months=6):
    if frappe.session.user == "Guest" or frappe.get_cached_value(
        "User", frappe.session.user, "user_type"
    ) != "System User":
        frappe.throw("Немає доступу.", frappe.PermissionError)
    companies = (frappe.get_list("Company", pluck="name", order_by="name", limit_page_length=0)
                 if frappe.has_permission("Company", "read") else [])
    if company and company not in companies:
        frappe.throw("Немає доступу до компанії.", frappe.PermissionError)
    today = nowdate()
    base = {"company": company or ["in", companies]}
    open_sales = {**base, "docstatus": 1, "per_delivered": ["<", 100],
                  "status": ["not in", ["Closed", "Completed", "Cancelled", "On Hold"]]}
    overdue = {**open_sales, "delivery_date": ["<", today]}
    unpaid = {**base, "docstatus": 1, "outstanding_amount": [">", 0], "is_return": 0}
    purchases = {**base, "docstatus": 1, "per_received": ["<", 100],
                 "status": ["not in", ["Closed", "Completed", "Cancelled", "On Hold"]]}
    late_purchases = {**purchases, "schedule_date": ["<", today]}
    stock_drafts = {**base, "docstatus": 0}
    delivery_drafts = {**stock_drafts, "is_return": 0}
    sections = [
        dict(key="sales", label="Продажі", workspace="selling", color="blue"),
        dict(key="accounting", label="Бухгалтерія", workspace="accounting", color="purple"),
        dict(key="purchasing", label="Закупівлі", workspace="buying", color="orange"),
        dict(key="stock", label="Склад", workspace="stock", color="green"),
    ]
    definitions = [
        ("sales", "orders", "Замовлення до відвантаження", "Sales Order", open_sales,
         "Підтверджені замовлення з невідвантаженим залишком", "blue"),
        ("sales", "overdue", "Прострочені відвантаження", "Sales Order", overdue,
         "Планова дата замовлення вже минула", "orange"),
        ("accounting", "invoices", "Рахунки до отримання оплати", "Sales Invoice", unpaid,
         "Кількість проведених рахунків клієнтам із залишком до сплати", "purple"),
        ("accounting", "payables", "Рахунки до оплати постачальникам", "Purchase Invoice", unpaid,
         "Кількість проведених рахунків постачальників із залишком до сплати", "purple"),
        ("purchasing", "purchases", "Очікувані надходження", "Purchase Order", purchases,
         "Підтверджені замовлення з неотриманим залишком", "orange"),
        ("purchasing", "late_purchases", "Прострочені надходження", "Purchase Order", late_purchases,
         "Планова дата отримання замовлення вже минула", "orange"),
        ("stock", "deliveries", "Відвантаження до проведення", "Delivery Note", delivery_drafts,
         "Чернетки видаткових накладних, без повернень", "green"),
        ("stock", "stock_drafts", "Складські операції до проведення", "Stock Entry", stock_drafts,
         "Чернетки переміщень, надходжень і списань", "green"),
    ]
    cards = []
    for section, key, label, doctype, filters, description, color in definitions:
        if not frappe.has_permission(doctype, "read"):
            continue
        count = frappe.get_list(doctype, filters=filters, fields=[{"COUNT": "name", "as": "total"}])[0].total
        cards.append(dict(section=section, key=key, label=label, doctype=doctype, filters=filters,
                          value=count, description=description, color=color))
    tables = []
    for label, doctype, filters, fields, order_by in [
        ("Відвантаження, що потребують уваги", "Sales Order", overdue,
         ["name", "customer_name", "delivery_date"], "delivery_date asc, name asc"),
        ("Надходження, що потребують уваги", "Purchase Order", late_purchases,
         ["name", "supplier_name", "schedule_date"], "schedule_date asc, name asc"),
    ]:
        if frappe.has_permission(doctype, "read"):
            rows = frappe.get_list(doctype, filters=filters, fields=fields,
                                   order_by=order_by, limit_page_length=6)
            tables.append(dict(label=label, doctype=doctype, filters=filters, rows=rows))
    links = []
    for label, kind, target, description in [
        ("Замовлення клієнтів", "DocType", "Sales Order", "Продажі та строки відвантаження"),
        ("Замовлення постачальникам", "DocType", "Purchase Order", "Закупівлі та очікувані надходження"),
        ("Товари", "DocType", "Item", "Каталог товарів і послуг"),
        ("Готовність до відправлення", "Report", "Order Readiness", "Наявність, оплати та залишок до відправлення"),
        ("Щоденний список для комірника", "Report", "Daily Dispatch List", "План відвантажень на день"),
        ("Рахунки клієнтам", "DocType", "Sales Invoice", "Рахунки та залишки до отримання оплати"),
        ("Рахунки постачальників", "DocType", "Purchase Invoice", "Рахунки та залишки до сплати"),
        ("Складські операції", "DocType", "Stock Entry", "Переміщення, надходження та списання"),
    ]:
        allowed = (frappe.has_permission(target, "read") if kind == "DocType"
                   else frappe.get_doc("Report", target).is_permitted())
        if allowed:
            links.append(dict(label=label, kind=kind, target=target, description=description))
    from warehouse_core.dashboard_charts import get_charts
    charts = get_charts([company] if company else companies, months)
    chart_sections = {"sales": "sales", "payments": "accounting", "purchases": "purchasing", "stock": "stock"}
    return dict(companies=companies, company=company, date=today, chart_data=charts,
                updated_at=str(now_datetime()), cards=cards, tables=tables, links=links,
                sections=[section for section in sections if any(card["section"] == section["key"] for card in cards)
                          or any(chart_sections[chart["key"]] == section["key"] for chart in charts["charts"])])
