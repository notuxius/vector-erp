"""Focused checks for the archive's no-overwrite and resume guarantees."""
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from extract_mtech_sample import Archive, digest, category_from_breadcrumbs


def photo(color):
    stream = io.BytesIO()
    Image.new("RGB", (20, 20), color).save(stream, format="PNG")
    return stream.getvalue()


class ArchiveSafetyTests(unittest.TestCase):
    def test_category_uses_recognized_parent_for_nested_groups(self):
        crumbs = [{"url": "https://mtech-cnc.com.ua/ua/g134852871-shvp-vinty-gajki"},
                  {"url": "https://mtech-cnc.com.ua/ua/g134871409-komplekty-shvp-vint"},
                  {"url": "https://mtech-cnc.com.ua/ua/p123-example.html"}]
        self.assertEqual(category_from_breadcrumbs(crumbs), "ball-screws")
        self.assertIsNone(category_from_breadcrumbs([{"url": "/ua/g999-unknown"}]))

    def test_discovery_combines_sitemaps_categories_and_language_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(SimpleNamespace(output=Path(directory), refresh_images=False))
            base = "https://mtech-cnc.com.ua"
            def xml(root, urls):
                return '<'+root+' xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'+''.join('<url><loc>'+u+'</loc></url>' for u in urls)+'</'+root+'>'
            pages = {
                base+"/sitemap.xml": xml("sitemapindex", [base+"/sitemap_products-0.xml", base+"/sitemap_products-1.xml", base+"/sitemap_groups-0.xml"]),
                base+"/sitemap_products-0.xml": xml("urlset", [base+"/p123-one.html", base+"/ua/p123-one.html"]),
                base+"/sitemap_products-1.xml": xml("urlset", [base+"/ua/p456-two.html"]),
                base+"/sitemap_groups-0.xml": xml("urlset", [base+"/ua/g1-group"]),
                base+"/ua/g1-group": '<a href="/ua/p789-extra.html">extra</a><div data-pagination-pages-count="2" data-pagination-url-pattern="/ua/g1-group/page_0"></div><a href="https://unrelated.example/ua/p999-test.html">offsite</a>',
                base+"/ua/g1-group/page_2": '<a href="/ua/p234-another.html">another</a><a href="/ua/g1-group">back</a>',
                base+"/ua/product_list": '<a href="/ua/g1-group">group</a>',
            }
            archive.get = lambda url: SimpleNamespace(content=pages[url].encode(), text=pages[url])
            discovered = archive.discover(crawl_categories=True)
            self.assertEqual(set(discovered), {"123", "456", "789", "234"})
            self.assertEqual(archive.report["sitemap_unique_uk_products"], 2)
            self.assertEqual(archive.report["category_pages_checked"], 3)
            self.assertFalse(archive.report["errors"])

    def test_reuse_and_source_change_preserve_original_and_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = Archive(SimpleNamespace(output=root, refresh_images=False))
            first = photo("red")
            archive.get = lambda *args: SimpleNamespace(status_code=200, content=first, headers={})
            original = archive.asset("123", "https://images.prom.ua/456_w40_h40_test.png", "456")
            edit = root / "edited" / "123" / "456.png"
            edit.parent.mkdir(parents=True)
            edit.write_bytes(photo("green"))
            edit_hash = digest(edit.read_bytes())
            def no_network(*args):
                raise AssertionError("Unchanged image should be reused without a request")
            archive.get = no_network
            reused = archive.asset("123", "https://images.prom.ua/456_w40_h40_test.png", "456")
            self.assertEqual(reused["sha256"], original["sha256"])
            archive.refresh = True
            archive.get = lambda *args: SimpleNamespace(status_code=200, content=photo("blue"), headers={})
            changed = archive.asset("123", "https://images.prom.ua/456_w40_h40_test.png", "456")
            self.assertNotEqual(changed["path"], original["path"])
            self.assertEqual((root / original["path"]).read_bytes(), first)
            self.assertEqual(digest(edit.read_bytes()), edit_hash)
            self.assertEqual(len(archive.report["warnings"]), 1)

    def test_unchanged_revalidation_preserves_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(SimpleNamespace(output=Path(directory), refresh_images=False))
            archive.get = lambda *args: SimpleNamespace(status_code=200, content=photo("red"), headers={})
            first = archive.asset("123", "https://images.prom.ua/456_test.png", "456")
            archive.refresh = True
            second = archive.asset("123", "https://images.prom.ua/456_test.png", "456")
            self.assertEqual(first, second)
            archive.get = lambda *args: SimpleNamespace(status_code=304)
            self.assertEqual(second, archive.asset("123", "https://images.prom.ua/456_test.png", "456"))


if __name__ == "__main__":
    unittest.main()
