"""Route socket authentication internally while retaining browser origin checks."""
from pathlib import Path

path = Path("/home/frappe/frappe-bench/apps/frappe/realtime/utils.js")
source = path.read_text()
original = "let url = socket.request.headers.origin;"
assert source.count(original) == 1, "Review the realtime URL patch against this Frappe version"
path.write_text(source.replace(original,
    "let url = process.env.FRAPPE_REALTIME_BACKEND_URL || socket.request.headers.origin;"))
