"""Localize trend month captions without changing dates, filters or values."""
from pathlib import Path

root = Path("/home/frappe/frappe-bench/apps/erpnext/erpnext")
path = root / "controllers/trends.py"
source = path.read_text()
original = 'return getdate(dt).strftime("%b")'
assert source.count(original) == 1, "Review trend month translation patch"
path.write_text(source.replace(original, 'return _(getdate(dt).strftime("%b"), context="Month abbreviation")'))

# Column captions already translate Amt. Strip the translated suffix as well,
# so short month labels do not become truncated "Jan (С ..." chart captions.
path = root / "selling/report/sales_order_trends/sales_order_trends.py"
source = path.read_text()
original = '.replace(" (Amt)", "")'
assert source.count(original) == 1, "Review sales trend chart caption patch"
path.write_text(source.replace(original, '.replace(" (" + _("Amt") + ")", "")'))
