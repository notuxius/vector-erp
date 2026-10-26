"""Render the selected Desk app's icon before its JavaScript starts."""
import re
from urllib.parse import unquote


def app_logo_for_path(boot, path):
    """Resolve against the user's boot navigation, without fetching any documents."""
    parts = [unquote(part) for part in path.strip("/").split("/")]
    if not parts or parts.pop(0) not in {"desk", "app"} or not parts:
        return None  # All apps has no selected app.

    sidebars = boot.get("module_sidebars") or {}
    canonical = boot.get("canonical_shell") or {}
    slug = lambda name: name.lower().replace(" ", "-")
    shells = {re.sub(r"\s+", "-", name.lower().replace("&", " and ").strip()): name
              for name in sidebars if name != "Private"}
    workspaces = {slug(name): shell for shell, sidebar in sidebars.items()
                  for name in sidebar.get("workspaces", [])}
    doctypes = {slug(name): name for name in (boot.get("user") or {}).get("can_read", [])}
    for layout in boot.get("doctype_layouts") or []:
        doctypes[slug(layout["name"])] = layout["document_type"]

    def entity(route):
        first = route[0]
        if first in workspaces:
            return "Workspace", None, workspaces[first]
        if first == "private":
            return "Workspace", None, "Private"
        if first in doctypes:
            kind, name = "DocType", doctypes[first]
        elif len(route) > 1 and first.lower() in {"list", "form", "tree"}:
            kind, name = "DocType", route[1]
        elif len(route) > 1 and first.lower() in {"query-report", "report"}:
            kind, name = "Report", route[1]
        elif len(route) > 1 and first == "dashboard-view":
            kind, name = "Dashboard", route[1]
        elif len(route) > 1 and first == "Workspaces":
            return "Workspace", None, workspaces.get(slug(route[-1]))
        else:
            kind, name = "Page", first
        return kind, name, canonical.get(kind, {}).get(name)

    stated = None
    # A workspace wins a slug collision; a doctype's second segment is a record ID.
    if (len(parts) > 1 and parts[0] in shells
            and (parts[0] in workspaces or parts[0] not in doctypes)
            and entity(parts[1:])[2]):
        stated = shells[parts.pop(0)]
    kind, name, shell = entity(parts)
    hosts = boot.get("app_rail_host") or {}

    def owner(value):
        app = sidebars.get(value, {}).get("app")
        return hosts.get(app, app)

    if stated and kind != "Workspace":
        listed = any(item.get("link_type") == kind and item.get("link_to") == name
                     for item in sidebars[stated].get("items", []))
        if listed or (owner(shell) and owner(stated) == owner(shell)):
            shell = stated
    app_name = owner(shell)
    if not app_name:
        return None
    app = next((app for app in boot.get("app_data", []) if app.get("app_name") == app_name), {})
    logo = app.get("app_logo_url")
    # Frappe app hooks may supply a list; app-screen entries supply a string.
    return (logo[-1] if logo else None) if isinstance(logo, (list, tuple)) else logo


def update_website_context(context):
    import frappe

    boot = context.get("boot")
    # Background/CLI print rendering has a boot context without an HTTP request.
    request = getattr(frappe.local, "request", None)
    if boot and request:
        logo = app_logo_for_path(boot, request.path)
        if logo:
            context["splash_image"] = logo
