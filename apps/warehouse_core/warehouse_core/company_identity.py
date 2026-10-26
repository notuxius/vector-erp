"""Explicit local company rename; never run automatically during installation.

Keep the MTech brand/abbreviation. Use native renames, refuse collisions, and
compare record identities, native links, financial rows and access settings.
"""
import frappe

COMPANY = "MTech"
ABBREVIATION = "MTech"
MAIN = "Основний склад - MTech"
SECONDARY = "Додатковий склад - MTech"
PRODUCTION = "Виробничий склад - MTech"
LEGACY_COMPANIES = ("ТОВ «ЕМ ТЕК»", "MTech — Склад і торгівля", "DEMO — Склад і торгівля")
WAREHOUSES = (
    ("MTech Main - MTech", MAIN, "Основний склад"),
    ("MTech Secondary - MTech", SECONDARY, "Додатковий склад"),
    ("MTech Production - MTech", PRODUCTION, "Виробничий склад"),
)
TARGET_TYPES = {"Company", "Warehouse", "Cost Center"}
ACCESS_TYPES = {"User", "Role", "Role Profile", "Has Role", "User Permission",
                "DocShare", "DocPerm", "Custom DocPerm"}
# Native rename appends audit comments; their historical contents stay intact.
AUDIT_TYPES = {"Comment", "Version", "Activity Log", "Access Log", "Error Log",
              "Notification Log", "Route History", "Scheduled Job Log", "RQ Job",
              "Deleted Document"}
DEFAULT_TYPES = {"company": "Company", "default_company": "Company",
                 "warehouse": "Warehouse", "default_warehouse": "Warehouse",
                 "cost_center": "Cost Center", "default_cost_center": "Cost Center"}


def plan():
    existing = [name for name in LEGACY_COMPANIES if frappe.db.exists("Company", name)]
    if len(existing) > 1 or (existing and frappe.db.exists("Company", COMPANY)):
        frappe.throw("Ambiguous company rename; refusing to merge business records")
    source = existing[0] if existing else COMPANY
    if not frappe.db.exists("Company", source):
        frappe.throw("Expected company is missing")
    if frappe.db.get_value("Company", source, "abbr") != ABBREVIATION:
        frappe.throw("Unexpected company abbreviation; refusing to change account identifiers")
    changes = []
    if source != COMPANY:
        changes.append(("Company", source, COMPANY))
        old_center = f"{source} - {ABBREVIATION}"
        if frappe.db.exists("Cost Center", {"name": old_center, "company": source}):
            changes.append(("Cost Center", old_center, f"{COMPANY} - {ABBREVIATION}"))
    for old, new, _ in WAREHOUSES:
        if frappe.db.exists("Warehouse", old):
            if frappe.db.get_value("Warehouse", old, "company") != source:
                frappe.throw("Warehouse belongs to another company")
            changes.append(("Warehouse", old, new))
    for dt, old, new in changes:
        if frappe.db.exists(dt, new):
            frappe.throw(f"{dt} {new} already exists; refusing to merge records")
    return changes


def _state(changes=()):
    from warehouse_core.record_names import snapshot

    mapping = {(dt, old): new for dt, old, new in changes}

    def mapped(dt, value):
        return mapping.get((dt, value), value)

    state = snapshot(changes)
    for dt in frappe.get_all("DocType", filters={"is_virtual": 0}, pluck="name"):
        meta = frappe.get_meta(dt)
        links = [f for f in meta.fields if not f.get("is_virtual") and
                 ((f.fieldtype == "Link" and f.options in TARGET_TYPES)
                  or f.fieldtype == "Dynamic Link")]
        if meta.issingle:
            row = frappe.db.get_singles_dict(dt)
            state[(dt, "links")] = {
                f.fieldname: mapped(f.options if f.fieldtype == "Link" else row.get(f.options),
                                    row.get(f.fieldname)) for f in links
            }
            continue
        # Saving Global Defaults recreates DefaultValue rows; compare their
        # semantic keys/owners/values below instead of generated row hashes.
        if not frappe.db.table_exists(dt) or dt in AUDIT_TYPES or dt == "DefaultValue":
            continue
        rows = frappe.get_all(dt, fields=["*"])
        state[(dt, "identities")] = sorted(mapped(dt, r.name) for r in rows)
        for f in links:
            state[(dt, f.fieldname)] = sorted(
                (mapped(dt, r.name),
                 mapped(f.options if f.fieldtype == "Link" else r.get(f.options), r.get(f.fieldname)))
                for r in rows)
        if meta.istable:
            state[(dt, "parents")] = sorted(
                (r.name, r.parenttype, r.parentfield, mapped(r.parenttype, r.parent), r.idx)
                for r in rows)
        if meta.has_field("custom_moysklad_id"):
            state[(dt, "external_ids")] = sorted(
                (mapped(dt, r.name), r.custom_moysklad_id) for r in rows)
        if dt in ACCESS_TYPES:
            normalized = []
            for row in rows:
                row = dict(row)
                for f in links:
                    target = f.options if f.fieldtype == "Link" else row.get(f.options)
                    row[f.fieldname] = mapped(target, row.get(f.fieldname))
                for key in ("modified", "modified_by", "last_active", "last_login", "last_ip"):
                    row.pop(key, None)
                normalized.append(row)
            state[(dt, "access")] = sorted(normalized, key=lambda r: r["name"])
    # DefaultValue.defvalue is Data, not a native Link field.
    state["defaults"] = sorted(
        (r.parent, r.defkey, mapped(DEFAULT_TYPES.get(r.defkey.lower()), r.defvalue))
        for r in frappe.get_all("DefaultValue", fields=["parent", "defkey", "defvalue"]))
    state["warehouse_tree"] = sorted(
        (mapped("Warehouse", r.name), mapped("Warehouse", r.parent_warehouse),
         r.is_group, r.lft, r.rgt)
        for r in frappe.get_all("Warehouse", fields=["name", "parent_warehouse", "is_group", "lft", "rgt"]))
    # Compare every financial/quantity field on all accounting and stock rows,
    # including invoices and payments absent from the older demo snapshot.
    for dt in ("Sales Invoice", "Sales Invoice Item", "Purchase Invoice", "Purchase Invoice Item",
               "Payment Entry", "Payment Entry Reference", "Journal Entry", "Journal Entry Account",
               "GL Entry", "Stock Ledger Entry", "Bin"):
        fields = [f.fieldname for f in frappe.get_meta(dt).fields
                  if f.fieldtype in {"Currency", "Float", "Int", "Percent", "Check"}
                  and not f.get("is_virtual")]
        state[(dt, "amounts")] = sorted(
            (r.name, tuple(r.get(f) for f in fields))
            for r in frappe.get_all(dt, fields=["name", *fields]))
    return state


def apply(dry_run=True, rehearsal=False):
    frappe.only_for("System Manager")
    if not frappe.conf.get("warehouse_demo"):
        frappe.throw("This explicit migration requires the configured local sample site")
    changes = plan()
    if dry_run:
        return {"company": COMPANY, "abbreviation": ABBREVIATION, "renames": changes}
    before = _state(changes)
    frappe.db.savepoint("company_identity")
    try:
        for dt, old, new in changes:
            actual = frappe.rename_doc(dt, old, new, force=True, merge=False,
                                       show_alert=False, rebuild_search=False)
            if actual != new:
                raise AssertionError(f"Unexpected rename result for {dt}")
            if dt == "Cost Center":
                frappe.db.set_value(dt, new, "cost_center_name", COMPANY, update_modified=False)
            elif dt == "Warehouse":
                title = next(title for _, target, title in WAREHOUSES if target == new)
                frappe.db.set_value(dt, new, "warehouse_name", title, update_modified=False)
        for row in frappe.get_all("DefaultValue", fields=["name", "defkey", "defvalue"]):
            target = DEFAULT_TYPES.get(row.defkey.lower())
            for dt, old, new in changes:
                if target == dt and row.defvalue == old:
                    frappe.db.set_value("DefaultValue", row.name, "defvalue", new, update_modified=False)
        after = _state()
        differences = [str(key) for key in before.keys() | after.keys() if before.get(key) != after.get(key)]
        if differences:
            raise AssertionError("Company rename changed protected state: " + ", ".join(differences))
        assert frappe.db.get_value("Company", COMPANY, "company_name") == COMPANY
        assert frappe.db.get_value("Company", COMPANY, "abbr") == ABBREVIATION
        assert not plan(), "Rename is not repeatable"
        if rehearsal:
            # Full rollback also clears after-commit realtime callbacks from
            # native rename, so a rehearsal never broadcasts fictitious names.
            frappe.db.rollback()
    except Exception:
        frappe.db.rollback(save_point="company_identity")
        raise
    finally:
        frappe.clear_cache()
        frappe.defaults.clear_defaults_cache()
    if not rehearsal:
        for dt in {r[0] for r in changes}:
            frappe.enqueue("frappe.utils.global_search.rebuild_for_doctype", doctype=dt,
                           enqueue_after_commit=True)
    return {"company": COMPANY, "renamed": changes, "rehearsal_rolled_back": rehearsal,
            "checked_state_groups": len(before),
            "records_links_balances_quantities_external_ids_permissions": "preserved"}
