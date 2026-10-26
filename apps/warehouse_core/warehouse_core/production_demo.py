"""Opt-in CNC training scenario. Native ledgers only; reruns never reset practice."""
import json

import frappe
from frappe.utils import add_days, nowdate, now_datetime

from warehouse_core.demo import COMPANY, MAIN, SECONDARY, PRODUCTION, SUPPLIER, CUSTOMER
from warehouse_core.assemblies import get_assemblies
from warehouse_core.warehouse_core.report.expected_production.expected_production import execute
from warehouse_core.warehouse_core.doctype.machine_assembly_installation.machine_assembly_installation import remove_assembly

MARKER = "TRAIN-PRODUCTION-V1"
NOTE = "НАВЧАЛЬНИЙ ПРИКЛАД. Не є замовленням, технологією чи ціною клієнта."
MACHINE = "TRAIN-CNC-1325-001"
FRAME = "TRAIN-FRAME-001"
CONTROL = "TRAIN-CONTROL-001"
OLD_SPINDLE, CURRENT_SPINDLE, SPARE_SPINDLE = (f"TRAIN-SPINDLE-{n:03}" for n in (1, 2, 3))
ITEMS = {
    "TRAIN-STEEL-KIT": ("НАВЧАННЯ: комплект заготовок рами 1325", 18000, 10, None),
    "TRAIN-LINEAR-KIT": ("НАВЧАННЯ: комплект напрямних і передач 1325", 12000, 10, None),
    "TRAIN-SERVO-KIT": ("НАВЧАННЯ: сервопривід із драйвером", 8500, 18, None),
    "TRAIN-CONTROL-KIT": ("НАВЧАННЯ: контролер ЧПК і електрокомпоненти", 22000, 6, None),
    "TRAIN-SPINDLE-KIT": ("НАВЧАННЯ: шпиндельний картридж 2,2 кВт", 14000, 10, None),
    "TRAIN-FRAME-1325": ("НАВЧАННЯ: рама з напрямними 1325", 0, 0, "assembly"),
    "TRAIN-CONTROL": ("НАВЧАННЯ: шафа керування ЧПК, 3 осі", 0, 0, "assembly"),
    "TRAIN-SPINDLE": ("НАВЧАННЯ: шпиндельний вузол 2,2 кВт", 0, 0, "assembly"),
    "TRAIN-CNC-1325": ("НАВЧАННЯ: фрезерний верстат ЧПК 1325", 0, 0, "machine"),
}
BOMS = {
    "TRAIN-BOM-FRAME": ("TRAIN-FRAME-1325", [("TRAIN-STEEL-KIT", 1), ("TRAIN-LINEAR-KIT", 1)]),
    "TRAIN-BOM-CONTROL": ("TRAIN-CONTROL", [("TRAIN-CONTROL-KIT", 1), ("TRAIN-SERVO-KIT", 3)]),
    "TRAIN-BOM-SPINDLE": ("TRAIN-SPINDLE", [("TRAIN-SPINDLE-KIT", 1)]),
    "TRAIN-BOM-MACHINE": ("TRAIN-CNC-1325", [("TRAIN-FRAME-1325", 1), ("TRAIN-CONTROL", 1), ("TRAIN-SPINDLE", 1)]),
}
WORKS = {
    "TRAIN-WO-FRAMES": ("TRAIN-BOM-FRAME", 3, MAIN, PRODUCTION, 2),
    "TRAIN-WO-SPINDLES": ("TRAIN-BOM-SPINDLE", 4, MAIN, PRODUCTION, 3),
    "TRAIN-WO-CONTROLS": ("TRAIN-BOM-CONTROL", 2, MAIN, PRODUCTION, 4),
    "TRAIN-WO-MACHINES": ("TRAIN-BOM-MACHINE", 2, PRODUCTION, SECONDARY, 7),
}
STOCK_NAMES = ["TRAIN-MATERIALS-001", "TRAIN-MFG-FRAMES", "TRAIN-MFG-SPINDLES", "TRAIN-MFG-CONTROLS",
               "TRAIN-MFG-MACHINE", "TRAIN-SERVICE-RETURN", "TRAIN-SERVICE-ISSUE"]
INSTALL_NAMES = ["TRAIN-INSTALL-FRAME", "TRAIN-INSTALL-CONTROL", "TRAIN-INSTALL-SPINDLE-OLD", "TRAIN-INSTALL-SPINDLE-NEW"]


def authorize():
    frappe.only_for("System Manager")
    if not frappe.conf.get("warehouse_demo") or not frappe.db.exists("Company", COMPANY):
        frappe.throw("Сценарій дозволено лише на налаштованому навчальному сайті MTech.")
    if frappe.db.exists("Company", {"name": ["!=", COMPANY]}):
        frappe.throw("На сайті є інша компанія; навчальні операції не створено.")
    for warehouse in (MAIN, SECONDARY, PRODUCTION):
        if not frappe.db.exists("Warehouse", {"name": warehouse, "company": COMPANY, "is_group": 0}):
            frappe.throw("Спочатку налаштуйте навчальні склади MTech командою demo.")


def submit(doctype, name, **values):
    doc = frappe.get_doc({"doctype": doctype, **values})
    doc.insert(set_name=name)
    doc.submit()
    return doc


def stock(name, day, hour, **values):
    return submit("Stock Entry", name, company=COMPANY, set_posting_time=1,
                  posting_date=day, posting_time=hour, remarks=NOTE, **values)


def manufacture(work, name, qty, day, hour, serials):
    from erpnext.manufacturing.doctype.work_order.work_order import make_stock_entry
    doc = frappe.get_doc(make_stock_entry(work, "Manufacture", qty))
    doc.set_posting_time, doc.posting_date, doc.posting_time = 1, day, hour
    doc.remarks = NOTE
    for row in doc.items:
        if row.item_code in serials:
            row.use_serial_batch_fields = 1
            row.serial_and_batch_bundle = None
            row.serial_no = "\n".join(serials[row.item_code])
    doc.insert(set_name=name)
    doc.submit()
    return doc


def installation(name, assembly, position, installed_at, notes):
    return submit("Machine Assembly Installation", name, company=COMPANY, machine_serial=MACHINE,
                  assembly_serial=assembly, position=position, installed_at=installed_at, notes=f"{NOTE}\n{notes}")


def summary(created=False):
    rows = [row for row in execute({"company": COMPANY})[1] if row["item_code"] in ITEMS]
    return {"created": created, "company": COMPANY, "machine": MACHINE,
            "expected_production": rows, "current_installations": get_assemblies(MACHINE)["rows"],
            "old_spindle": OLD_SPINDLE, "spare_spindle": SPARE_SPINDLE,
            "history": INSTALL_NAMES, "work_orders": list(WORKS),
            "note": "Повторний запуск зберігає ваші зміни та не відновлює початковий стан."}


def _seed():
    authorize()
    # Serialize setup runs and commit the completion marker with all transactions.
    frappe.db.get_value("Company", COMPANY, "name", for_update=True)
    if frappe.db.get_value("Comment", MARKER, "name", for_update=True):
        marker = frappe.get_doc("Comment", MARKER)
        if marker.reference_doctype != "Company" or marker.reference_name != COMPANY or NOTE not in marker.content:
            frappe.throw("Ідентифікатор навчального сценарію зайнятий іншим записом.")
        return summary()
    expected = {"Item": list(ITEMS), "BOM": list(BOMS), "Work Order": list(WORKS),
                "Stock Entry": STOCK_NAMES, "Machine Assembly Installation": INSTALL_NAMES,
                "Purchase Order": ["TRAIN-PO-SPINDLES"], "Maintenance Visit": ["TRAIN-SERVICE-001"],
                "Serial No": [MACHINE, FRAME, "TRAIN-FRAME-002", CONTROL, OLD_SPINDLE, CURRENT_SPINDLE, SPARE_SPINDLE]}
    for doctype, names in expected.items():
        if frappe.db.exists(doctype, {"name": ["in", names]}):
            frappe.throw(f"Знайдено записи {doctype} цього сценарію без позначки завершення. Перевірте їх; автоматичного перезапису немає.")

    started = add_days(nowdate(), -7)
    built = add_days(nowdate(), -5)
    for code, (title, rate, qty, kind) in ITEMS.items():
        frappe.get_doc({"doctype": "Item", "item_code": code, "item_name": title,
            "description": NOTE, "item_group": "Products", "stock_uom": "Nos", "is_stock_item": 1,
            "custom_is_machine": int(kind == "machine"), "custom_is_serialized_assembly": int(kind == "assembly"),
            "serial_no_series": code + "-AUTO-.####" if kind else None,
            "item_defaults": [{"company": COMPANY, "default_warehouse": SECONDARY if kind == "machine" else PRODUCTION if kind else MAIN}]}).insert()
    stock("TRAIN-MATERIALS-001", started, "08:00:00", stock_entry_type="Material Receipt",
          items=[{"item_code": code, "qty": qty, "t_warehouse": MAIN, "basic_rate": rate}
                 for code, (_, rate, qty, kind) in ITEMS.items() if not kind])
    # Create each BOM only after its subassemblies have a valuation from actual output.
    def make_bom(name):
        output, components = BOMS[name]
        return submit("BOM", name, company=COMPANY, item=output, quantity=1, currency="UAH",
                      items=[{"item_code": code, "qty": qty} for code, qty in components])

    def make_work(name):
        bom, qty, source, target, days = WORKS[name]
        doc = frappe.get_doc({"doctype": "Work Order", "company": COMPANY,
            "production_item": BOMS[bom][0], "bom_no": bom, "qty": qty, "use_multi_level_bom": 0,
            "source_warehouse": source, "wip_warehouse": PRODUCTION, "fg_warehouse": target,
            "skip_transfer": 1, "from_wip_warehouse": 0,
            "planned_start_date": f"{started} 09:00:00", "planned_end_date": f"{add_days(nowdate(), days)} 17:00:00",
            "description": NOTE})
        doc.get_items_and_operations_from_bom()
        doc.insert(set_name=name)
        doc.submit()

    for bom in ("TRAIN-BOM-FRAME", "TRAIN-BOM-SPINDLE", "TRAIN-BOM-CONTROL"):
        make_bom(bom)
    for work in ("TRAIN-WO-FRAMES", "TRAIN-WO-SPINDLES", "TRAIN-WO-CONTROLS"):
        make_work(work)
    manufacture("TRAIN-WO-FRAMES", "TRAIN-MFG-FRAMES", 2, built, "09:00:00", {"TRAIN-FRAME-1325": [FRAME, "TRAIN-FRAME-002"]})
    manufacture("TRAIN-WO-SPINDLES", "TRAIN-MFG-SPINDLES", 3, built, "10:00:00", {"TRAIN-SPINDLE": [OLD_SPINDLE, CURRENT_SPINDLE, SPARE_SPINDLE]})
    manufacture("TRAIN-WO-CONTROLS", "TRAIN-MFG-CONTROLS", 1, built, "11:00:00", {"TRAIN-CONTROL": [CONTROL]})
    make_bom("TRAIN-BOM-MACHINE")
    make_work("TRAIN-WO-MACHINES")
    manufacture("TRAIN-WO-MACHINES", "TRAIN-MFG-MACHINE", 1, built, "14:00:00", {
        "TRAIN-FRAME-1325": [FRAME], "TRAIN-SPINDLE": [OLD_SPINDLE],
        "TRAIN-CONTROL": [CONTROL], "TRAIN-CNC-1325": [MACHINE]})

    for serial, number, description in (
        (MACHINE, "TRAIN-1325-2026-001", "Робоче поле 1300 × 2500 мм; 3 осі; шпиндель 2,2 кВт. Параметри умовні."),
        (FRAME, "TRAIN-1325-2026-001", "Зварна рама з напрямними; фізичний номер збігається з номером верстата."),
        (CONTROL, "TRAIN-CTRL-001", "Контролер ЧПК, 3 сервоприводи; навчальна комплектація."),
        (OLD_SPINDLE, "TRAIN-SP-001", "Початковий шпиндель; під час навчального ремонту знятий для діагностики."),
        (CURRENT_SPINDLE, "TRAIN-SP-002", "Заміна початкового шпинделя; 2,2 кВт, цанга ER20, параметри умовні."),
        (SPARE_SPINDLE, "TRAIN-SP-003", "Вільний запасний шпиндель для самостійного тесту заміни."),
    ):
        doc = frappe.get_doc("Serial No", serial)
        doc.custom_manufacturer_serial_no = number
        doc.custom_technical_specifications = f"{NOTE}\n{description}"
        if serial == MACHINE:
            doc.custom_machine_configuration = f"{NOTE}\nРама, шафа керування й шпиндель. Актуальні серійні зв’язки — у таблиці встановлень."
            doc.custom_installation_details = f"{built}: навчальне складання та пробний запуск у майстерні; без передачі реальному клієнту."
            doc.warranty_expiry_date = add_days(built, 365)
        doc.save()
    installation("TRAIN-INSTALL-FRAME", FRAME, "Рама та напрямні", f"{built} 14:30:00", "Первинне складання; приклад спільного фізичного номера рами й верстата.")
    installation("TRAIN-INSTALL-CONTROL", CONTROL, "Шафа керування", f"{built} 14:35:00", "Первинне складання.")
    old = installation("TRAIN-INSTALL-SPINDLE-OLD", OLD_SPINDLE, "Шпиндель", f"{built} 14:40:00", "Первинне складання. Цей вузол буде замінено в навчальному сценарії.")
    remove_assembly(old.name, f"{NOTE} Підвищений шум підшипника на пробному запуску; заміна вузла, знятий — на діагностику.")
    returned = submit("Stock Entry", "TRAIN-SERVICE-RETURN", company=COMPANY, stock_entry_type="Material Receipt",
        remarks=f"{NOTE} Знятий шпиндель до огляду; умовна залишкова оцінка 3 500 грн. Встановлення {old.name}.",
        items=[{"item_code": "TRAIN-SPINDLE", "qty": 1, "t_warehouse": MAIN, "basic_rate": 3500,
                "use_serial_batch_fields": 1, "serial_no": OLD_SPINDLE}])
    issue = submit("Stock Entry", "TRAIN-SERVICE-ISSUE", company=COMPANY, stock_entry_type="Material Issue",
        remarks=f"{NOTE} Видача нового шпинделя для верстата {MACHINE}; навчальний ремонт.",
        items=[{"item_code": "TRAIN-SPINDLE", "qty": 1, "s_warehouse": PRODUCTION,
                "use_serial_batch_fields": 1, "serial_no": CURRENT_SPINDLE}])
    installation("TRAIN-INSTALL-SPINDLE-NEW", CURRENT_SPINDLE, "Шпиндель", now_datetime(),
                 f"Заміна {OLD_SPINDLE}; складські документи {returned.name}, {issue.name}.")
    person = frappe.db.get_value("Sales Person", {"is_group": 0}, "name")
    if not person:
        frappe.throw("Потрібен навчальний сервісний спеціаліст; спочатку виконайте demo.")
    submit("Maintenance Visit", "TRAIN-SERVICE-001", company=COMPANY, customer=CUSTOMER,
        mntc_date=nowdate(), completion_status="Fully Completed", maintenance_type="Unscheduled",
        purposes=[{"item_code": "TRAIN-CNC-1325", "serial_no": MACHINE, "service_person": person,
                   "work_done": f"{NOTE} Заміна {OLD_SPINDLE} на {CURRENT_SPINDLE}; перевірено обертання та пробне фрезерування. Знятий вузол повернуто на огляд, запасний {SPARE_SPINDLE} залишився на складі."}])
    submit("Purchase Order", "TRAIN-PO-SPINDLES", company=COMPANY, supplier=SUPPLIER,
        transaction_date=nowdate(), schedule_date=add_days(nowdate(), 5), currency="UAH", buying_price_list="Standard Buying",
        terms=NOTE, items=[{"item_code": "TRAIN-SPINDLE", "qty": 2, "rate": 16500,
                           "warehouse": PRODUCTION, "schedule_date": add_days(nowdate(), 5)}])
    frappe.get_doc({"doctype": "Comment", "comment_type": "Comment", "reference_doctype": "Company",
        "reference_name": COMPANY, "content": f"{NOTE}<br>Сценарій {MARKER}: верстат {MACHINE}, чотири виробничі замовлення, заміна шпинделя. Повторний запуск не скидає зміни користувача."}).insert(set_name=MARKER)
    return summary(created=True)


def seed():
    """CLI entry point: one atomic transaction, with a durable completion marker."""
    try:
        result = _seed()
        frappe.db.commit()
        return result
    except Exception:
        frappe.db.rollback()
        raise


def rehearse():
    """Verify the fresh seed, ledger quantities and rerun safety, then roll it back."""
    authorize()
    if frappe.db.exists("Comment", MARKER):
        frappe.throw("Репетицію виконують до створення сценарію; наявні навчальні дії не скидаються.")
    try:
        first = _seed()
        assert first["created"]
        expected = {"TRAIN-FRAME-1325": (1, 1, 0), "TRAIN-SPINDLE": (1, 1, 2),
                    "TRAIN-CONTROL": (0, 1, 0), "TRAIN-CNC-1325": (1, 1, 0)}
        assert {row["item_code"]: (row["actual_qty"], row["expected_qty"], row["ordered_qty"])
                for row in first["expected_production"]} == expected
        assert {row.assembly_serial for row in first["current_installations"]} == {FRAME, CONTROL, CURRENT_SPINDLE}
        old = frappe.get_doc("Machine Assembly Installation", "TRAIN-INSTALL-SPINDLE-OLD")
        assert old.removed_at and old.removal_reason and old.removed_by == frappe.session.user
        assert get_assemblies(OLD_SPINDLE)["rows"] == []
        assert frappe.db.get_value("Serial No", OLD_SPINDLE, "warehouse") == MAIN
        assert frappe.db.get_value("Serial No", SPARE_SPINDLE, "warehouse") == PRODUCTION
        for serial in (FRAME, CONTROL, CURRENT_SPINDLE):
            assert not frappe.db.get_value("Serial No", serial, "warehouse"), serial
        def snapshot():
            return {dt: frappe.get_all(dt, fields=["*"], order_by="name", limit_page_length=0)
                    for dt in ("Stock Ledger Entry", "GL Entry", "Bin", "Work Order", "Machine Assembly Installation", "Serial No", "Has Role")}
        before = json.dumps(snapshot(), sort_keys=True, default=str)
        assert not _seed()["created"]
        assert json.dumps(snapshot(), sort_keys=True, default=str) == before, "Rerun changed transactions or roles"
        return {"ok": True, "checks": ["Four partially completed orders each expect one unit", "Purchasing is separate from production",
            "Installed serials consumed through native stock documents", "Old/new/spare spindle and service history agree",
            "Repeat execution leaves ledgers, serials, links and roles unchanged"], "test_data": "rolled back"}
    finally:
        frappe.db.rollback()
