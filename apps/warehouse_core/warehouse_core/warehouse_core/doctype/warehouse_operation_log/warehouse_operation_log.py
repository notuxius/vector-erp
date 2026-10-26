import frappe
from frappe.model.document import Document


class WarehouseOperationLog(Document):
    def before_insert(self):
        if not self.flags.from_operation_hook:
            frappe.throw("Записи журналу створюються лише під час проведення або скасування операцій.", frappe.PermissionError)

    def validate(self):
        if not self.is_new():
            frappe.throw("Записи журналу операцій не можна змінювати.", frappe.PermissionError)

    def on_trash(self):
        frappe.throw("Записи журналу операцій не можна видаляти.", frappe.PermissionError)
