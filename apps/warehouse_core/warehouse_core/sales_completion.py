"""Read-only completion checks; billing is not payment or business completion.

Keep native stock/accounting status intact. Unknown return obligations, settlement
by write-off, and additional client conditions must never become automatic Done.
"""
import frappe
from frappe.utils import flt

from warehouse_core.warehouse_core.report.order_readiness.order_readiness import (
    linked_documents,
    payment_status,
)


def return_status(order, invoice_names, delivery_names):
    """Discover returns through both order lines and their original document.

    Existence queries only establish whether the check is incomplete. Document
    names and quantities are never returned without parent read permissions.
    Drafts count because a return can still be awaiting goods or money.
    """
    for doctype, childtype, order_field, originals in (
        ("Delivery Note", "Delivery Note Item", "against_sales_order", delivery_names),
        ("Sales Invoice", "Sales Invoice Item", "sales_order", invoice_names),
    ):
        if not frappe.has_permission(doctype, "read"):
            return "Немає доступу для перевірки повернень"
        parent, child = frappe.qb.DocType(doctype), frappe.qb.DocType(childtype)
        linked = child[order_field] == order.name
        if originals:
            linked = linked | parent.return_against.isin(list(originals))
        names = {
            row.name for row in (
                frappe.qb.from_(parent).join(child).on(child.parent == parent.name)
                .select(parent.name).distinct()
                .where((parent.company == order.company) & (parent.docstatus < 2)
                    & (parent.is_return == 1) & linked)
            ).run(as_dict=True)
        }
        if not names:
            continue
        visible = set(frappe.get_list(doctype, filters={"name": ["in", list(names)]},
            pluck="name", limit_page_length=0))
        if names - visible:
            return "Немає доступу для перевірки повернень"
        # Do not infer that goods/refunds are complete merely from a credit note.
        return "Є повернення — перевірте товар і кошти"
    return "Пов’язаних документів повернення не знайдено"


@frappe.whitelist()
def get_completion_check(sales_order):
    order = frappe.get_doc("Sales Order", sales_order)
    order.check_permission("read")
    frappe.get_doc("Company", order.company).check_permission("read")
    invoice_links, invoices, delivery_links, _ = linked_documents({order.name: order}, order.company)
    payment = payment_status(order, invoice_links[order.name], invoices)
    returns = return_status(order, invoice_links[order.name], delivery_links[order.name])
    delivered = flt(order.per_delivered)
    shipment = "Відвантажено повністю" if delivered >= 99.999 else (
        "Відвантажено частково" if delivered > 0 else "Не відвантажено"
    )
    if order.docstatus == 0:
        conclusion = "Чернетка — замовлення не проведено"
    elif order.docstatus == 2:
        conclusion = "Замовлення скасовано"
    elif order.status == "Closed":
        conclusion = "Замовлення закрито — перевірте розрахунки та причину закриття"
    elif returns != "Пов’язаних документів повернення не знайдено":
        conclusion = "Завершення потребує перевірки повернень"
    elif payment in ("Очікується оплата", "Очікується повернення або залік коштів"):
        conclusion = "Не завершено: є неврегульовані розрахунки"
    elif delivered < 99.999:
        conclusion = "Не завершено: відвантаження не повне"
    else:
        # Even settled invoices may include write-offs or allocations shared with
        # other orders. No data here establishes every client completion condition.
        conclusion = "Перевірте оплату та інші умови завершення"
    return {
        "shipment": shipment,
        "delivered_percent": delivered,
        "payment": payment,
        "returns": returns,
        "conclusion": conclusion,
        "native_completed": order.status == "Completed",
        "note": (
            "Виконано — усі необхідні умови виконані, переважно товар відвантажено й повністю оплачено. "
            "За наявності повернення перевірте повернення товару та коштів. "
            "Погашення рахунків може включати залік або списання; відсутність документа повернення "
            "не підтверджує відсутність звернення клієнта. Ця перевірка не завершує замовлення автоматично."
        ),
    }
