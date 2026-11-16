"""Build the demo KB snapshot from the local, reconciled public storefront export."""
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "sites/mtech-design-refresh/dist"
OUTPUT = ROOT / "apps/warehouse_core/warehouse_core/assistant_content/catalog.json"


def main():
    categories = dict(re.findall(r"\['([a-z0-9-]+)', '([^']+)', '(?:equipment|parts|services)'",
                                 (SITE / "catalog-data.js").read_text("utf-8")))
    groups = defaultdict(list)
    for entry in json.loads((SITE / "catalog-products.json").read_text("utf-8")):
        product = json.loads((SITE / entry["detail_url"]).read_text("utf-8"))
        group = product["category_id"]
        facts = [f"{s['name']} — {s['value']}" for s in product["specifications"]
                 if s["name"] in ("Діаметр", "Виробник", "Матеріал")]
        groups[group].append({
            "id": product["source_product_id"], "code": product["code"], "name": product["name"],
            "url": product["source_url"], "date": product["captured_at"][:10], "facts": facts,
        })
    result = []
    for group, products in sorted(groups.items()):
        products.sort(key=lambda p: (p["code"] or p["name"], p["id"]))
        for start in range(0, len(products), 5):
            page = products[start:start + 5]
            def label(p):
                return p["code"] or p["name"][:40]
            span = label(page[0]) if len(page) == 1 else f"{label(page[0])} … {label(page[-1])}"
            paragraphs = []
            for p in page:
                details = [p["name"], *p["facts"]]
                if p["code"]:
                    details.append(f"Артикул каталогу — {p['code']}")
                details.append(f"Джерело ({p['date']}) — {p['url']}")
                paragraphs.append("\n".join(details))
            result.append({"id": f"catalog-{group}-{page[0]['id']}", "category": "Товари",
                           "title": f"{categories[group]} · {span}", "content": "\n\n".join(paragraphs)})
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(f"{sum(map(len, groups.values()))} product listings in {len(result)} articles")


if __name__ == "__main__":
    main()
