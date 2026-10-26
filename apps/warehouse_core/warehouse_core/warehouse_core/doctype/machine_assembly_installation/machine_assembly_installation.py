"""Assembly genealogy independent of stock movements. Serial rows serialize changes."""
import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime, now_datetime


class MachineAssemblyInstallation(Document):
    def validate(self):
        if self.machine_serial == self.assembly_serial:
            frappe.throw("Верстат і вузол мають бути різними серійними записами.")
        machine = frappe.get_doc("Serial No", self.machine_serial)
        assembly = frappe.get_doc("Serial No", self.assembly_serial)
        for serial in (machine, assembly):
            serial.check_permission("read")
            if serial.company != self.company:
                frappe.throw("Верстат і вузол мають належати компанії встановлення.")
        if not frappe.db.get_value("Item", machine.item_code, "custom_is_machine"):
            frappe.throw("Для верстата виберіть товар із позначкою «Верстат із серійним обліком».")
        if not frappe.db.get_value("Item", assembly.item_code, "custom_is_serialized_assembly"):
            frappe.throw("Для вузла виберіть товар із позначкою «Вузол із серійним обліком».")
        if not self.installed_at or get_datetime(self.installed_at) > now_datetime():
            frappe.throw("Дата встановлення обов’язкова й не може бути в майбутньому.")
        # Read-only form fields must also be protected against direct API writes.
        if self.docstatus == 0:
            self.installed_by = None
            self.removed_at = self.removed_by = self.removal_reason = None

    def lock_assembly(self):
        frappe.db.sql("select name from `tabSerial No` where name=%s for update", self.assembly_serial)

    def before_submit(self):
        self.lock_assembly()
        self.removed_at = self.removed_by = self.removal_reason = None
        # Locking reads see the latest committed rows even under repeatable-read.
        previous = frappe.db.sql("""select name, installed_at, removed_at
            from `tabMachine Assembly Installation`
            where assembly_serial=%s and docstatus=1 and name!=%s for update""",
            (self.assembly_serial, self.name), as_dict=True)
        if any(not row.removed_at for row in previous):
            frappe.throw("Вузол уже встановлено. Спочатку зафіксуйте його зняття.")
        if any(get_datetime(row.removed_at) > get_datetime(self.installed_at) for row in previous):
            frappe.throw("Дата встановлення не може передувати попередньому зняттю цього вузла.")
        self.installed_by = frappe.session.user

    def before_update_after_submit(self):
        self.lock_assembly()
        old = frappe.db.get_value(self.doctype, self.name,
                                  ["removed_at", "removed_by", "removal_reason"], as_dict=True, for_update=True)
        if not self.flags.record_removal or old.removed_at:
            frappe.throw("Проведене встановлення змінюється лише дією «Зняти вузол».")
        if not self.removal_reason or not self.removal_reason.strip():
            frappe.throw("Вкажіть причину зняття.")
        self.removed_at = now_datetime()
        self.removed_by = frappe.session.user
        if get_datetime(self.removed_at) < get_datetime(self.installed_at):
            frappe.throw("Зняття не може передувати встановленню.")

    def before_cancel(self):
        self.lock_assembly()
        removed_at = frappe.db.get_value(self.doctype, self.name, "removed_at", for_update=True)
        if removed_at:
            frappe.throw("Завершену історію встановлення не можна скасувати.")

    def on_trash(self):
        if self.docstatus != 0:
            frappe.throw("Історію встановлення вузлів не можна видаляти.")


@frappe.whitelist()
def remove_assembly(name, reason):
    doc = frappe.get_doc("Machine Assembly Installation", name)
    doc.check_permission("write")
    if doc.docstatus != 1 or doc.removed_at:
        frappe.throw("Зняти можна лише вузол із чинного проведеного встановлення.")
    doc.flags.record_removal = True
    doc.removal_reason = str(reason or "").strip()
    doc.save()
    return doc.name


def has_permission(doc, user=None, ptype=None):
    # Frappe combines True with native role/company checks; this hook only denies.
    for field in ("machine_serial", "assembly_serial"):
        if doc.get(field) and not frappe.has_permission("Serial No", "read", doc=doc.get(field), user=user):
            return False
    return True


def permission_query(user=None):
    # Native subquery includes company, warehouse, item, serial and shared-record
    # permissions without materializing the full serial catalogue in application RAM.
    if not frappe.has_permission("Serial No", "read", user=user):
        return "1=0"
    from frappe.model.db_query import DatabaseQuery
    query = DatabaseQuery("Serial No", user=user)
    sql = query.execute(fields=["name"], user=user, limit_page_length=0, order_by="", run=False)
    return " and ".join(f"`tabMachine Assembly Installation`.`{field}` in ({sql})"
                        for field in ("machine_serial", "assembly_serial"))
