"""Supply the signed-in user's missing name component for display formatting."""


def boot_session(bootinfo):
    import frappe

    user = bootinfo.get("user")
    if user and user.get("name") == frappe.session.user:
        user["middle_name"] = frappe.db.get_value("User", frappe.session.user, "middle_name") or ""
