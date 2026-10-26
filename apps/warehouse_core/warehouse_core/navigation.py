"""Place the custom operations inside Vector ERP's existing Stock navigation."""
import frappe

SECTIONS = (
    ("Відправлення та контроль", "DocType", (
        ("Журнал операцій", "Warehouse Operation Log"),
        ("Налаштування Нової пошти", "Nova Poshta Settings"),
        ("Відправлення Нової пошти", "Nova Poshta Shipment"),
        ("Встановлення серійних вузлів", "Machine Assembly Installation"),
    )),
    ("Звіти операцій", "Report", (
        ("Готовність замовлень до відправлення", "Order Readiness"),
        ("Щоденний список для комірника", "Daily Dispatch List"),
        ("Історія сервісу верстата", "Machine Service History"),
        ("Очікуваний випуск виробництва", "Expected Production"),
    )),
)

SECTION_ICONS = {
    "Відправлення та контроль": "truck",
    "Звіти операцій": "sheet",
}


def apply():
    from frappe.desk.doctype.custom_sidebar.custom_sidebar import get_customization
    from frappe.desk.doctype.sidebar.sidebar import item_key

    existing = get_customization("Stock", None)
    if existing:
        sidebar = frappe.get_doc("Custom Sidebar", existing.name)
    else:
        sidebar = frappe.new_doc("Custom Sidebar")
        sidebar.update({"module": "Stock", "user": ""})
        # References preserve the shipped order and inherit future label changes.
        # Copying whole base rows would freeze upstream labels and destinations.
        for row in frappe.get_doc("Sidebar", "Stock").items:
            sidebar.append("sidebar_items", {
                **{key: row.get(key) for key in ("type", "link_type", "link_to", "url", "child")},
                "key": item_key(row), "added": 0,
            })
    seen = {item_key(row): row for row in sidebar.sidebar_items}
    changed = sidebar.is_new()
    # The shared homepage lives above individual modules. Retire only our old link.
    for row in list(sidebar.sidebar_items):
        if row.added and row.link_type == "Page" and row.link_to == "operations-overview":
            sidebar.sidebar_items.remove(row)
            changed = True
    for label, kind, entries in SECTIONS:
        # Use the same native icon-row presentation as Stock's Tools/Setup groups.
        rows = [{"type": "Section Break", "label": label, "collapsible": 1,
                 "keep_closed": 0, "indent": 1, "icon": SECTION_ICONS[label]}]
        rows.extend({"type": "Link", "label": title, "link_type": kind, "link_to": target, "child": 1}
                    for title, target in entries)
        for row in rows:
            key = item_key(row)
            if key not in seen:
                seen[key] = sidebar.append("sidebar_items", {**row, "added": 1})
                changed = True
            elif row["type"] == "Section Break" and seen[key].added:
                # Upgrade existing app-owned headings without replacing their
                # rows, ordering, links, or saved expanded/collapsed choices.
                for field in ("indent", "icon"):
                    if seen[key].get(field) != row[field]:
                        seen[key].set(field, row[field])
                        changed = True
        # New links must be inserted inside their section, not after the last
        # existing section. Limit placement repair to these newly shipped links.
        for title, target in entries:
            if target not in {"Machine Assembly Installation", "Expected Production"}:
                continue
            current = next((item for item in sidebar.sidebar_items if item.link_to == target and item.added), None)
            if not current:
                continue
            parent_label = None
            for item in sidebar.sidebar_items:
                if item.type == "Section Break":
                    parent_label = item.label
                if item == current:
                    break
            if parent_label != label:
                sidebar.sidebar_items.remove(current)
                heading_index = next(index for index, item in enumerate(sidebar.sidebar_items)
                                     if item.type == "Section Break" and item.label == label)
                insert_at = next((index for index in range(heading_index + 1, len(sidebar.sidebar_items))
                                  if sidebar.sidebar_items[index].type == "Section Break"), len(sidebar.sidebar_items))
                sidebar.sidebar_items.insert(insert_at, current)
                changed = True
    if changed:
        for index, item in enumerate(sidebar.sidebar_items, 1):
            item.idx = index
        sidebar.save(ignore_permissions=True)

    # Retire the old entry, retaining its layout and all business records. The
    # code_only_modules hook makes bare document/report routes resolve to Stock.
    frappe.db.set_value("Workspace", "Warehouse Core", "is_hidden", 1, update_modified=False)
    for name in frappe.get_all("User", filters={"default_workspace": "Warehouse Core"}, pluck="name"):
        frappe.db.set_value("User", name, "default_workspace", "Stock", update_modified=False)
        frappe.clear_cache(user=name)


def verify():
    """Check real resolved navigation, idempotence, and preservation of roles/data."""
    from frappe.boot import get_module_sidebars, build_entity_module_map
    from frappe.desk.desk_views import DeskViews
    from frappe.desk.doctype.sidebar.sidebar import build_canonical_shells, item_key
    from frappe.desk.doctype.custom_sidebar.custom_sidebar import get_customization

    def snapshot():
        return {
            "records": {dt: frappe.db.count(dt) for dt in (
                "Delivery Note", "Sales Order", "Stock Ledger Entry", "GL Entry",
                "Warehouse Operation Log", "Nova Poshta Shipment", "Nova Poshta Settings")},
            "roles": sorted((r.parent, r.role) for r in frappe.get_all("Has Role", fields=["parent", "role"])),
        }

    original = frappe.session.user
    try:
        frappe.set_user("Administrator")
        before = snapshot()
        apply()
        first = get_customization("Stock", None).as_dict()
        apply()
        second = get_customization("Stock", None).as_dict()
        assert first == second, "Navigation migration is not idempotent"
        assert snapshot() == before, "Navigation changed business data or role assignments"
        keys = [item_key(row) for row in second.sidebar_items]
        assert len(keys) == len(set(keys)), "Duplicate sidebar rows"
        section = None
        for row in second.sidebar_items:
            if row.type == "Section Break":
                section = row.label
            if row.link_to == "Machine Assembly Installation":
                assert section == "Відправлення та контроль", "Installation link is outside its section"
            if row.link_to == "Expected Production":
                assert section == "Звіти операцій", "Production report is outside its section"
        sidebars = get_module_sidebars()
        assert "Warehouse Core" not in sidebars, "Separate operations shell is still visible"
        assert sidebars["Stock"]["app"] == "erpnext"
        canonical, _ = build_canonical_shells(sidebars, build_entity_module_map(sidebars), DeskViews())
        for _, kind, entries in SECTIONS:
            for _, target in entries:
                assert any(row.get("link_to") == target for row in sidebars["Stock"]["items"]), target
                assert canonical[kind][target] == "Stock", target
        assert frappe.db.get_value("Workspace", "Warehouse Core", "is_hidden")
        assert {"frappe", "erpnext", "warehouse_core"}.issubset(frappe.get_installed_apps())
        return {"ok": True, "destination": "Vector ERP / Stock", "moved_links": sum(len(entries) for _, _, entries in SECTIONS),
                "separate_shell_hidden": True, "repeatable": True, "records_and_roles_preserved": True}
    finally:
        frappe.db.rollback()
        frappe.set_user(original)
