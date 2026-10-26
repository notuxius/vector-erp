"""Explicit local-site migration of the requested sales directory names."""
import frappe

from warehouse_core.record_names import COMPANY, snapshot


RENAMES = (
    ("Sales Person", "Sales Team", "Відділ продажів"),
    ("Sales Person", "DEMO Service Person", "Сервісний спеціаліст"),
    ("Sales Person", "Service Person", "Сервісний спеціаліст"),
    ("Territory", "All Territories", "Усі території"),
    ("Territory", "Ukraine", "Україна"),
    ("Territory", "Rest Of The World", "Решта світу"),
)
TREES = {
    "Sales Person": ("sales_person_name", "parent_sales_person"),
    "Territory": ("territory_name", "parent_territory"),
}


def _state(changes=()):
    mapping = {(dt, old): new for dt, old, new in changes}

    def mapped(dt, value):
        return mapping.get((dt, value), value)

    state = snapshot(changes)
    for dt, (_, parent) in TREES.items():
        state[dt + ":tree"] = sorted(
            (mapped(dt, r.name), mapped(dt, r[parent]), r.is_group, r.lft, r.rgt)
            for r in frappe.get_all(dt, fields=["name", parent, "is_group", "lft", "rgt"])
        )
    # Check every populated native Link to either directory, including service
    # visits, customers, transactions, defaults and user permissions.
    for dt in frappe.get_all("DocType", filters={"is_virtual": 0}, pluck="name"):
        meta = frappe.get_meta(dt)
        fields = [f for f in meta.fields if f.fieldtype == "Link" and f.options in TREES]
        if not fields:
            continue
        if meta.issingle:
            for f in fields:
                state[(dt, f.fieldname)] = mapped(f.options, frappe.db.get_single_value(dt, f.fieldname))
        elif frappe.db.table_exists(dt):
            for f in fields:
                state[(dt, f.fieldname)] = sorted(
                    (mapped(dt, r.name), mapped(f.options, r[f.fieldname]))
                    for r in frappe.get_all(dt, fields=["name", f.fieldname])
                )
    return state


def apply(dry_run=True):
    frappe.only_for("System Manager")
    if not frappe.conf.get("warehouse_demo") or not frappe.db.exists("Company", COMPANY):
        frappe.throw("This explicit migration requires the configured local sample site")
    changes = [r for r in RENAMES if frappe.db.exists(r[0], r[1])]
    targets = set()
    for dt, old, new in changes:
        if frappe.db.exists(dt, new) or (dt, new) in targets:
            frappe.throw(f"{dt} {new} already exists; refusing to merge records")
        targets.add((dt, new))
    if dry_run:
        return {"renames": changes}
    before = _state(changes)
    frappe.db.savepoint("sales_directory_names")
    try:
        for dt, old, new in changes:
            actual = frappe.rename_doc(dt, old, new, force=True, merge=False,
                                       show_alert=False, rebuild_search=False)
            if actual != new:
                raise AssertionError(f"Unexpected rename result: {actual}")
            frappe.db.set_value(dt, new, TREES[dt][0], new)
            frappe.clear_document_cache(dt, new)
        if _state() != before:
            raise AssertionError("Directory migration changed links, trees, balances or roles")
    except Exception:
        frappe.db.rollback(save_point="sales_directory_names")
        raise
    frappe.clear_cache()
    for dt in {r[0] for r in changes}:
        frappe.enqueue("frappe.utils.global_search.rebuild_for_doctype", doctype=dt,
                       enqueue_after_commit=True)
    return {"renamed": changes, "links_trees_balances_roles": "preserved"}
