"""Verify full discovered coverage, downloaded bytes, source fields and edit preservation."""
import hashlib
import json
from pathlib import Path

from bs4 import BeautifulSoup
from PIL import Image

from extract_mtech_sample import ROOT, encoded, read_json, text, write


def verify(root):
    inventory = read_json(root / "source/inventory.json", {})
    products = read_json(root / "manifest.json", {}).get("products", [])
    ids = {p["source_product_id"] for p in products}
    failures, notes, assets = [], [], {}
    missing = sorted(set(inventory.get("products", {})) - ids)
    if missing:
        failures.append({"missing_product_ids": missing})
    if len(ids) != len(products):
        failures.append({"issue": "Duplicate product IDs"})
    for p in products:
        pid = p["source_product_id"]
        if len(p["gallery"]) != p["expected_gallery_count"]:
            failures.append({"product_id": pid, "issue": "Incomplete gallery"})
        if p.get("missing_description_images"):
            failures.append({"product_id": pid, "missing_description_images": p["missing_description_images"]})
        for field in ["code", "description_text", "category_id"]:
            if not p.get(field):
                notes.append({"product_id": pid, "missing_field": field})
        source = root / "products" / pid / "revisions" / p["revision"] / "source.html"
        soup = BeautifulSoup(source.read_bytes(), "html.parser")
        for key, selector in [("name", "h1"), ("code", '[data-qaid="product_code"]')]:
            if p[key] != text(soup.select_one(selector)):
                failures.append({"product_id": pid, "issue": "Source field mismatch", "field": key})
        if p["price"]["raw"] != text(soup.select_one('[data-qaid="product_price"]')):
            failures.append({"product_id": pid, "issue": "Source price mismatch"})
        if p["availability"]["raw"] != text(soup.select_one('[data-qaid="presence_data"]')):
            failures.append({"product_id": pid, "issue": "Source availability mismatch"})
        for a in p["gallery"] + p["description_images"]:
            assets[a["path"]] = a
    for path, a in assets.items():
        try:
            file = root / path
            if hashlib.sha256(file.read_bytes()).hexdigest() != a["sha256"]:
                raise ValueError("File hash mismatch")
            with Image.open(file) as image:
                if list(image.size) != [a["width"], a["height"]]:
                    raise ValueError("Image dimensions mismatch")
                image.verify()
        except Exception as exc:
            failures.append({"path": path, "error": str(exc)})
    protected = read_json(root / "reports/pre-full-editorial-hashes.json", {})
    for path, expected in protected.items():
        file = root / path
        if not file.exists() or hashlib.sha256(file.read_bytes()).hexdigest() != expected:
            failures.append({"path": path, "issue": "Edited/editorial file changed"})
    preview = BeautifulSoup((root / "review/index.html").read_text(encoding="utf-8"), "html.parser")
    if len(preview.select("article")) != len(products):
        failures.append({"issue": "Preview product count mismatch"})
    for img in preview.select("img[src]"):
        if not (root / "review" / img["src"]).is_file():
            failures.append({"issue": "Broken preview image", "src": img["src"]})
    report = {
        "products": len(products), "discovered_products": len(inventory.get("products", {})),
        "sitemap_products": inventory.get("sitemap_product_count"),
        "extra_products_from_categories": len(inventory.get("extra_ids_from_categories", [])),
        "gallery_photos": sum(len(p["gallery"]) for p in products),
        "description_image_occurrences": sum(len(p["description_images"]) for p in products),
        "unique_image_files": len(assets),
        "images_with_short_edge_below_600px": sum(min(a["width"], a["height"]) < 600 for a in assets.values()),
        "image_mb": round(sum(a["bytes"] for a in assets.values()) / 1024 / 1024, 1),
        "description_tables": sum(len(p.get("description_tables", [])) for p in products),
        "specification_rows": sum(len(p["specifications"]) for p in products),
        "protected_files_unchanged": len(protected), "failures": failures, "metadata_notes": notes,
    }
    write(root / "reports/verification.json", encoded(report))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(verify(ROOT / "data/mtech-product-archive"))
