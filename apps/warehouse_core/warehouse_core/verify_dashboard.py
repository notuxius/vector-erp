"""Dashboard comparisons with temporary role fixtures; all changes roll back."""
import uuid
import frappe
from warehouse_core.dashboard import get_overview


def run():
    original = frappe.session.user
    users = frappe.get_all("User", filters={"enabled": 1, "user_type": "System User"}, pluck="name")
    checked = []
    try:
        frappe.set_user("Administrator")
        for role in ("Sales User", "Accounts User", "Purchase User", "Stock User", None):
            email = f"verify-dashboard-{uuid.uuid4().hex[:12]}@example.test"
            frappe.get_doc({"doctype": "User", "email": email, "first_name": "Dashboard check",
                            "user_type": "System User", "send_welcome_email": 0,
                            "roles": [{"role": role}] if role else [{"role": "Desk User"}]}).insert()
            users.append(email)
        for user in users:
            frappe.set_user(user)
            data = get_overview()
            for company in [None, *data["companies"]]:
                overview = get_overview(company)
                returned = {card["doctype"] for card in overview["cards"]}
                for doctype in ("Sales Order", "Sales Invoice", "Purchase Invoice", "Purchase Order", "Delivery Note", "Stock Entry"):
                    assert (doctype in returned) == bool(frappe.has_permission(doctype, "read")), (user, doctype)
                chart_sections = {"sales": "sales", "payments": "accounting", "purchases": "purchasing", "stock": "stock"}
                assert {section["key"] for section in overview["sections"]} == ({card["section"] for card in overview["cards"]}
                    | {chart_sections[chart["key"]] for chart in overview["chart_data"]["charts"]})
                from warehouse_core.dashboard_charts import can_read
                chart_keys = {chart["key"] for chart in overview["chart_data"]["charts"]}
                if overview["chart_data"]["currency"]:
                    for key, dt, fields in [("sales", "Sales Invoice", ["posting_date", "base_net_total"]),
                            ("purchases", "Purchase Invoice", ["posting_date", "base_net_total"]),
                            ("payments", "Payment Entry", ["posting_date", "payment_type", "base_received_amount", "base_paid_amount"])]:
                        assert (key in chart_keys) == bool(can_read(dt, fields)), (user, key)
                for card in overview["cards"]:
                    rows = frappe.get_list(card["doctype"], filters=card["filters"],
                                           pluck="name", limit_page_length=0)
                    assert card["value"] == len(rows), (user, company, card["key"])
                for table in overview["tables"]:
                    permitted = set(frappe.get_list(table["doctype"], filters=table["filters"],
                                                   pluck="name", limit_page_length=0))
                    assert {row.name for row in table["rows"]}.issubset(permitted)
                    assert len(table["rows"]) <= 6
            try:
                get_overview("__inaccessible_company__")
                raise AssertionError("Invalid company was accepted")
            except frappe.PermissionError:
                frappe.clear_messages()
            checked.append(user)
        frappe.set_user("Guest")
        try:
            get_overview()
            raise AssertionError("Guest was allowed")
        except frappe.PermissionError:
            frappe.clear_messages()
        return {"ok": True, "users_checked": len(checked), "counts_match_visible_records": True,
                "company_scope_checked": True, "guest_denied": True}
    finally:
        frappe.db.rollback()
        frappe.set_user(original)
