import frappe
from frappe.model.document import Document


class KnowledgeQueryLog(Document):
    def validate(self):
        if not self.is_new() or not self.flags.knowledge_audit:
            frappe.throw("Журнал ведеться автоматично.", frappe.PermissionError)

    def on_trash(self):
        frappe.throw("Записи журналу не можна видаляти через інтерфейс.")
