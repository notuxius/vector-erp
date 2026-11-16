"""Archive MTech public products; source files and editorial work stay separate.

Install scripts/mtech-extraction-requirements.txt, then run with --help.
"""
import argparse
import csv
import hashlib
import html
import io
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://mtech-cnc.com.ua"
PILOT = {
    "2337939480": "metal-lasers",
    "2367949497": "co2-lasers",
    "2371310483": "pro-routers",
    "2337702451": "spindles",
    "2337723903": "spindles",
    "2337635281": "cooling-systems",
    "2336791084": "controllers",
    "2333410055": "laser-tubes",
    "2337803974": "motor-driver-kits",
    "745076463": "cable-chains",
}
CATEGORY_GROUPS = dict(zip(
    "144534140 145648973 134914812 144532069 144532106 138929067 134877559 36491833 36491901 134880043 134908354 134905369 149074866 36491837 134904426 36493643 134847312 36491852 134852871 134879592 36493795 134842019 36493675 36491867 134851352 134998475 134998791 147402803 147811653 136132608 137499016 137499150 139652412 140335321 142747788".split(),
    "metal-lasers laser-markers pro-routers hr-routers co2-lasers laser-welding-cleaning controllers laser-tubes stepper-drivers stepper-motors motor-driver-kits spindles frequency-converters high-voltage-supplies cooling-systems switches-sensors-wiring honeycomb-tables linear-guides-bearings ball-screws gear-drives flexible-couplings collets lenses-mirrors cable-chains industrial-hardware dust-shoe-brushes laser-extraction rotary-axes pneumatics services power-supplies lubrication-systems vacuum-pumps laser-cutting-heads production-equipment".split()
))


def category_from_breadcrumbs(breadcrumbs):
    for entry in reversed(breadcrumbs):
        match = re.search(r"/g(\d+)-", entry.get("url", ""))
        if match and match[1] in CATEGORY_GROUPS:
            return CATEGORY_GROUPS[match[1]]
    return None


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    for attempt in range(12):
        try:
            temp.replace(path)
            break
        except PermissionError:
            # Windows indexers/preview readers can briefly lock a destination.
            if attempt == 11:
                raise
            time.sleep(.25 * (attempt + 1))


def read_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def text(node):
    return node.get_text(" ", strip=True) if node else ""


class Archive:
    def __init__(self, args):
        self.root = args.output.resolve()
        self.refresh = args.refresh_images
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "MTechProductArchive/1.0 (local catalogue archive)"
        self.last_request = 0
        self.assets = read_json(self.root / "asset-index.json", {})
        self.report = {"started_at": datetime.now(timezone.utc).isoformat(), "downloaded": 0,
                       "reused": 0, "not_modified": 0, "errors": [], "warnings": [], "products": []}
        self.robots = None

    def get(self, url, headers=None):
        if self.robots and urlparse(url).hostname == urlparse(BASE).hostname:
            if not self.robots.can_fetch("MTechProductArchive", url):
                raise ValueError("Disallowed by robots.txt: " + url)
        for attempt in range(3):
            time.sleep(max(0, .35 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                response = self.session.get(url, headers=headers or {}, timeout=45)
                if response.status_code in (429, 500, 502, 503, 504):
                    time.sleep(min(10, 2 ** (attempt + 1)))
                    continue
                response.raise_for_status()
                return response
            except requests.RequestException:
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)
        raise RuntimeError("Retries exhausted: " + url)

    def asset(self, pid, url, image_id, role="gallery", order=0, alt=""):
        """Use content-addressed immutable files; never touch edited/ or editorial/."""
        observed_url = urljoin(BASE, url)
        host = urlparse(observed_url).hostname or ""
        if host not in {"images.prom.ua", "mtech.com.ua"}:
            raise ValueError("Unexpected image host: " + host)
        # Prom's public unscaled rendition. The exact requested URL is retained.
        url = re.sub(r"_w\d+_h\d+(?=_|\.)", "", observed_url) if host == "images.prom.ua" else observed_url
        key = image_id if image_id else digest(url.encode())[:20]
        cache = self.assets.get(key)
        cache_valid = bool(cache and (self.root / cache["path"]).exists()
                           and digest((self.root / cache["path"]).read_bytes()) == cache["sha256"])
        if cache_valid and not self.refresh:
            self.report["reused"] += 1
            result = dict(cache)
        else:
            headers = {}
            if cache_valid and cache.get("etag"):
                headers["If-None-Match"] = cache["etag"]
            if cache_valid and cache.get("last_modified"):
                headers["If-Modified-Since"] = cache["last_modified"]
            response = self.get(url, headers)
            if response.status_code == 304 and cache_valid:
                self.report["not_modified"] += 1
                result = dict(cache)
            else:
                with Image.open(io.BytesIO(response.content)) as image:
                    width, height = image.size
                    fmt = image.format
                    image.verify()
                sha = digest(response.content)
                extension = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif"}.get(fmt)
                if not extension:
                    raise ValueError("Unsupported image format: " + str(fmt))
                path = Path("originals") / pid / f"{key}-{sha[:16]}.{extension}"
                # Deduplicate equal bytes even when source image IDs differ.
                existing = next((a for a in self.assets.values() if a["sha256"] == sha
                                 and (self.root / a["path"]).exists()), None)
                if existing:
                    path = Path(existing["path"])
                else:
                    write(self.root / path, response.content)
                result = {"source_image_id": image_id, "download_url": url,
                          "path": path.as_posix(), "sha256": sha, "width": width, "height": height,
                          "bytes": len(response.content), "format": fmt,
                          "etag": response.headers.get("ETag"),
                          "last_modified": response.headers.get("Last-Modified"),
                          "downloaded_at": datetime.now(timezone.utc).isoformat()}
                if cache and cache["sha256"] != sha:
                    self.report["warnings"].append({"product_id": pid, "image_id": key,
                        "issue": "Source image changed; prior original retained. Review any edited version."})
                elif cache_valid and cache["sha256"] == sha:
                    result = dict(cache)
                self.assets[key] = result
                write(self.root / "asset-index.json", encoded(self.assets))
                self.report["downloaded"] += 1
        return {**result, "source_url": observed_url, "role": role, "order": order, "alt": alt}

    def product(self, pid, url, category):
        response = self.get(url)
        response.encoding = "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")
        structured = []
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(script.get_text())
                structured.extend(data if isinstance(data, list) else [data])
            except json.JSONDecodeError:
                pass
        product = next((s for s in structured if s.get("@type") == "Product"), {})
        if not product or not soup.h1:
            raise ValueError("Expected product page/structured Product data missing")
        breadcrumb = next((s for s in structured if s.get("@type") == "BreadcrumbList"), {})
        breadcrumbs = [dict(name=e["item"].get("name"), url=urljoin(BASE, e["item"].get("@id", "")))
                       for e in breadcrumb.get("itemListElement", [])]
        category = category or category_from_breadcrumbs(breadcrumbs)
        description = soup.select_one('[data-qaid="product_description"]')
        description_html = str(description) if description else ""
        description_text = description.get_text("\n", strip=True) if description else product.get("description", "")
        specs, group = [], None
        for row in soup.select('[data-qaid="characteristics_block"] tr'):
            heading = row.select_one('[data-qaid="attributes_group"]')
            if heading:
                group = text(heading)
            name, value = row.select_one('[data-qaid="attribute_name"]'), row.select_one('[data-qaid="attribute_value"]')
            if name and value:
                specs.append({"group": group, "name": text(name), "value": text(value)})
        availability = text(soup.select_one('[data-qaid="presence_data"]'))
        quantity = re.search(r"(\d+(?:[.,]\d+)?)\s*(од\.|шт\.|компл\.)", availability)
        offers = product.get("offers", {})
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        price_text = text(soup.select_one('[data-qaid="product_price"]'))
        price = offers.get("price")
        if price is None and "₴" in price_text:
            value = re.sub(r"[^\d,.]", "", price_text.split("₴")[0]).replace(",", ".")
            price = value or None
        code = text(soup.select_one('[data-qaid="product_code"]'))
        images, videos, expected, seen = [], [], 0, set()
        for node in soup.select('[data-cspgo-image-id]'):
            img = node.find("img")
            if not img or not img.get("src"):
                continue
            image_id, image_url = node["data-cspgo-image-id"], img["src"]
            if image_id in seen:
                continue
            seen.add(image_id)
            if urlparse(image_url).hostname != "images.prom.ua":
                videos.append({"source_media_id": image_id, "thumbnail_url": image_url, "label": img.get("alt", "")})
                continue
            expected += 1
            try:
                images.append(self.asset(pid, image_url, image_id, order=expected, alt=img.get("alt", "")))
            except Exception as exc:
                self.report["errors"].append({"product_id": pid, "url": image_url, "error": str(exc)})
        description_images, missing_description_images = [], []
        for order, img in enumerate(description.select("img") if description else [], 1):
            source_url = img.get("src") or img.get("data-src")
            if not source_url:
                self.report["warnings"].append({"product_id": pid, "issue": "Empty image placeholder in source description", "order": order})
                continue
            image_url = urljoin(url, source_url)
            match = re.search(r"/(\d+)[_.]", image_url) if urlparse(image_url).hostname == "images.prom.ua" else None
            try:
                description_images.append(self.asset(pid, image_url, match[1] if match else None,
                                                      role="description", order=order, alt=img.get("alt", "")))
            except Exception as exc:
                self.report["errors"].append({"product_id": pid, "url": image_url, "error": str(exc)})
                missing_description_images.append({"source_url": image_url, "order": order})
        description_tables = [[[text(cell) for cell in row.find_all(["th", "td"], recursive=False)]
                               for row in table.find_all("tr")]
                              for table in (description.select("table") if description else [])]
        links = [{"label": text(a), "url": urljoin(url, a["href"])}
                 for a in (description.select("a[href]") if description else [])]
        documents = [link for link in links if re.search(r"\.(pdf|docx?|xlsx?|zip)(?:$|\?)", link["url"], re.I)]
        snapshot = {"source": "mtech-cnc.com.ua", "source_product_id": pid, "source_url": url,
                    "language": "uk", "name": text(soup.h1), "code": code,
                    "category_id": category, "category_mapping_status": "source-category-map" if category else "unmapped",
                    "breadcrumbs": breadcrumbs, "description_html": description_html,
                    "description_text": description_text, "description_tables": description_tables,
                    "specifications": specs,
                    "price": {"amount": str(price) if price is not None else None,
                              "currency": offers.get("priceCurrency"), "raw": price_text,
                              "unit": price_text.split("/", 1)[1] if "/" in price_text else None},
                    "availability": {"raw": availability,
                                     "status": availability[:quantity.start()].strip() if quantity else availability,
                                     "displayed_quantity": quantity[1].replace(",", ".") if quantity else None,
                                     "unit": quantity[2] if quantity else None,
                                     "schema_status": offers.get("availability"), "is_erp_stock": False},
                    "labels": list(dict.fromkeys(text(x) for x in soup.select('[data-qaid="stripe_label"]'))),
                    "gallery": images, "expected_gallery_count": expected,
                    "description_images": description_images, "missing_description_images": missing_description_images,
                    "videos": videos,
                    "description_links": links, "document_links": documents,
                    "documents_downloaded": False}
        if not images or len(images) != expected:
            self.report["warnings"].append({"product_id": pid, "issue": "Gallery incomplete or empty"})
        for asset in images + description_images:
            if min(asset["width"], asset["height"]) < 600:
                self.report["warnings"].append({"product_id": pid, "image_id": asset["source_image_id"],
                                               "issue": "Short edge below 600 px", "size": [asset["width"], asset["height"]]})
        revision = digest(encoded(snapshot))
        current_path = self.root / "products" / pid / "current.json"
        old = read_json(current_path, {})
        captured = datetime.now(timezone.utc).isoformat()
        if old.get("revision") == revision:
            record = {**old, "last_checked_at": captured}
            state = "unchanged"
        else:
            record = {**snapshot, "revision": revision, "captured_at": captured, "last_checked_at": captured}
            revision_dir = self.root / "products" / pid / "revisions" / revision
            write(revision_dir / "product.json", encoded(record))
            write(revision_dir / "source.html", response.content)
            write(revision_dir / "description.html", description_html)
            write(revision_dir / "description.txt", description_text)
            state = "changed" if old else "new"
        write(current_path, encoded(record))
        self.report["products"].append({"id": pid, "state": state, "photos": len(images),
                                         "expected_photos": expected, "specifications": len(specs)})
        print(f'{pid}: {len(images)}/{expected} photos, {len(specs)} specifications, {state}', flush=True)
        return record

    def export(self):
        products = [read_json(p, {}) for p in sorted((self.root / "products").glob("*/current.json"))]
        write(self.root / "manifest.json", encoded({"schema_version": 1, "products": products}))
        out = io.StringIO(newline="")
        fields = ["source_product_id", "code", "name", "category_id", "price", "currency", "availability",
                  "displayed_quantity", "gallery_count", "source_url", "captured_at"]
        writer = csv.DictWriter(out, fieldnames=fields)
        writer.writeheader()
        for p in products:
            row = {k: p.get(k) for k in fields}
            row.update(price=p["price"]["amount"], currency=p["price"]["currency"],
                       availability=p["availability"]["raw"], displayed_quantity=p["availability"]["displayed_quantity"],
                       gallery_count=len(p["gallery"]))
            # Spreadsheet-safe display; exact source values remain in JSON.
            writer.writerow({k: "'" + str(v) if str(v).startswith(("=", "+", "-", "@")) else v for k, v in row.items()})
        write(self.root / "manifest.csv", b"\xef\xbb\xbf" + out.getvalue().encode("utf-8"))
        write(self.root / "catalogue-products.json", encoded({"schema_version": 1,
              "status": "source-snapshot-for-review", "asset_base": ".", "products": products}))
        self.preview(products)

    def preview(self, products):
        esc = lambda value: html.escape(str(value or ""), quote=True)
        cards = []
        for p in products:
            photos = ''.join(f'<a href="../{esc(a["path"])}" target="_blank"><img loading="lazy" src="../{esc(a["path"])}" alt="{esc(a["alt"])}"><span>{a["order"]} · {a["width"]} × {a["height"]}</span></a>' for a in p["gallery"])
            specs = ''.join(f'<tr><th>{esc(s["name"])}</th><td>{esc(s["value"])}</td></tr>' for s in p["specifications"])
            links = ''.join(f'<li><a href="{esc(l["url"])}" rel="noreferrer">{esc(l["label"] or l["url"])}</a></li>' for l in p["document_links"] if l["url"].startswith("https://"))
            cards.append(f'<article id="p{p["source_product_id"]}"><div class="heading"><div><small>{esc(p["category_id"])} / {p["source_product_id"]}</small><h2>{esc(p["name"])}</h2><p>Код: <strong>{esc(p["code"])}</strong></p></div><div class="price">{esc(p["price"]["raw"])}<p>{esc(p["availability"]["raw"])}</p></div></div><div class="photos">{photos}</div><details><summary>Опис і характеристики</summary><p class="description">{esc(p["description_text"])}</p><table>{specs}</table><ul>{links}</ul></details><footer><a href="{esc(p["source_url"])}" target="_blank" rel="noreferrer">Джерело ↗</a><span>{len(p["gallery"])} фото · {len(p["specifications"])} характеристик · {esc(p["captured_at"][:10])}</span></footer></article>')
        page = '<!doctype html><html lang="uk"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MTech — зразок архіву товарів</title><style> *{box-sizing:border-box}body{margin:0;background:#eef1f3;color:#19262d;font:16px system-ui,sans-serif}main{max-width:1200px;margin:auto;padding:40px 24px}h1{font-size:clamp(28px,5vw,46px);margin:10px 0}h2{font-size:22px;margin:8px 0}header{margin-bottom:30px}header p{max-width:850px;line-height:1.6;color:#465963}.badge{color:#06725f;font-size:12px;letter-spacing:2px;font-weight:700}article{background:white;border:1px solid #d8e1e5;border-radius:14px;padding:24px;margin:24px 0}.heading{display:flex;gap:24px;justify-content:space-between}.price{font-weight:750;font-size:23px;min-width:210px}.price p,small,footer{font-size:13px;color:#536771}.photos{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px;margin:24px 0}.photos a{border:1px solid #e0e6ea;border-radius:8px;overflow:hidden;text-decoration:none;color:#536771}.photos img{display:block;width:100%;height:155px;object-fit:contain;background:white}.photos span{display:block;padding:9px;font-size:12px}summary{cursor:pointer;font-weight:650;padding:12px 0}.description{white-space:pre-line;line-height:1.65;overflow-wrap:anywhere}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:10px;border-bottom:1px solid #e0e6ea;vertical-align:top}footer{display:flex;gap:15px;justify-content:space-between;margin-top:20px}a{color:#06725f}a:focus-visible,summary:focus-visible{outline:3px solid #e7a600;outline-offset:3px}@media(max-width:650px){main{padding:24px 12px}article{padding:16px}.heading,footer{display:block}.photos{grid-template-columns:repeat(2,minmax(0,1fr))}.price{margin-top:16px}footer span{display:block;margin-top:10px}}</style><main><header><span class="badge">MTECH / АРХІВ ТОВАРІВ</span><h1>Перші 10 товарів для каталогу</h1><p>Оригінальні фото, описи й характеристики. Ціни та наявність — знімок публічного сайту на дату збору, а не підтверджені залишки ERP. Натисніть фото, щоб відкрити повний розмір. Покращені фото зберігатимуться окремо.</p></header>' + ''.join(cards) + '</main></html>'
        write(self.root / "review" / "index.html", page)

        # Keep the full archive searchable without changing any published site.
        page = page.replace("Перші 10 товарів для каталогу", f"Архів товарів · {len(products)}")
        page = page.replace("MTech — зразок архіву товарів", "MTech — архів товарів")
        page = page.replace("</header>", '<label for="product-search">Пошук за назвою, кодом або характеристиками</label><input id="product-search" type="search" placeholder="Наприклад, FL3015 або шпиндель" style="display:block;width:100%;padding:14px;margin-top:8px;border:1px solid #8b9da6;border-radius:8px;font:inherit"><p id="search-count" role="status"></p></header>')
        page = page.replace("</html>", '<script>const input=document.getElementById("product-search");const cards=[...document.querySelectorAll("article")];const count=document.getElementById("search-count");function search(){const q=input.value.trim().toLocaleLowerCase("uk");let n=0;cards.forEach(card=>{const show=card.textContent.toLocaleLowerCase("uk").includes(q);card.hidden=!show;if(show)n++;});count.textContent=`Показано ${n} із ${cards.length} товарів`;}input.addEventListener("input",search);search();</script></html>')
        write(self.root / "review" / "index.html", page)

    def discover(self, crawl_categories=False):
        """Read every product sitemap and cross-check public category links."""
        urls, listing_queue, visited = {}, [], set()
        index = self.get(BASE + "/sitemap.xml").content
        write(self.root / "source" / "sitemap.xml", index)
        maps = [e.text for e in ET.fromstring(index).iter() if e.tag.endswith("}loc")]
        for sitemap_url in maps:
            if not any(s in sitemap_url for s in ("sitemap_products", "sitemap_groups")):
                continue
            xml = self.get(sitemap_url).content
            write(self.root / "source" / Path(urlparse(sitemap_url).path).name, xml)
            for entry in ET.fromstring(xml).iter():
                if not entry.tag.endswith("}loc") or not entry.text:
                    continue
                url = entry.text
                match = re.search(r"/p(\d+)-", url)
                if match:
                    # The two language pages share a product ID.
                    path = urlparse(url).path
                    urls[match[1]] = BASE + (path if path.startswith("/ua/") else "/ua" + path)
                elif "/ua/g" in url:
                    listing_queue.append(url)
        sitemap_ids = set(urls)
        if crawl_categories:
            listing_queue.append(BASE + "/ua/product_list")
            while listing_queue:
                url = listing_queue.pop(0)
                if url in visited:
                    continue
                visited.add(url)
                try:
                    response = self.get(url)
                    response.encoding = "utf-8"
                    soup = BeautifulSoup(response.text, "html.parser")
                    # Prom renders pagination links in JavaScript from these attributes.
                    # Following only HTML anchors silently misses later listing pages.
                    for pager in soup.select('[data-pagination-url-pattern][data-pagination-pages-count]'):
                        pattern = pager["data-pagination-url-pattern"]
                        count = int(pager["data-pagination-pages-count"])
                        if not 1 <= count <= 1000 or "page_0" not in pattern:
                            raise ValueError("Unrecognized pagination configuration")
                        for number in range(2, count + 1):
                            link = urljoin(url, pattern.replace("page_0", f"page_{number}"))
                            if urlparse(link).hostname == urlparse(BASE).hostname and link not in visited and link not in listing_queue:
                                listing_queue.append(link)
                    for a in soup.select("a[href]"):
                        link = urljoin(url, a["href"])
                        parsed = urlparse(link)
                        if parsed.hostname != urlparse(BASE).hostname or parsed.fragment or parsed.query:
                            continue
                        match = re.search(r"/ua/p(\d+)-", parsed.path)
                        if match:
                            urls[match[1]] = link
                        elif re.match(r"/ua/(?:g\d+-|product_list)", parsed.path) and link not in visited and link not in listing_queue:
                            listing_queue.append(link)
                    if len(visited) % 10 == 0:
                        print(f"Discovery: {len(visited)} category pages, {len(urls)} unique products", flush=True)
                except Exception as exc:
                    self.report["errors"].append({"phase": "discovery", "url": url, "error": str(exc)})
        inventory = {"captured_at": datetime.now(timezone.utc).isoformat(), "sitemap_product_count": len(sitemap_ids),
                     "category_pages_checked": sorted(visited), "extra_ids_from_categories": sorted(set(urls)-sitemap_ids),
                     "products": urls}
        write(self.root / "source" / "inventory.json", encoded(inventory))
        self.report["sitemap_unique_uk_products"] = len(sitemap_ids)
        self.report["category_pages_checked"] = len(visited)
        self.report["discovered_product_count"] = len(urls)
        return urls


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "mtech-product-archive")
    parser.add_argument("--product-id", action="append", help="Select individual source IDs from the sitemap")
    parser.add_argument("--all", action="store_true", help="Download all public products, checking sitemaps and categories")
    parser.add_argument("--resume", action="store_true", help="Skip complete local products while finishing an interrupted full download")
    parser.add_argument("--use-inventory", action="store_true", help="Resume using the already saved discovery inventory")
    parser.add_argument("--refresh-images", action="store_true", help="Revalidate source images; retain old versions")
    args = parser.parse_args()
    if args.all and args.product_id:
        parser.error("Use --all or --product-id, not both")
    if args.resume and args.refresh_images:
        parser.error("--resume cannot be combined with --refresh-images")
    if args.use_inventory and not (args.all and args.resume):
        parser.error("--use-inventory requires --all --resume")
    archive = Archive(args)
    robots = archive.get(BASE + "/robots.txt").text
    archive.robots = RobotFileParser()
    archive.robots.parse(robots.splitlines())
    write(archive.root / "source" / "robots.txt", robots)
    if args.use_inventory:
        inventory = read_json(archive.root / "source/inventory.json", {})
        urls = inventory.get("products", {})
        if not urls:
            parser.error("Saved inventory is missing or empty")
        archive.report.update(sitemap_unique_uk_products=inventory["sitemap_product_count"],
                              category_pages_checked=len(inventory["category_pages_checked"]),
                              discovered_product_count=len(urls), resumed_inventory_at=inventory["captured_at"])
    else:
        urls = archive.discover(crawl_categories=args.all)
    selected = list(urls) if args.all else args.product_id or list(PILOT)
    archive.report["selected_product_ids"] = selected
    print(f"Downloading {len(selected)} products", flush=True)
    for index, pid in enumerate(selected, 1):
        complete = False
        try:
            current = read_json(archive.root / "products" / pid / "current.json", {}) if args.resume else {}
            assets = current.get("gallery", []) + current.get("description_images", [])
            complete = bool(current and current.get("gallery")
                            and len(current["gallery"]) == current.get("expected_gallery_count")
                            and not current.get("missing_description_images")
                            and all((archive.root / a["path"]).is_file()
                                    and digest((archive.root / a["path"]).read_bytes()) == a["sha256"] for a in assets))
            if complete:
                archive.report["products"].append({"id": pid, "state": "resumed-existing", "photos": len(current["gallery"]),
                    "expected_photos": current["expected_gallery_count"], "specifications": len(current["specifications"])})
            else:
                archive.product(pid, urls[pid], PILOT.get(pid))
            if index % 10 == 0 and not complete:
                archive.export()
        except Exception as exc:
            archive.report["errors"].append({"product_id": pid, "error": str(exc)})
            print(f"FAILED {pid}: {exc}", flush=True)
        if not complete or index % 20 == 0:
            write(archive.root / "reports" / "progress.json", encoded(archive.report))
    archive.export()
    for folder in ["edited", "editorial"]:
        (archive.root / folder).mkdir(parents=True, exist_ok=True)
    archive.report["finished_at"] = datetime.now(timezone.utc).isoformat()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    write(archive.root / "reports" / (stamp + ".json"), encoded(archive.report))
    write(archive.root / "reports" / "latest.json", encoded(archive.report))
    write(archive.root / "reports" / "progress.json", encoded(archive.report))
    print(json.dumps({k:v for k,v in archive.report.items() if k not in {"products", "warnings", "selected_product_ids"}}, ensure_ascii=False, indent=2), flush=True)
    return bool(archive.report["errors"])


if __name__ == "__main__":
    raise SystemExit(main())
