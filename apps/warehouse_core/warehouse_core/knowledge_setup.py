"""Add knowledge configuration without changing existing users or business records."""
import frappe


def apply():
    for role in ("Knowledge Editor", "Knowledge Publisher"):
        if not frappe.db.exists("Role", role):
            frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(ignore_permissions=True)
    frappe.db.add_index("Knowledge Article", ["status", "cloud_allowed"])
    frappe.db.add_index("Knowledge Attachment", ["article"])
    frappe.db.add_index("Knowledge Access User", ["user", "parent"])
    frappe.db.add_index("Knowledge Access Role", ["role", "parent"])
    title = "Початок роботи з базою знань"
    legacy_content = (
                "Це початкова інструкція для перевірки видавцем.\n\n"
                "Створіть статтю, заповніть її текст і категорію. У тексті зазначте перевірені факти, "
                "джерело та дату перевірки. Вкладення зберігаються окремо: їхній текст автоматично не потрапляє до пошуку.\n\n"
                "Видавець визначає робочі ролі або конкретних працівників із доступом. Без списку доступу "
                "публікація неможлива. Дозвіл на використання тексту в ШІ задається окремо.\n\n"
                "Збережіть статтю та передайте її на перевірку. Видавець встановлює стан «Опубліковано». "
                "Зміна тексту, доступу, строку дії або додавання файлу повертає опубліковану статтю до чернетки.\n\n"
                "У «ШІ-помічнику» виберіть відповідь із бази знань або чернетку відповіді клієнту. "
                "Використовуйте назву товару й конкретні ключові слова. Кожне запитання розглядається окремо. "
                "Перевіряйте відповідь за посиланнями на джерела. Помічник не змінює бізнес-записи й не надсилає повідомлень."
            )
    pdf_content = legacy_content.replace(
        "Вкладення зберігаються окремо: їхній текст автоматично не потрапляє до пошуку.",
        "Можна зберегти чернетку лише з назвою, потім додати захищений PDF/DOCX: текст буде "
        "автоматично додано для перевірки. Скани та зображення поки не розпізнаються. "
        "Перевірте результат витягування біля файлу, порядок тексту, таблиці й числа.")
    office_content = pdf_content.replace("PDF/DOCX:", "PDF/DOCX/XLSX/TXT:")
    content = office_content.replace("У «ШІ-помічнику»", "У чаті з Векторією")
    name = frappe.db.get_value("Knowledge Article", {"title": title}, "name")
    if not name:
        frappe.get_doc({"doctype": "Knowledge Article", "title": title, "category": "Інструкції",
            "status": "Draft", "cloud_allowed": 0, "content": content}).insert(ignore_permissions=True)
    else:
        doc = frappe.get_doc("Knowledge Article", name)
        # Update only the untouched starter; preserve user edits and any approvals.
        if (doc.status == "Draft" and doc.owner == "Administrator" and doc.content in (legacy_content, pdf_content, office_content)
                and not doc.cloud_allowed and not doc.allowed_roles and not doc.allowed_users):
            doc.content = content
            doc.save(ignore_permissions=True)
