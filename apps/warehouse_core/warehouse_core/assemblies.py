"""Permission-aware serial form view. Installation records remain the source of truth."""
import frappe


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def serial_query(doctype, txt, searchfield, start, page_len, filters):
    filters = frappe._dict(filters or {})
    if filters.kind not in ("machine", "assembly") or not filters.company:
        return []
    flag = "custom_is_machine" if filters.kind == "machine" else "custom_is_serialized_assembly"
    items = frappe.get_list("Item", filters={flag: 1}, pluck="name", limit_page_length=0)
    if not items:
        return []
    return frappe.get_list("Serial No", filters={"company": filters.company, "item_code": ["in", items]},
        or_filters={"name": ["like", f"%{txt}%"], "item_code": ["like", f"%{txt}%"],
                    "custom_manufacturer_serial_no": ["like", f"%{txt}%"]},
        fields=["name", "item_code", "custom_manufacturer_serial_no"],
        order_by="name", limit_start=start, limit_page_length=page_len, as_list=True)


@frappe.whitelist()
def get_assemblies(serial_no):
    serial = frappe.get_doc("Serial No", serial_no)
    serial.check_permission("read")
    frappe.has_permission("Machine Assembly Installation", "read", throw=True)
    is_machine = bool(frappe.db.get_value("Item", serial.item_code, "custom_is_machine"))
    field = "machine_serial" if is_machine else "assembly_serial"
    rows = frappe.get_list("Machine Assembly Installation",
        filters={field: serial_no, "docstatus": 1, "removed_at": ["is", "not set"]},
        fields=["name", "machine_serial", "assembly_serial", "installed_at", "position"],
        order_by="installed_at desc", limit_page_length=0)
    return {"is_machine": is_machine, "filter_field": field, "rows": rows}
