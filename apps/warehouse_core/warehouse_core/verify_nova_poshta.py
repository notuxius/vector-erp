"""Rollback-only carrier integration checks. All HTTP is mocked; no real TTNs."""
import json
import uuid
from unittest.mock import Mock, patch

import frappe
import requests
from frappe.utils import nowdate

from warehouse_core import nova_poshta as np
from warehouse_core.demo import COMPANY, SECONDARY

REF = "11111111-1111-1111-1111-111111111111"
NUMBER = "20400000000001"


def run():
    if not frappe.conf.get("warehouse_demo"):
        frappe.throw("Run only on the explicitly configured demo site")
    original_user = frappe.session.user
    frappe.set_user("Administrator")
    checks = []
    # Even explicit production commits are suppressed here so fixtures roll back.
    with patch.object(frappe.db, "commit") as commit, patch.object(np.requests, "post") as post:
        try:
            delivery = frappe.get_doc("Delivery Note", "DISPATCH-DN-001")
            if frappe.db.exists("Nova Poshta Shipment", {"delivery_note": delivery.name}):
                frappe.throw("Demo delivery already has a shipment; use a clean demo fixture")
            delivery.db_set("custom_nova_poshta_ttn", "")
            settings = frappe.get_doc("Nova Poshta Settings", COMPANY) if frappe.db.exists("Nova Poshta Settings", COMPANY) else frappe.new_doc("Nova Poshta Settings")
            settings.update({"company": COMPANY, "enabled": 1, "api_key": "TEST-NOT-A-REAL-KEY",
                "sender_ref": REF, "sender_contact": REF, "sender_city": REF,
                "sender_warehouse": REF, "sender_phone": "+380501234567"})
            settings.save()
            shipment = frappe.get_doc({"doctype": "Nova Poshta Shipment", "delivery_note": delivery.name,
                "shipment_date": nowdate(), "first_name": "Тест", "last_name": "Перевірка",
                "recipient_phone": "0501234567", "recipient_city": REF, "recipient_warehouse": REF,
                "weight": 2, "seats": 1, "declared_value": 1000, "description": "Тестова посилка"}).insert()

            def response(data=None, **extra):
                return Mock(status_code=200, json=lambda: {"success": True, "data": data or [], **extra})

            def rejected(call, error=frappe.ValidationError):
                try:
                    call()
                except error:
                    return
                raise AssertionError("Invalid operation accepted")

            # Invalid inputs fail before the provider and before durable intent.
            for value in (0, -1, "nan", "inf", "nonsense"):
                rejected(lambda: np.positive(value, "Вага"))
            rejected(lambda: np.positive(1.5, "Місця", True))
            rejected(lambda: np.phone("+48123456789"))
            assert np.phone("(050) 123-45-67") == "380501234567"
            checks.append("weight, seat count, phone validation")

            frappe.set_user("Guest")
            rejected(lambda: np.create_waybill(shipment.name), frappe.PermissionError)
            rejected(lambda: np.lookup("senders", company=COMPANY), frappe.PermissionError)
            frappe.set_user("Administrator")
            assert post.call_count == 0
            checks.append("unauthorized requests rejected before network")

            user = frappe.get_doc({"doctype": "User", "email": "verify-nova-" + uuid.uuid4().hex[:10] + "@example.test",
                "first_name": "Verification", "enabled": 1, "send_welcome_email": 0,
                "user_type": "System User", "roles": [{"role": "Stock User"}]}).insert()
            frappe.set_user(user.name)
            assert frappe.has_permission(shipment.doctype, "read", doc=shipment.name)
            assert not frappe.has_permission("Nova Poshta Settings", "read", doc=COMPANY)
            frappe.set_user("Administrator")
            frappe.get_doc({"doctype": "User Permission", "user": user.name, "allow": "Warehouse",
                "for_value": SECONDARY, "apply_to_all_doctypes": 1}).insert()
            frappe.clear_cache(user=user.name)
            frappe.set_user(user.name)
            assert not frappe.has_permission(shipment.doctype, "read", doc=shipment.name)
            assert not frappe.get_list(shipment.doctype, filters={"name": shipment.name})
            rejected(lambda: np.create_waybill(shipment.name), frappe.PermissionError)
            frappe.set_user("Administrator")
            checks.append("warehouse restrictions cover shipment reads, lists and creation; API settings private")

            # First save succeeds externally, but the waybill response times out.
            post.side_effect = [response([{"Ref": REF, "ContactPerson": {"data": [{"Ref": REF}]}}]), requests.Timeout()]
            result = np.create_waybill(shipment.name)
            assert result["state"] == "Uncertain" and commit.call_count >= 2
            assert frappe.db.get_value(shipment.doctype, shipment.name, "state") == "Uncertain"
            calls = post.call_count
            rejected(lambda: np.create_waybill(shipment.name))
            assert post.call_count == calls
            shipment.reload()
            shipment.weight = 3
            rejected(shipment.save)
            delivery.reload()
            delivery.custom_nova_poshta_ttn = "20400000000002"
            rejected(delivery.save)
            checks.append("timeout persists intent, blocks retry and edits")

            # Simulate an administrator reconciliation after provider verification.
            frappe.db.set_value(shipment.doctype, shipment.name, "requested_at", "2020-01-01 00:00:00")
            post.side_effect = None
            post.return_value = response([{"IntDocNumber": NUMBER, "Ref": REF, "InfoRegClientBarcodes": "WRONG"}])
            rejected(lambda: np.reconcile(shipment.name, NUMBER))
            post.return_value = response([{"IntDocNumber": NUMBER, "Ref": REF, "InfoRegClientBarcodes": shipment.name}])
            assert np.reconcile(shipment.name, NUMBER)["state"] == "Created"
            assert frappe.db.get_value("Delivery Note", delivery.name, "custom_nova_poshta_ttn") == NUMBER
            calls = post.call_count
            assert np.create_waybill(shipment.name)["waybill"] == NUMBER
            assert post.call_count == calls
            checks.append("reconciliation verifies account reference; repeat is idempotent")

            post.return_value = response([{"Number": "20400000000002", "Status": "Чужий статус"}])
            rejected(lambda: np.refresh_tracking(delivery.name))
            post.return_value = response([{"Number": NUMBER, "Status": "Прибув у відділення", "StatusCode": 7}])
            np.refresh_tracking(delivery.name)
            assert frappe.db.get_value("Delivery Note", delivery.name, "custom_nova_poshta_status_code") == "7"
            checks.append("tracking matches exact waybill and stores status")

            # Normal success path, with a fresh draft state solely inside this fixture.
            frappe.db.set_value(shipment.doctype, shipment.name, {"state": "Draft", "waybill": "", "carrier_ref": ""})
            frappe.db.set_value("Delivery Note", delivery.name, "custom_nova_poshta_ttn", "")
            post.side_effect = [response([{"Ref": REF, "ContactPerson": {"data": [{"Ref": REF}]}}]),
                                response([{"Ref": REF, "IntDocNumber": NUMBER, "CostOnSite": 90}])]
            assert np.create_waybill(shipment.name)["waybill"] == NUMBER
            request = post.call_args.kwargs
            assert request["json"]["methodProperties"]["ServiceType"] == "WarehouseWarehouse"
            assert request["json"]["methodProperties"]["Cost"] == 1000
            assert request["allow_redirects"] is False and request["timeout"] == (5, 30)
            checks.append("creation saves carrier result and links native Delivery Note")

            post.side_effect = None
            post.return_value = response(success=False, errors=["secret-key private-person"])
            try:
                np.api(settings, "Address", "getCities", {})
            except np.CarrierError as exc:
                assert "secret-key" not in str(exc) and "private-person" not in str(exc)
            else:
                raise AssertionError("Provider error accepted")
            checks.append("carrier errors do not leak credentials or personal data")
        finally:
            frappe.db.rollback()
            frappe.set_user(original_user)
    result = {"ok": True, "checks": checks, "real_carrier_requests": 0, "fixtures_rolled_back": True}
    print(json.dumps(result, ensure_ascii=False))
    return result
