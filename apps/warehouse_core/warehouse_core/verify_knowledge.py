"""Transactional security checks against real Frappe permissions; no paid requests."""
import base64
import io
import json
import unittest
import uuid
from unittest.mock import patch

import frappe
from frappe.utils import add_days, nowdate
from warehouse_core import knowledge as kb, ai_chat


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        self.suffix = uuid.uuid4().hex[:10]
        self.users = {}
        for label, roles in (("writer", ["Knowledge Editor"]), ("reader", ["Stock User"]),
                             ("outsider", ["Sales User"]), ("publisher", ["Knowledge Publisher"])):
            email = f"kb-{label}-{self.suffix}@example.invalid"
            user = frappe.get_doc({"doctype": "User", "email": email, "first_name": label,
                "enabled": 1, "user_type": "System User", "send_welcome_email": 0,
                "roles": [{"role": role} for role in roles]})
            user.flags.no_welcome_mail = True
            # Fixture creation is rolled back; do not consume the production signup throttle.
            with patch("frappe.core.doctype.user.user.throttle_user_creation"):
                user.insert(ignore_permissions=True)
            self.users[label] = email
        self.article = frappe.get_doc({"doctype": kb.ARTICLE, "title": "Гарантія " + self.suffix,
            "content": "Гарантія Соняшник: зверніться до сервісної служби.", "category": "Сервіс і гарантія",
            "status": "Draft", "allowed_users": [{"user": self.users["reader"]}], "cloud_allowed": 1}).insert()

    def tearDown(self):
        frappe.set_user("Administrator")

    def publish(self):
        frappe.set_user("Administrator")
        self.article.reload()
        self.article.status = "Published"
        self.article.save()

    def as_user(self, name):
        frappe.set_user(self.users[name])

    def test_default_deny_and_guests(self):
        for who in ("reader", "outsider"):
            self.as_user(who)
            self.assertFalse(frappe.has_permission(kb.ARTICLE, doc=self.article.name))
            self.assertNotIn(self.article.name, frappe.get_list(kb.ARTICLE, pluck="name"))
            self.assertEqual(kb.search(self.suffix)["articles"], [])
        frappe.set_user("Guest")
        with self.assertRaises(frappe.PermissionError): kb.search(self.suffix)

    def test_published_user_acl_and_direct_rest_boundary(self):
        self.publish()
        self.as_user("reader")
        self.assertTrue(frappe.has_permission(kb.ARTICLE, doc=self.article.name))
        self.assertIn(self.article.name, frappe.get_list(kb.ARTICLE, pluck="name"))
        self.assertEqual(kb.retrieve("Соняшник")[0]["name"], self.article.name)
        self.as_user("outsider")
        self.assertFalse(frappe.has_permission(kb.ARTICLE, doc=self.article.name))
        self.assertEqual(kb.search(self.suffix)["articles"], [])
        with self.assertRaises(frappe.PermissionError): kb.attachments(self.article.name)

    def test_role_acl(self):
        self.article.allowed_users = []
        self.article.append("allowed_roles", {"role": "Stock User"})
        self.article.save()
        self.publish()
        self.as_user("reader")
        self.assertTrue(frappe.has_permission(kb.ARTICLE, doc=self.article.name))
        self.as_user("outsider")
        self.assertFalse(frappe.has_permission(kb.ARTICLE, doc=self.article.name))

    def test_short_guide_keeps_opening_steps_for_a_late_match(self):
        self.article.content = "Відкрийте складську операцію. " + "Оберіть склад. " * 20 + self.suffix
        self.article.save()
        self.publish()
        self.as_user("reader")
        self.assertEqual(kb.retrieve(self.suffix)[0]["excerpt"], self.article.content)

    def test_access_lists_do_not_leak_through_child_table_api(self):
        from frappe.client import get_list
        self.publish()
        self.as_user("outsider")
        rows = get_list("Knowledge Access User", fields=["parent", "user"], parent=kb.ARTICLE,
                        filters={"parent": self.article.name})
        self.assertEqual(rows, [])
        self.as_user("reader")
        rows = get_list("Knowledge Access User", fields=["parent", "user"], parent=kb.ARTICLE,
                        filters={"parent": self.article.name})
        self.assertEqual(rows[0].user, self.users["reader"])

    def test_editor_cannot_publish_or_approve_cloud(self):
        self.as_user("writer")
        doc = frappe.get_doc({"doctype": kb.ARTICLE, "title": "Чернетка", "content": "Текст", "status": "Draft", "category": "Інструкції"}).insert()
        doc.status = "Published"
        with self.assertRaises(frappe.PermissionError): doc.save()
        doc.reload(); doc.cloud_allowed = 1
        with self.assertRaises(frappe.PermissionError): doc.save()
        doc.reload(); doc.approved_by = self.users["publisher"]; doc.approved_at = "2026-01-01"; doc.save()
        self.assertFalse(doc.approved_by)
        self.assertFalse(doc.approved_at)

    def test_publisher_role_can_publish(self):
        self.as_user("publisher")
        self.article.status = "Published"; self.article.save()
        self.assertEqual(self.article.approved_by, self.users["publisher"])

    def test_edits_withdraw_approval_and_increment_revision(self):
        self.publish(); revision = self.article.revision
        self.article.content += " Нова редакція."; self.article.save()
        self.assertEqual(self.article.status, "Draft")
        self.assertEqual(self.article.revision, revision + 1)
        self.assertFalse(self.article.approved_by)
        self.as_user("reader"); self.assertEqual(kb.retrieve("Соняшник"), [])

    def test_local_only_not_sent_to_provider(self):
        self.article.cloud_allowed = 0; self.article.save(); self.publish()
        self.as_user("reader")
        self.assertTrue(kb.search(self.suffix)["articles"])
        with patch.object(ai_chat, "generate_text") as provider:
            result = ai_chat.knowledge_answer("Соняшник", "knowledge")
            self.assertEqual(result["sources"], [])
            provider.assert_not_called()

    def test_specific_model_uses_only_its_catalog_entry(self):
        code = "TEST-" + self.suffix.upper()
        article = frappe.get_doc({"doctype": kb.ARTICLE, "title": "Картриджі · " + code,
            "content": f"Інший картридж {code}0\n24000 об/хв\n\nКартридж {code}\n12000 об/хв",
            "category": "Товари", "status": "Published", "cloud_allowed": 1,
            "allowed_users": [{"user": self.users["reader"]}]}).insert(set_name="KB-MTECH-catalog-" + self.suffix)
        self.as_user("reader")
        source = kb.retrieve(code + " — характеристики")[0]
        self.assertEqual(source["name"], article.name)
        self.assertIn("12000", source["excerpt"])
        self.assertNotIn("24000", source["excerpt"])

    def test_expired_and_archived_excluded(self):
        self.publish()
        frappe.db.set_value(kb.ARTICLE, self.article.name, "valid_until", add_days(nowdate(), -1))
        self.as_user("reader")
        self.assertEqual(kb.retrieve("Соняшник"), [])
        self.assertFalse(frappe.has_permission(kb.ARTICLE, doc=self.article.name))
        frappe.set_user("Administrator"); self.article.reload(); self.article.status = "Archived"; self.article.save()
        self.as_user("reader"); self.assertEqual(kb.search(self.suffix)["articles"], [])

    def test_protected_attachment_download_and_revocation(self):
        file = kb.upload(self.article.name, "manual.txt", base64.b64encode("Тест".encode()).decode())
        self.publish(); self.as_user("reader")
        kb.download(file["name"])
        self.assertEqual(frappe.local.response["filecontent"], "Тест".encode())
        self.assertTrue(frappe.has_permission(kb.ATTACHMENT, doc=file["name"]))
        self.as_user("outsider")
        with self.assertRaises(frappe.PermissionError): kb.download(file["name"])
        self.assertFalse(frappe.has_permission(kb.ATTACHMENT, doc=file["name"]))
        self.assertNotIn(file["name"], frappe.get_list(kb.ATTACHMENT, pluck="name"))
        frappe.set_user("Administrator"); self.article.allowed_users=[]; self.article.save()
        self.as_user("reader")
        with self.assertRaises(frappe.PermissionError): kb.download(file["name"])
        frappe.local.response.pop("filecontent", None); frappe.local.response.pop("type", None)

    def test_upload_withdraws_publication_and_is_immutable(self):
        self.publish()
        file = kb.upload(self.article.name, "manual.txt", base64.b64encode(b"manual").decode())
        self.article.reload(); self.assertEqual(self.article.status, "Draft")
        doc = frappe.get_doc(kb.ATTACHMENT, file["name"]); doc.article = "elsewhere"
        with self.assertRaises((frappe.PermissionError, frappe.LinkValidationError)): doc.save(ignore_permissions=True)

    def test_standard_attachments_and_shares_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            kb.reject_standard_attachment(frappe.get_doc({"doctype": "File", "attached_to_doctype": kb.ARTICLE}))
        with self.assertRaises(frappe.PermissionError):
            frappe.get_doc({"doctype": "DocShare", "share_doctype": kb.ARTICLE, "share_name": self.article.name,
                "user": self.users["outsider"], "read": 1}).insert(ignore_permissions=True)

    def test_file_validation_and_unauthorized_upload(self):
        for name, data in (("../a.txt", "YWJj"), ("a.html", "YWJj"), ("a.pdf", "YWJj"), ("a.txt", "bad")):
            with self.assertRaises(frappe.ValidationError): kb.upload(self.article.name, name, data)
        self.as_user("outsider")
        with self.assertRaises(frappe.PermissionError): kb.upload(self.article.name, "a.txt", "YWJj")

    def test_extraction_requires_renewed_approval_and_preserves_acl(self):
        from warehouse_core.verify_knowledge_extraction import make_pdf, make_docx, make_xlsx
        for suffix, raw, marker in (("pdf", make_pdf(), "ExtractionMarker"),
                                    ("docx", make_docx("ManualDocxMarker"), "ManualDocxMarker"),
                                    ("xlsx", make_xlsx(), "XlsxMarker"),
                                    ("txt", "\ufeffTxtMarker Інструкція".encode("utf-8"), "TxtMarker")):
            self.publish()
            previous = self.article.content
            revision = self.article.revision
            result = kb.upload(self.article.name, "manual." + suffix, base64.b64encode(raw).decode())
            self.assertEqual(result["extraction_status"], "Extracted")
            self.article.reload()
            self.assertTrue(self.article.content.startswith(previous))
            self.assertIn(marker, self.article.content)
            self.assertEqual(self.article.status, "Draft")
            self.assertEqual(self.article.revision, revision + 1)
            self.assertFalse(self.article.approved_by)
            self.assertEqual(self.article.cloud_allowed, 1)
            self.assertEqual([r.user for r in self.article.allowed_users], [self.users["reader"]])
            self.as_user("reader")
            self.assertEqual(kb.search(marker)["articles"], [])
            with self.assertRaises(frappe.PermissionError): kb.download(result["name"])
            self.publish(); self.as_user("reader")
            self.assertEqual(kb.retrieve(marker)[0]["name"], self.article.name)
        frappe.set_user("Administrator")
        self.article.reload(); self.article.cloud_allowed = 0; self.article.save(); self.publish()
        self.as_user("reader")
        self.assertTrue(kb.search("ManualDocxMarker")["articles"])
        self.assertEqual(kb.retrieve("ManualDocxMarker"), [])

    def test_extraction_denies_unauthorized_before_parsing(self):
        self.as_user("outsider")
        with patch("warehouse_core.knowledge_extraction.extract") as parser:
            with self.assertRaises(frappe.PermissionError):
                kb.upload(self.article.name, "manual.pdf", base64.b64encode(b"%PDF-no").decode())
            parser.assert_not_called()

    def test_failed_extraction_keeps_file_and_withdraws_approval(self):
        self.publish(); previous = self.article.content
        result = kb.upload(self.article.name, "broken.pdf", base64.b64encode(b"%PDF-broken").decode())
        self.assertEqual(result["extraction_status"], "Failed")
        self.article.reload()
        self.assertEqual(self.article.content, previous)
        self.assertEqual(self.article.status, "Draft")
        self.assertFalse(self.article.approved_by)
        self.assertTrue(frappe.db.exists(kb.ATTACHMENT, result["name"]))

    def test_total_content_limit_does_not_truncate_or_replace(self):
        from warehouse_core.verify_knowledge_extraction import make_docx
        self.article.content = "x" * kb.MAX_CONTENT; self.article.save()
        result = kb.upload(self.article.name, "manual.docx", base64.b64encode(make_docx()).decode())
        self.assertEqual(result["extraction_status"], "Limit Exceeded")
        self.article.reload(); self.assertEqual(self.article.content, "x" * kb.MAX_CONTENT)
        self.assertEqual(result["extracted_characters"], 0)

    def test_empty_draft_upload_workflow_and_review_validation(self):
        from warehouse_core.verify_knowledge_extraction import make_docx
        self.article.content = ""; self.article.save()
        for status in ("Review", "Published"):
            self.article.status = status
            with self.assertRaises(frappe.ValidationError): self.article.save()
            self.article.reload()
        result = kb.upload(self.article.name, "manual.docx", base64.b64encode(make_docx()).decode())
        self.assertEqual(result["extraction_status"], "Extracted")
        self.article.reload(); self.assertTrue(self.article.content)
        self.assertEqual(self.article.cloud_allowed, 1)

    def test_grounded_answer_audit_and_no_payload_logging(self):
        self.publish(); self.as_user("reader")
        with patch.object(ai_chat, "generate_text", return_value="Зверніться до сервісу. [1]") as provider, patch.object(frappe.db, "commit"):
            result = ai_chat.knowledge_answer("Соняшник", "knowledge")
            self.assertTrue(result["ok"]); self.assertEqual(result["sources"][0]["name"], self.article.name)
            self.assertIn("untrusted reference data", provider.call_args.args[0])
        log = frappe.get_all("Knowledge Query Log", filters={"requested_by": frappe.session.user}, fields=["sources", "outcome", "query_hash"])[0]
        self.assertEqual(log.outcome, "answered")
        self.assertNotIn("Соняшник", json.dumps(dict(log), ensure_ascii=False))
        self.assertEqual(json.loads(log.sources)[0]["revision"], self.article.revision)

    def test_revocation_during_provider_call_hides_answer(self):
        self.publish(); self.as_user("reader")
        def revoke(*args, **kwargs):
            frappe.db.set_value(kb.ARTICLE, self.article.name, "cloud_allowed", 0)
            return "A revoked answer must not escape"
        with patch.object(ai_chat, "generate_text", side_effect=revoke), patch.object(frappe.db, "commit"):
            result = ai_chat.knowledge_answer("Соняшник", "knowledge")
            self.assertFalse(result["ok"]); self.assertNotIn("text", result)

    def test_unknown_queries_do_not_spend(self):
        self.publish(); self.as_user("reader")
        with patch.object(ai_chat, "generate_text") as provider:
            result = ai_chat.knowledge_answer("zzzznonexistenttoken", "knowledge")
            self.assertEqual(result["sources"], []); provider.assert_not_called()

    def test_followup_memory_respects_real_source_access_and_publication(self):
        self.publish(); self.as_user("reader")
        with patch.object(ai_chat, "generate_text", return_value="Зверніться до сервісу. [1]") as provider, patch.object(frappe.db, "commit"):
            first = ai_chat.knowledge_answer("Соняшник", "knowledge")
            memory = first["_memory"]
            followup = ai_chat.knowledge_answer("Коротше", "knowledge", memory)
            self.assertTrue(followup["ok"])
            self.assertEqual(followup["sources"][0]["name"], self.article.name)
            self.assertIn("Соняшник", provider.call_args.args[0])
            calls = provider.call_count
            for field, value in (("status", "Draft"), ("cloud_allowed", 0),
                                 ("valid_until", add_days(nowdate(), -1)), ("modified", "2030-01-01 00:00:00"),
                                 ("allowed_users", None)):
                with self.subTest(field=field):
                    frappe.db.savepoint("memory_revocation")
                    try:
                        if field == "allowed_users":
                            frappe.db.delete("Knowledge Access User", {"parent": self.article.name})
                        else:
                            frappe.db.set_value(kb.ARTICLE, self.article.name, field, value, update_modified=False)
                        result = ai_chat.knowledge_answer("Повтори", "knowledge", memory)
                        self.assertTrue(result["reset_conversation"])
                        self.assertNotIn("text", result)
                        self.assertEqual(provider.call_count, calls)
                    finally:
                        frappe.db.rollback(save_point="memory_revocation")


def run():
    original = frappe.session.user
    before = sorted((r.parent, r.role) for r in frappe.get_all("Has Role", fields=["parent", "role"]))
    frappe.db.savepoint("knowledge_security")
    stream = io.StringIO()
    try:
        result = unittest.TextTestRunner(stream=stream).run(unittest.defaultTestLoader.loadTestsFromTestCase(KnowledgeTests))
    finally:
        frappe.set_user("Administrator")
        frappe.db.rollback(save_point="knowledge_security")
        frappe.local.response.pop("filecontent", None); frappe.local.response.pop("type", None)
        frappe.set_user(original)
    assert before == sorted((r.parent, r.role) for r in frappe.get_all("Has Role", fields=["parent", "role"])), "Role assignments changed"
    if not result.wasSuccessful():
        raise AssertionError(stream.getvalue())
    return {"passed": result.testsRun, "paid_calls": 0, "test_records": "rolled back", "role_assignments": "unchanged"}


def live():
    """One explicitly invoked synthetic smoke test through the existing spending guard."""
    if not frappe.conf.get("warehouse_demo"):
        frappe.throw("Дозволено лише в локальному демонстраційному середовищі.")
    original = frappe.session.user
    frappe.set_user("Administrator")
    doc = None
    try:
        if not ai_chat.status()["available"]:
            return {"tested": False, "reason": "spending_guard_blocked", "paid_calls": 0}
        marker = "KBTEST" + uuid.uuid4().hex[:8]
        doc = frappe.get_doc({"doctype": kb.ARTICLE, "title": "Тимчасова перевірка " + marker,
            "content": f"Це синтетична тестова стаття {marker}. Код перевірки: Соняшник.",
            "category": "Інструкції", "status": "Published", "cloud_allowed": 1,
            "allowed_users": [{"user": "Administrator"}]}).insert()
        frappe.db.commit()
        result = ai_chat.knowledge_answer(f"Який код перевірки у статті {marker}?", "knowledge")
        assert result["ok"], result.get("message")
        assert "соняшник" in result["text"].casefold(), "Answer did not use the test fact"
        assert any(s["name"] == doc.name for s in result["sources"]), "Missing source"
        assert "[1]" in result["text"], "Missing inline citation"
        return {"tested": True, "answer": result["text"], "sources": len(result["sources"]), "paid_calls": 1,
                "fixture": "removed after test"}
    finally:
        if doc and doc.name:
            for table in ("Knowledge Access User", "Knowledge Access Role"):
                frappe.db.delete(table, {"parent": doc.name, "parenttype": kb.ARTICLE})
            frappe.db.delete("Comment", {"reference_doctype": kb.ARTICLE, "reference_name": doc.name})
            frappe.db.delete(kb.ARTICLE, {"name": doc.name})
            frappe.db.commit()
        frappe.set_user(original)
