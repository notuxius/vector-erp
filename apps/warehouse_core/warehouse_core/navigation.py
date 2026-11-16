"""Place operations in Stock and shared knowledge in Administration."""
import frappe

ADMINISTRATION_SECTIONS = (
    ("Знання та інструкції", "Page", (
        ("База знань", "knowledge-base"),
    )),
)

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
    "Знання та інструкції": "book-open",
    "Відправлення та контроль": "truck",
    "Звіти операцій": "sheet",
}


def apply():
    _apply_sidebar("Stock", SECTIONS)
    _apply_sidebar("Setup", ADMINISTRATION_SECTIONS)

    # Retire the old entry, retaining its layout and all business records. The
    # code_only_modules hook makes bare document/report routes resolve to Stock.
    frappe.db.set_value("Workspace", "Warehouse Core", "is_hidden", 1, update_modified=False)
    for name in frappe.get_all("User", filters={"default_workspace": "Warehouse Core"}, pluck="name"):
        frappe.db.set_value("User", name, "default_workspace", "Stock", update_modified=False)
        frappe.clear_cache(user=name)


def _apply_sidebar(module, sections):
    from frappe.desk.doctype.custom_sidebar.custom_sidebar import get_customization
    from frappe.desk.doctype.sidebar.sidebar import item_key

    existing = get_customization(module, None)
    if existing:
        sidebar = frappe.get_doc("Custom Sidebar", existing.name)
    else:
        sidebar = frappe.new_doc("Custom Sidebar")
        sidebar.update({"module": module, "user": ""})
        # References preserve the shipped order and inherit future label changes.
        # Copying whole base rows would freeze upstream labels and destinations.
        for row in frappe.get_doc("Sidebar", module).items:
            sidebar.append("sidebar_items", {
                **{key: row.get(key) for key in ("type", "link_type", "link_to", "url", "child")},
                "key": item_key(row), "added": 0,
            })
    changed = sidebar.is_new()
    # The shared homepage lives above individual modules. Retire only our old link.
    for row in list(sidebar.sidebar_items):
        if (module == "Stock" and row.added and row.link_type == "Page"
                and row.link_to in {"operations-overview", "knowledge-base"}):
            sidebar.sidebar_items.remove(row)
            changed = True
    # Remove our retired knowledge heading only when it has no remaining links.
    if module == "Stock":
        for row in list(sidebar.sidebar_items):
            if row.added and row.type == "Section Break" and row.label == "Знання та інструкції":
                index = sidebar.sidebar_items.index(row)
                following = sidebar.sidebar_items[index + 1:]
                if not following or following[0].type == "Section Break":
                    sidebar.sidebar_items.remove(row)
                    changed = True
    seen = {item_key(row): row for row in sidebar.sidebar_items}
    for label, kind, entries in sections:
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
                "Warehouse Operation Log", "Nova Poshta Shipment", "Nova Poshta Settings",
                "Knowledge Article", "Knowledge Attachment", "Knowledge Access Role", "Knowledge Access User")},
            "roles": sorted((r.parent, r.role) for r in frappe.get_all("Has Role", fields=["parent", "role"])),
        }

    original = frappe.session.user
    try:
        frappe.set_user("Administrator")
        before = snapshot()
        apply()
        first = {module: get_customization(module, None).as_dict() for module in ("Stock", "Setup")}
        apply()
        second_by_module = {module: get_customization(module, None).as_dict() for module in ("Stock", "Setup")}
        second = second_by_module["Stock"]
        assert first == second_by_module, "Navigation migration is not idempotent"
        assert snapshot() == before, "Navigation changed business data or role assignments"
        for module, customization in second_by_module.items():
            keys = [item_key(row) for row in customization.sidebar_items]
            assert len(keys) == len(set(keys)), f"Duplicate sidebar rows in {module}"
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
        for _, kind, entries in ADMINISTRATION_SECTIONS:
            for _, target in entries:
                assert any(row.get("link_to") == target for row in sidebars["Setup"]["items"]), target
                assert not any(row.get("link_to") == target for row in sidebars["Stock"]["items"]), target
                assert canonical[kind][target] == "Setup", target
        assert frappe.db.get_value("Workspace", "Warehouse Core", "is_hidden")
        assert {"frappe", "erpnext", "warehouse_core"}.issubset(frappe.get_installed_apps())
        return {"ok": True, "destination": "Vector ERP / Stock", "knowledge_destination": "Vector ERP / Administration",
                "moved_links": sum(len(entries) for _, _, entries in SECTIONS + ADMINISTRATION_SECTIONS),
                "separate_shell_hidden": True, "repeatable": True, "records_and_roles_preserved": True}
    finally:
        frappe.db.rollback()
        frappe.set_user(original)
