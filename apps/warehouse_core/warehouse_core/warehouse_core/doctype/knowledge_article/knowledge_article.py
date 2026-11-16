import frappe
from frappe.model.document import Document
from frappe.utils import cint, now_datetime

from warehouse_core.knowledge import MAX_CONTENT, allowed, current, editor, publisher


class KnowledgeArticle(Document):
    def validate(self):
        old = self.get_doc_before_save()
        if not editor() or (old and not allowed(old, write=True)):
            frappe.throw("Редагування статті недоступне.", frappe.PermissionError)
        if old and self.owner != old.owner:
            frappe.throw("Автора статті не можна змінювати.", frappe.PermissionError)
        if not old:
            self.owner = frappe.session.user
        self.title = (self.title or "").strip()
        self.content = (self.content or "").strip()
        if not self.title or len(self.title) > 180 or len(self.content) > MAX_CONTENT:
            frappe.throw("Вкажіть назву до 180 символів і текст до 100 000 символів.")
        if self.status in ("Review", "Published") and not self.content:
            frappe.throw("Перед перевіркою або публікацією додайте текст статті чи завантажте PDF/DOCX/XLSX/TXT.")
        if self.status not in ("Draft", "Review", "Published", "Archived"):
            frappe.throw("Некоректний стан статті.")
        acl = lambda d: (sorted(r.role for r in d.allowed_roles), sorted(r.user for r in d.allowed_users), cint(d.cloud_allowed))
        if not publisher():
            previous = acl(old) if old else ([], [], 0)
            if acl(self) != previous or self.status in ("Published", "Archived") and (not old or self.status != old.status):
                frappe.throw("Публікацію, доступ і передачу до ШІ налаштовує видавець.", frappe.PermissionError)
        for row in self.allowed_roles:
            if row.role in ("All", "Guest", "Administrator", "System Manager", "Knowledge Publisher"):
                frappe.throw("Виберіть конкретні робочі ролі. Адміністратори та видавці вже мають доступ.")
        for row in self.allowed_users:
            from warehouse_core.knowledge import desk_user
            if not desk_user(row.user):
                frappe.throw("Доступ можна надати лише активному працівнику.")
        changed = not old or any(self.get(field) != old.get(field) for field in
            ("title", "content", "category", "valid_until")) or (old and acl(self) != acl(old)) or self.flags.knowledge_attachment_changed
        self.revision = (cint(old.revision) if old else 0) + (1 if changed else 0)
        if old and changed and old.status == "Published":
            # Every modification withdraws the old approval, including publisher edits.
            self.status = "Draft"
        if self.status == "Published":
            if not current(self):
                frappe.throw("Строк дії статті минув.")
            if not (self.allowed_roles or self.allowed_users):
                frappe.throw("Перед публікацією вкажіть користувачів або робочі ролі з доступом.")
            if not old or old.status != "Published":
                if not publisher():
                    frappe.throw("Публікація доступна лише видавцю.", frappe.PermissionError)
                self.approved_by = frappe.session.user
                self.approved_at = now_datetime()
            else:
                self.approved_by, self.approved_at = old.approved_by, old.approved_at
        else:
            self.approved_by = self.approved_at = None

    def on_trash(self):
        frappe.throw("Замість видалення переведіть статтю в архів.")

    def before_rename(self, *args, **kwargs):
        frappe.throw("Ідентифікатор статті не можна змінювати.")
