"""Availability checks: mocked AI plus real ERP transactions, always rolled back."""
import io
import json
import unittest
import uuid
from unittest.mock import patch

import frappe
from frappe.utils import nowdate

from warehouse_core import availability as stock
from warehouse_core.ai import AIUnavailable


class BoundaryTests(unittest.TestCase):
    def test_strict_filters(self):
        for value in ({"sql": "select *"}, {"stock_state": "anything"}, {"query": []},
                      {"query": "x" * 141}, {"query": "a\nb"}, [], "{", "x" * 2001):
            with self.assertRaises((ValueError, TypeError)):
                stock.normalize(value)

    def test_no_double_subtraction_or_negative_free_stock(self):
        values = frappe._dict(actual_qty=10, reserved_stock=3, reserved_qty=3,
            reserved_qty_for_production=6, reserved_qty_for_production_plan=2,
            reserved_qty_for_sub_contract=1, ordered_qty=20)
        item = frappe._dict(name="part", item_name="Part", stock_uom="Nos", item_group="Products")
        warehouse = frappe._dict(name="Stock", company="Company")
        row = stock.stock_row(values, item, warehouse)
        self.assertEqual(row["free_qty"], 7)
        self.assertEqual(row["production_demand"], 6)
        self.assertEqual(row["ordered_qty"], 20)
        values.actual_qty = -2
        self.assertEqual(stock.stock_row(values, item, warehouse)["free_qty"], 0)
        values.actual_qty, values.reserved_stock = 1.125, .125
        self.assertEqual(stock.stock_row(values, item, warehouse)["free_qty"], 1)

    def test_planner_sends_only_question_and_filters(self):
        plan = {"action": "search", **stock.normalize({"query": "шпиндель"})}
        with patch.object(stock, "require_access"), patch.object(stock, "scopes"), \
                patch.object(stock, "generate_text", return_value=json.dumps(plan)) as provider:
            self.assertEqual(stock.interpret("Чи є шпиндель?")["query"], "шпиндель")
            payload = json.loads(provider.call_args.args[0].split("\n", 1)[1])
            self.assertEqual(set(payload), {"question", "previous_filters"})
            self.assertEqual(provider.call_args.kwargs, {"max_output_tokens": 512})

    def test_invalid_and_unsupported_plans_never_read_stock(self):
        with patch.object(stock, "require_access"), patch.object(stock, "scopes"), \
                patch.object(stock, "search") as search, patch.object(stock, "generate_text") as provider:
            for raw in ('{"action":"delete"}', '{"action":"search","sql":"anything"}',
                        '[]', '```json\n{}\n```', '{"action":"unsupported"}', 'not json'):
                provider.return_value = raw
                result = stock.answer("Зміни всі залишки")
                self.assertFalse(result["ok"])
                self.assertEqual(result.get("incident"), None if raw == '{"action":"unsupported"}' else "stock_plan")
            search.assert_not_called()

    def test_gateway_blocked_no_retry(self):
        with patch.object(stock, "require_access"), patch.object(stock, "scopes"), \
                patch.object(stock, "search") as search, patch.object(stock, "generate_text", side_effect=AIUnavailable()) as provider:
            self.assertFalse(stock.answer("Які шпинделі можна знайти?")["ok"])
            provider.assert_called_once()
            search.assert_not_called()

    def test_denied_before_model_and_revoked_after_model(self):
        with patch.object(stock, "require_access", side_effect=frappe.PermissionError), \
                patch.object(stock, "generate_text") as provider:
            self.assertTrue(stock.answer("Покажи шпинделі")["reset_conversation"])
            provider.assert_not_called()
        with patch.object(stock, "interpret", return_value=stock.normalize({})), \
                patch.object(stock, "scopes", side_effect=frappe.PermissionError):
            result = stock.answer("Покажи шпинделі")
            self.assertTrue(result["reset_conversation"])
            self.assertNotIn("availability", result)

    def test_direct_filters_do_not_use_ai(self):
        result = {"rows": [], "filters": stock.normalize({}), "next_after": None, "refine_search": False}
        with patch.object(stock, "search", return_value=result), patch.object(stock, "generate_text") as provider:
            self.assertTrue(stock.answer("Пошук", filters={})["ok"])
            provider.assert_not_called()

    def test_revoked_group_context_is_not_sent_to_model(self):
        with patch.object(stock, "require_access"), patch.object(stock, "scopes"), \
                patch.object(frappe, "has_permission", return_value=False), patch.object(stock, "generate_text") as provider:
            result = stock.answer("А на іншому складі?", {"filters": stock.normalize({"item_group": "Revoked group"})})
            self.assertTrue(result["reset_conversation"])
            provider.assert_not_called()

    def test_no_client_cursor_without_server_memory(self):
        with patch.object(stock, "search") as search, patch.object(stock, "generate_text") as provider:
            self.assertFalse(stock.answer("Ще", next_page=True)["ok"])
            self.assertFalse(stock.answer("Ще", {"next_after": ["x", "y"]}, filters={}, next_page=True)["ok"])
            search.assert_not_called()
            provider.assert_not_called()


def transaction_checks():
    from warehouse_core.demo import COMPANY, MAIN, SECONDARY, CUSTOMER
    from erpnext.manufacturing.doctype.work_order.work_order import make_stock_entry
    if not frappe.conf.get("warehouse_demo") or not frappe.db.exists("Company", COMPANY):
        raise RuntimeError("Run transaction checks only on the configured demo site")
    prefix = "VERIFY-AVAIL-" + uuid.uuid4().hex[:8]
    original_user, original_test = frappe.session.user, frappe.flags.in_test
    frappe.set_user("Administrator")
    frappe.flags.in_test = True
    baseline_roles = frappe.db.count("Has Role")
    checks = []

    def submit(doc):
        doc.insert()
        doc.submit()
        return doc

    def item(suffix, **extra):
        return frappe.get_doc({"doctype": "Item", "item_code": prefix + suffix,
            "item_group": "Products", "stock_uom": "Nos", "is_stock_item": 1, **extra}).insert()

    def find(code, **extra):
        return stock.search({"query": code, "company": COMPANY, "stock_state": "all", **extra})["rows"]

    try:
        part, assembly, empty = item("-PART"), item("-ASSEMBLY"), item("-EMPTY")
        submit(frappe.get_doc({"doctype": "Stock Entry", "stock_entry_type": "Material Receipt", "company": COMPANY,
            "items": [{"item_code": part.name, "qty": 10, "t_warehouse": MAIN, "basic_rate": 10}]}))
        assert find(part.name)[0]["actual_qty"] == 10
        assert find(part.name)[0]["free_qty"] == 10
        assert not find(empty.name), "Missing Bin must not become a fabricated zero"
        assert "stock_value" not in find(part.name)[0]
        checks.append("Live receipt, stock UOM, absent Bin, no financial fields")

        frappe.db.set_single_value("Stock Settings", "enable_stock_reservation", 1)
        order = submit(frappe.get_doc({"doctype": "Sales Order", "customer": CUSTOMER, "company": COMPANY,
            "transaction_date": nowdate(), "delivery_date": nowdate(), "selling_price_list": "Standard Selling",
            "items": [{"item_code": part.name, "qty": 3, "rate": 100, "warehouse": MAIN, "reserve_stock": 1}]}))
        order.create_stock_reservation_entries(items_details=[{"sales_order_item": order.items[0].name,
            "warehouse": MAIN, "qty_to_reserve": 3}], notify=False)
        row = find(part.name)[0]
        assert row["reserved_stock"] == 3 and row["free_qty"] == 7 and row["sales_demand"] == 3, row
        checks.append("Native physical reservation separate from sales demand")

        bom = submit(frappe.get_doc({"doctype": "BOM", "company": COMPANY, "item": assembly.name,
            "quantity": 1, "currency": "UAH", "items": [{"item_code": part.name, "qty": 2, "rate": 10}]}))
        work = frappe.get_doc({"doctype": "Work Order", "company": COMPANY, "production_item": assembly.name,
            "bom_no": bom.name, "qty": 3, "source_warehouse": MAIN, "wip_warehouse": SECONDARY,
            "fg_warehouse": MAIN, "skip_transfer": 1, "from_wip_warehouse": 0})
        work.get_items_and_operations_from_bom()
        submit(work)
        row = find(part.name, stock_state="production")[0]
        assert row["production_demand"] == 6 and row["free_qty"] == 7, row
        submit(frappe.get_doc(make_stock_entry(work.name, "Manufacture", 1)))
        row = find(part.name)[0]
        assert row["actual_qty"] == 8 and row["production_demand"] == 4 and row["free_qty"] == 5, row
        for name in frappe.get_all("Stock Reservation Entry", filters={"voucher_no": order.name, "docstatus": 1}, pluck="name"):
            frappe.get_doc("Stock Reservation Entry", name).cancel()
        assert find(part.name)[0]["free_qty"] == 8
        checks.append("Work-order component demand, real manufacturing consumption and reservation cancellation refresh")

        pages = [item(f"-PAGE-{i:02}") for i in range(23)]
        submit(frappe.get_doc({"doctype": "Stock Entry", "stock_entry_type": "Material Receipt", "company": COMPANY,
            "items": [{"item_code": p.name, "qty": 1, "t_warehouse": MAIN, "basic_rate": 1} for p in pages]}))
        filters = {"query": prefix + "-PAGE", "company": COMPANY}
        first = stock.search(filters)
        second = stock.search(filters, first["next_after"])
        assert len(first["rows"]) == 20 and first["has_more"]
        assert len(second["rows"]) == 3 and not second["has_more"]
        assert len({r["item_code"] for r in first["rows"] + second["rows"]}) == 23
        assert not find(prefix + "%"), "Wildcards must be literal"
        assert not find("' OR 1=1 --"), "SQL text must remain a search value"
        checks.append("Stable pagination, literal wildcards, bound query values")

        user = frappe.get_doc({"doctype": "User", "email": prefix.lower() + "@example.test", "first_name": "Availability test",
            "enabled": 1, "send_welcome_email": 0, "user_type": "System User", "roles": [{"role": "Stock User"}]}).insert()
        for doctype, name in (("Warehouse", MAIN), ("Item", part.name), ("Company", COMPANY)):
            frappe.get_doc({"doctype": "User Permission", "user": user.name, "allow": doctype,
                "for_value": name, "apply_to_all_doctypes": 1}).insert()
        frappe.clear_cache(user=user.name)
        frappe.set_user(user.name)
        assert find(part.name)[0]["actual_qty"] == 8
        assert not find(pages[0].name), "Item User Permission leaked"
        assert all(w["name"] == MAIN for w in stock.options()["warehouses"])
        denied = stock.answer("Пошук", filters={"warehouse": SECONDARY})
        assert not denied["ok"] and "availability" not in denied
        checks.append("Real Stock User: company/item/warehouse User Permissions and scoped selectors")

        original_readable = stock.readable
        with patch.object(stock, "readable", side_effect=lambda dt, r: False if dt == "Bin" else original_readable(dt, r)):
            assert not find(part.name), "Document permission hook bypassed"
        meta = frappe.get_meta("Bin")
        with patch.object(meta, "get_permitted_fieldnames", return_value=[f for f in stock.BIN_FIELDS if f != "reserved_stock"]):
            assert not stock.answer("Пошук", filters={})["ok"], "Field restriction bypassed"
        frappe.set_user("Guest")
        assert not stock.answer("Пошук", filters={})["ok"]
        checks.append("Document hooks, field permission denial, guest denial")
    finally:
        frappe.set_user(original_user)
        frappe.db.rollback()
        frappe.flags.in_test = original_test
        frappe.clear_cache()
    assert not frappe.db.exists("Item", prefix + "-PART")
    assert frappe.db.count("Has Role") == baseline_roles
    return checks


def run():
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream).run(unittest.defaultTestLoader.loadTestsFromTestCase(BoundaryTests))
    if not result.wasSuccessful():
        raise AssertionError(stream.getvalue())
    checks = transaction_checks()
    return {"unit_tests": result.testsRun, "transaction_checks": checks,
            "paid_calls": 0, "fixtures": "rolled back", "role_assignments": "unchanged"}
