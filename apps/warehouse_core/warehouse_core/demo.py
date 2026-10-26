"""Explicitly opted-in local demo only. Never called by install/migrate hooks."""
import frappe
from frappe.utils import nowdate, getdate

from warehouse_core.company_identity import COMPANY, MAIN, SECONDARY, PRODUCTION
CUSTOMER = "Клієнт"
SUPPLIER = "Постачальник"
ITEM = "ITEM-001"


def rename_company():
    """Compatibility entry point for the checked company/warehouse migration."""
    from warehouse_core.company_identity import apply
    return apply(dry_run=False)


def rename_warehouses():
    """Compatibility entry point; retain the MTech brand abbreviation."""
    from warehouse_core.company_identity import apply
    return apply(dry_run=False)


def ensure(doctype, name, **fields):
    if frappe.db.exists(doctype, name):
        return frappe.get_doc(doctype, name)
    return frappe.get_doc({"doctype": doctype, **fields}).insert(set_name=name)


def seed():
    if not frappe.conf.get("warehouse_demo"):
        frappe.throw("Demo data requires the explicit site config warehouse_demo=1")
    if frappe.db.exists("Company", COMPANY):
        from warehouse_core.record_names import apply as clean_record_names
        clean_record_names(dry_run=False)
    other_companies = frappe.get_all("Company", filters={"name": ["!=", COMPANY]}, pluck="name")
    if other_companies:
        frappe.throw("Refusing demo setup: this site contains another company")
    if not frappe.is_setup_complete():
        from frappe.desk.page.setup_wizard.setup_wizard import setup_complete
        year = getdate(nowdate()).year
        setup_complete(frappe._dict({
            "language": "English", "lang": "en", "country": "Ukraine", "currency": "UAH",
            "timezone": "Europe/Kiev", "company_name": COMPANY, "company_abbr": "MTech",
            "chart_of_accounts": "Standard", "domain": "Distribution",
            "fy_start_date": f"{year}-01-01", "fy_end_date": f"{year}-12-31",
            "bank_account": "Bank", "setup_demo": 0, "enable_telemetry": 0,
        }))
        # Setup schedules a timezone update for standard users; start fresh afterward.
        frappe.db.commit()
    if not frappe.db.exists("Company", COMPANY):
        frappe.throw("Demo company is missing; refusing to modify an already configured site")
    parent = frappe.db.get_value("Warehouse", {"company": COMPANY, "is_group": 1, "parent_warehouse": ["is", "not set"]})
    for name, title in [(MAIN, "Основний склад"), (SECONDARY, "Додатковий склад"), (PRODUCTION, "Виробничий склад")]:
        ensure("Warehouse", name, warehouse_name=title, company=COMPANY, parent_warehouse=parent)
    ensure("Customer", CUSTOMER, customer_name=CUSTOMER, customer_type="Company",
           customer_group="Commercial", territory="Україна" if frappe.db.exists("Territory", "Україна") else "Ukraine")
    ensure("Supplier", SUPPLIER, supplier_name=SUPPLIER, supplier_type="Company", supplier_group="Local")
    ensure("Item", "CNC-001", item_code="CNC-001", item_name="Верстат із ЧПК",
           stock_uom="Nos", item_group="Products", is_stock_item=1, custom_is_machine=1, serial_no_series="CNC-.####",
           description="Навчальна модель. Комплектацію та специфікацію клієнта ще не надано.",
           item_defaults=[{"company": COMPANY, "default_warehouse": PRODUCTION}])
    for code, title, stock, buying, selling in [
        (ITEM, "Тестовий товар", 1, 100, 150),
        ("ITEM-002", "Другий товар", 1, 50, 80),
        ("SERVICE-001", "Послуга", 0, 0, 200),
    ]:
        ensure("Item", code, item_code=code, item_name=title, stock_uom="Nos",
               item_group="Products" if stock else "Services", is_stock_item=stock,
               description="Навчальний запис. Не є даними клієнта.",
               item_defaults=[{"company": COMPANY, "default_warehouse": MAIN}])
        for price_list, rate in [("Standard Buying", buying), ("Standard Selling", selling)]:
            if rate and not frappe.db.exists("Item Price", {"item_code": code, "price_list": price_list}):
                frappe.get_doc({"doctype": "Item Price", "item_code": code,
                                "price_list": price_list, "currency": "UAH", "price_list_rate": rate}).insert()
    if not frappe.db.exists("Stock Entry", "OPENING-001"):
        opening = frappe.get_doc({"doctype": "Stock Entry", "stock_entry_type": "Material Receipt",
            "company": COMPANY, "remarks": "навчальні початкові залишки",
            "items": [{"item_code": ITEM, "qty": 100, "t_warehouse": MAIN, "basic_rate": 100},
                      {"item_code": "ITEM-002", "qty": 50, "t_warehouse": MAIN, "basic_rate": 50}]})
        opening.insert(set_name="OPENING-001")
        opening.submit()
    ensure("Sales Order", "SO-001", company=COMPANY, customer=CUSTOMER,
           delivery_date=nowdate(), selling_price_list="Standard Selling",
           items=[{"item_code": ITEM, "qty": 2, "rate": 150, "warehouse": MAIN}])
    ensure("Purchase Order", "PO-001", company=COMPANY, supplier=SUPPLIER,
           schedule_date=nowdate(), buying_price_list="Standard Buying",
           items=[{"item_code": ITEM, "qty": 10, "rate": 100, "warehouse": MAIN}])
    seed_client_examples()
    frappe.db.set_single_value("System Settings", "language", "uk")
    frappe.db.set_single_value("System Settings", "enable_telemetry", 0)
    frappe.db.set_value("User", "Administrator", "language", "uk")
    frappe.clear_cache()
    frappe.db.commit()
    return {"company": COMPANY, "workspace": "/desk/warehouse-core", "sample_sales_order": "SO-001",
            "sample_purchase_order": "PO-001"}


def seed_client_examples():
    """Illustrative MTech workflow; no imported records or actual specifications."""
    from warehouse_core.staff import seed_examples
    seed_examples()
    frappe.db.set_value("Customer", CUSTOMER, "language", "uk")
    frappe.db.set_value("Supplier", SUPPLIER, "language", "uk")
    company = frappe.get_doc("Company", COMPANY)
    if not company.custom_payment_details:
        company.custom_payment_details = "НАВЧАЛЬНИЙ ПРИКЛАД для MTech / ТОВ «ЕМ ТЕК».\nРеальні ЄДРПОУ, IBAN і банк буде заповнено після імпорту реквізитів. Не використовувати для оплати."
        company.save()
    if not frappe.db.exists("BOM", "CNC-BOM-001"):
        bom = frappe.get_doc({"doctype": "BOM", "item": "CNC-001", "quantity": 1,
            "company": COMPANY, "currency": "UAH",
            "items": [{"item_code": ITEM, "qty": 2, "rate": 100},
                      {"item_code": "ITEM-002", "qty": 1, "rate": 50}]})
        bom.insert(set_name="CNC-BOM-001")
        bom.submit()
    if not frappe.db.exists("Work Order", "CNC-WO-001"):
        work = frappe.get_doc({"doctype": "Work Order", "company": COMPANY,
            "production_item": "CNC-001", "bom_no": "CNC-BOM-001", "qty": 1,
            "source_warehouse": MAIN, "wip_warehouse": PRODUCTION, "fg_warehouse": SECONDARY})
        work.get_items_and_operations_from_bom()
        work.insert(set_name="CNC-WO-001")
    if not frappe.db.exists("Sales Order", "DISPATCH-SO-001"):
        sale = frappe.get_doc({"doctype": "Sales Order", "customer": CUSTOMER, "company": COMPANY,
            "delivery_date": nowdate(), "selling_price_list": "Standard Selling",
            "items": [{"item_code": ITEM, "qty": 2, "rate": 150, "warehouse": MAIN}]})
        sale.insert(set_name="DISPATCH-SO-001")
        sale.submit()
    if not frappe.db.exists("Delivery Note", "DISPATCH-DN-001"):
        from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
        delivery = make_delivery_note("DISPATCH-SO-001")
        delivery.items[0].qty = 1
        delivery.custom_nova_poshta_ttn = "00000000000000"
        delivery.insert(set_name="DISPATCH-DN-001")
        delivery.submit()
    if not frappe.db.exists("Stock Entry", "CNC-RECEIPT-001"):
        receipt = frappe.get_doc({"doctype": "Stock Entry", "stock_entry_type": "Material Receipt",
            "company": COMPANY, "remarks": "навчальне надходження готового верстата; вартість умовна",
            "items": [{"item_code": "CNC-001", "qty": 1, "t_warehouse": SECONDARY,
                       "basic_rate": 250, "use_serial_batch_fields": 1, "serial_no": "CNC-SN-001"}]})
        receipt.insert(set_name="CNC-RECEIPT-001")
        receipt.submit()
        serial = frappe.get_doc("Serial No", "CNC-SN-001")
        serial.custom_machine_configuration = "ПРИКЛАД: блок керування, шпиндель, комплект кабелів. Реальна комплектація — після імпорту."
        serial.save()
    if not frappe.db.exists("Maintenance Visit", "SERVICE-001"):
        person_name = "Сервісний спеціаліст" if frappe.db.exists("Sales Person", "Сервісний спеціаліст") else "Service Person"
        person = ensure("Sales Person", person_name, sales_person_name=person_name,
            parent_sales_person=frappe.db.get_value("Sales Person", {"is_group": 1, "parent_sales_person": ["is", "not set"]}), is_group=0)
        visit = frappe.get_doc({"doctype": "Maintenance Visit", "customer": CUSTOMER, "company": COMPANY,
            "mntc_date": nowdate(), "completion_status": "Fully Completed", "maintenance_type": "Unscheduled",
            "purposes": [{"item_code": "CNC-001", "serial_no": "CNC-SN-001",
                          "service_person": person.name, "work_done": "ПРИКЛАД: перевірка підключення та передача комплекту кабелів."}]})
        visit.insert(set_name="SERVICE-001")
        visit.submit()
    for doctype, name in (("Sales Order", "SO-001"), ("Purchase Order", "PO-001"),
                          ("Sales Order", "DISPATCH-SO-001"), ("Delivery Note", "DISPATCH-DN-001")):
        if frappe.get_meta(doctype).has_field("language"):
            frappe.db.set_value(doctype, name, "language", "uk")
