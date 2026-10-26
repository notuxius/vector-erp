"""Example users and explicit, repeatable replacement from an approved roster.

No notifications are sent. Old identities are disabled, never renamed, so audit
history cannot be attributed to a real person who did not perform those actions.
This module is an administrator CLI facility, not a whitelisted web endpoint.
"""
import json
from pathlib import Path

import frappe
from frappe.utils import validate_email_address

from warehouse_core.install import ROLE_PROFILES

EXAMPLES = {
    1: ["Warehouse Operations Manager", "Warehouse Accounting"],
    2: ["Warehouse Sales"],
    3: ["Warehouse Purchasing"],
    4: ["Warehouse Staff", "Warehouse Manufacturing"],
    5: ["Warehouse Service"],
}


def example_email(slot):
    return f"worker-{slot}@example.test"


def seed_examples():
    if not frappe.conf.get("warehouse_demo"):
        frappe.throw("Example staff require the explicitly configured demo site")
    for slot, profiles in EXAMPLES.items():
        email = example_email(slot)
        if not frappe.db.exists("User", email):
            roles = sorted({role for profile in profiles for role in ROLE_PROFILES[profile]})
            frappe.get_doc({"doctype": "User", "email": email, "first_name": f"Працівник {slot}",
                "last_name": "(приклад)", "language": "uk", "enabled": 0, "send_welcome_email": 0,
                "user_type": "System User", "custom_demo_slot": slot,
                "roles": [{"role": role} for role in roles]}).insert()


def import_roster(path, dry_run=True):
    return apply_roster(json.loads(Path(path).read_text(encoding="utf-8-sig")), dry_run=dry_run)


def apply_roster(rows, dry_run=True):
    frappe.only_for("System Manager")
    if not isinstance(dry_run, bool) or not isinstance(rows, list) or not rows:
        frappe.throw("Provide a non-empty roster list and a boolean dry_run")
    seen_slots, seen_emails, plan = set(), set(), []
    for row in rows:
        slot = row.get("slot")
        email = (row.get("email") or "").strip().lower()
        profiles = row.get("profiles") or []
        if type(slot) is not int or slot not in EXAMPLES or slot in seen_slots:
            frappe.throw("Each example slot 1–5 must appear at most once")
        if not email or email in seen_emails or email.endswith("@example.test"):
            frappe.throw("Provide a unique real work email for every replacement")
        if validate_email_address(email, throw=True) != email:
            frappe.throw("Provide exactly one valid email address")
        if not row.get("first_name") or not isinstance(profiles, list) or not profiles or any(p not in ROLE_PROFILES for p in profiles):
            frappe.throw("Provide the real name and explicit Warehouse role profiles")
        if "enabled" in row and type(row["enabled"]) is not bool:
            frappe.throw("enabled must be a boolean")
        old = frappe.get_doc("User", example_email(slot))
        if old.custom_demo_slot != slot or (old.custom_replaced_by and old.custom_replaced_by != email):
            frappe.throw("Example account is not available for this mapping")
        exists = bool(frappe.db.exists("User", email))
        if exists and old.custom_replaced_by != email:
            frappe.throw("Target user already exists; review its permissions before mapping it")
        plan.append({"slot": slot, "example_user": old.name, "email": email,
                     "first_name": row["first_name"], "last_name": row.get("last_name") or "",
                     "profiles": profiles, "enabled": row.get("enabled", False), "already_replaced": exists})
        seen_slots.add(slot)
        seen_emails.add(email)
    if dry_run:
        return {"dry_run": True, "users": plan}
    for entry in plan:
        if entry["already_replaced"]:
            continue
        roles = sorted({role for profile in entry["profiles"] for role in ROLE_PROFILES[profile]})
        frappe.get_doc({"doctype": "User", "email": entry["email"], "first_name": entry["first_name"],
            "last_name": entry["last_name"], "enabled": int(entry["enabled"]), "language": "uk",
            "send_welcome_email": 0, "user_type": "System User",
            "roles": [{"role": role} for role in roles]}).insert()
        old = frappe.get_doc("User", entry["example_user"])
        old.enabled = 0
        old.custom_replaced_by = entry["email"]
        old.save()
    return {"dry_run": False, "users": plan, "notifications_sent": False}
