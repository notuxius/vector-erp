"""Explicit demo-only publication of public catalog and training material."""
import json
from pathlib import Path

import frappe

from frappe.utils import nowdate

from warehouse_core.company_identity import COMPANY, MAIN, SECONDARY
from warehouse_core.production_demo import authorize

CONTENT = Path(__file__).with_name("assistant_content")
READERS = ("AI Assistant Tester", "Telegram ERP Reader")


def create_practice_requests():
    drafts = [
        ("TRAIN-AI-TRANSFER-DM542", "Навчальне переміщення двох DM542", "Material Transfer", "DM542", 2, SECONDARY),
        ("TRAIN-AI-PURCHASE-SBR16UU", "Навчальна закупівля одного SBR16UU", "Purchase", "SBR16UU", 1, MAIN),
    ]
    created = []
    for name, title, purpose, code, qty, target in drafts:
        if frappe.db.exists("Material Request", name):
            continue
        row = {"item_code": code, "qty": qty, "warehouse": target, "schedule_date": nowdate()}
        if purpose == "Material Transfer":
            row["from_warehouse"] = MAIN
        frappe.get_doc({"doctype": "Material Request", "company": COMPANY, "title": title,
                        "material_request_type": purpose, "schedule_date": nowdate(),
                        "items": [row]}).insert(set_name=name)
        created.append(name)
    return created


def seed():
    authorize()
    # Serialize seed runs. Existing articles (including withdrawn approvals) belong to the publisher.
    frappe.db.get_value("Company", COMPANY, "name", for_update=True)
    for role in READERS:
        if not frappe.db.exists("Role", role):
            frappe.throw(f"Для навчальної бази знань спочатку налаштуйте роль {role}.")
    created = kept = 0
    for filename in ("catalog.json", "examples.json"):
        for entry in json.loads((CONTENT / filename).read_text("utf-8")):
            name = "KB-MTECH-" + entry["id"]
            if frappe.db.exists("Knowledge Article", name):
                kept += 1
                continue
            article = {field: entry[field] for field in ("category", "title", "content")}
            frappe.get_doc({"doctype": "Knowledge Article", **article, "status": "Published",
                            "cloud_allowed": 1, "allowed_roles": [{"role": role} for role in READERS]}
                           ).insert(set_name=name)
            created += 1
    return {"created": created, "preserved": kept, "new_drafts": create_practice_requests()}
