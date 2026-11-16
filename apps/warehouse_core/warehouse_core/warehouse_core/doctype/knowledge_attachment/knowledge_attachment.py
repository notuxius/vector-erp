import frappe
from frappe.model.document import Document


class KnowledgeAttachment(Document):
    def validate(self):
        if not self.is_new() or not self.flags.knowledge_upload:
            frappe.throw("Файли додаються лише через захищене завантаження у статті.", frappe.PermissionError)

    def on_trash(self):
        frappe.throw("Файл зберігається разом зі статтею. За потреби архівуйте статтю.")

    def before_rename(self, *args, **kwargs):
        frappe.throw("Ідентифікатор файлу не можна змінювати.")
