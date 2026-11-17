"""Real Desk report, admin notification and Codex write-back, rolled back together."""
import uuid
from unittest.mock import patch

import frappe
from warehouse_core.warehouse_core.doctype.vectoria_feedback import vectoria_feedback as feedback


def run():
    original = frappe.session.user
    staff = frappe.db.get_value("User", {"user_type": "System User", "enabled": 1, "name": ["!=", "Administrator"]})
    assert staff, "A non-administrator Desk user is needed"
    token = uuid.uuid4().hex
    try:
        frappe.set_user("Guest")
        try:
            feedback.submit("Помилка", "example", "", token)
        except frappe.PermissionError:
            pass
        else:
            raise AssertionError("Guest submitted feedback")
        frappe.set_user(staff)
        name = feedback.submit("Помилка", "Приклад помилки", "Приклад контексту", token)
        assert name == feedback.submit("Помилка", "Приклад помилки", "Приклад контексту", token)
        assert not frappe.has_permission("Vectoria Feedback", doc=name, ptype="read")
        assert frappe.db.count("Notification Log", {"document_type": "Vectoria Feedback", "document_name": name,
                                                    "for_user": "Administrator", "type": "Alert"}) == 1
        frappe.set_user("Administrator")
        doc = frappe.get_doc("Vectoria Feedback", name)
        assert doc.has_permission("read") and doc.reported_by == staff
        assert {"name": name, "category": "Помилка", "message": "Приклад помилки", "creation": doc.creation,
                "context": "Приклад контексту"} in feedback.pending()["reports"] or frappe.db.count("Vectoria Feedback") > 5
        feedback.record_analysis(name, "Перевірено")
        feedback.record_analysis(name, "Повтор")
        assert doc.reload().analysis == "Перевірено"
        assert name not in [r.name for r in feedback.pending()["reports"]]
        with patch.object(feedback, "time") as clock:
            clock.time.return_value = 3600
            auto = feedback.report_error("send", "knowledge", "503",
                '{"observed_at":"2026-11-17T09:10:00.000Z","build":"abc012345678","viewport":"390x800",'
                '"page":"/desk/private-record","connection":"offline","message":"private text"}')
            context = frappe.db.get_value("Vectoria Feedback", auto, "context")
            assert all(value in context for value in ("09:10:00.000Z", "abc012345678", "390x800", "offline"))
            assert "private" not in context
            frappe.set_user(staff)
            assert auto == feedback.report_error("send", "knowledge", "503")
            assert not frappe.has_permission("Vectoria Feedback", doc=auto, ptype="read")
            assert frappe.db.count("Notification Log", {"document_name": auto, "for_user": "Administrator"}) == 1
            try:
                feedback.report_error("send", "knowledge", "403")
            except frappe.ValidationError:
                pass
            else:
                raise AssertionError("Permission error accepted as a technical failure")
        return {"passed": True, "report_and_notification": "rolled back"}
    finally:
        frappe.db.rollback()
        frappe.set_user(original)
