"""Initialize Banking translations before React evaluates any component modules.

The pinned Banking entry fetches translations asynchronously, while main.tsx
renders immediately. Frappe already supplies the complete dictionary in boot.
Patch both source and built entry pages; no business logic or bundles change.
"""
from pathlib import Path
import re

root = Path("/home/frappe/frappe-bench/apps/erpnext")
for relative in ("banking/index.html", "erpnext/public/banking/index.html", "erpnext/www/banking.html"):
    path = root / relative
    source = path.read_text()
    title = "<title>Banking | {{ app_name }}</title>"
    assert source.count(title) == 1, f"Review Banking title patch: {relative}"
    source = source.replace(title, '<title>{{ _("Banking") }} | {{ app_name }}</title>')
    source, count = re.subn(
        r"frappe\._translations_loaded = fetch\([\s\S]*?\}\)\.catch\(\(\) => \{ \}\);",
        "frappe._messages = frappe.boot.__messages || {};\n"
        "    frappe._translations_loaded = Promise.resolve();",
        source,
    )
    assert count == 1, f"Review Banking dictionary initialization patch: {relative}"
    path.write_text(source)
