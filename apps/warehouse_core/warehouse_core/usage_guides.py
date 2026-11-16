"""Publish the reviewed Vector ERP usage guides on explicit publisher request."""
import json
from pathlib import Path

import frappe

from warehouse_core.knowledge import publisher


def publish():
    if not publisher():
        frappe.throw("Потрібні права видавця бази знань.", frappe.PermissionError)
    entries = json.loads((Path(__file__).with_name("assistant_content") / "erp_guides.json").read_text("utf-8"))
    # Serialize imports and preserve every editorial decision on an existing article.
    frappe.db.get_value("DocType", "Knowledge Article", "name", for_update=True)
    created = []
    for entry in entries:
        name = "KB-VECTOR-USE-" + entry["id"]
        if frappe.db.exists("Knowledge Article", name):
            continue
        roles = list(entry["roles"])
        if frappe.db.exists("Role", "Telegram ERP Reader"):
            roles.append("Telegram ERP Reader")
        content = entry["content"] + "\n\nДжерело: " + entry["source"] + ". Версія ERPNext: 16.50.0."
        frappe.get_doc({"doctype": "Knowledge Article", "title": entry["title"], "content": content,
                        "category": "Інструкції", "status": "Published", "cloud_allowed": 1,
                        "allowed_roles": [{"role": role} for role in roles]}).insert(set_name=name)
        created.append(name)
    return created
