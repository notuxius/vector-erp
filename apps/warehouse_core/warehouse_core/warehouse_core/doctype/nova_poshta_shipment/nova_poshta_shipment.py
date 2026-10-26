import frappe
from frappe.model.document import Document

MANAGED = ("state", "waybill", "carrier_ref", "delivery_cost", "last_error", "requested_by", "requested_at")


class NovaPoshtaShipment(Document):
    def validate(self):
        from warehouse_core.nova_poshta import authorize, phone
        delivery = authorize(self.delivery_note)
        if self.is_new():
            if delivery.docstatus != 1 or delivery.is_return or delivery.custom_nova_poshta_ttn:
                frappe.throw("Виберіть проведене відвантаження без ТТН, яке не є поверненням.")
            self.company = delivery.company
            if self.state != "Draft" or any(self.get(key) for key in MANAGED if key != "state"):
                frappe.throw("Стан і ТТН заповнює інтеграція.")
        else:
            frappe.db.get_value(self.doctype, self.name, "name", for_update=True)
            previous = frappe.get_doc(self.doctype, self.name)
            if previous.state != "Draft":
                frappe.throw("Після запиту до перевізника дані відправлення заблоковані.")
            if any(previous.get(key) != self.get(key) for key in (*MANAGED, "delivery_note", "company")):
                frappe.throw("Службові дані відправлення змінює лише інтеграція.")
        if self.company != delivery.company:
            frappe.throw("Компанія має відповідати відвантаженню.")
        if self.recipient_phone:
            self.recipient_phone = phone(self.recipient_phone)


def has_permission(doc, ptype=None, user=None):
    if not doc.delivery_note:
        return True
    if not frappe.has_permission("Delivery Note", "read", doc=doc.delivery_note, user=user):
        return False
    return True


def permission_query(user=None):
    # Reuse native document/user restrictions; do not expose customer details from
    # shipments whose source Delivery Note the user cannot list.
    candidates = frappe.get_all("Nova Poshta Shipment", pluck="delivery_note", limit_page_length=0)
    names = [name for name in candidates if frappe.has_permission("Delivery Note", "read", doc=name, user=user)]
    if not names:
        return "1=0"
    return "`tabNova Poshta Shipment`.`delivery_note` in (" + ",".join(frappe.db.escape(n) for n in names) + ")"
