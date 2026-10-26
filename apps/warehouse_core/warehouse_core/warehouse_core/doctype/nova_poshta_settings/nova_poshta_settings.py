import frappe
from frappe.model.document import Document


class NovaPoshtaSettings(Document):
    def validate(self):
        from warehouse_core.nova_poshta import phone
        if self.sender_phone:
            self.sender_phone = phone(self.sender_phone)
        previous = self.get_doc_before_save()
        if previous and previous.company != self.company:
            frappe.throw("Для іншої компанії створіть окремі налаштування.")

