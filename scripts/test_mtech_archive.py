"""Focused checks for the archive's no-overwrite and resume guarantees."""
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from extract_mtech_sample import Archive, digest


def photo(color):
    stream = io.BytesIO()
    Image.new("RGB", (20, 20), color).save(stream, format="PNG")
    return stream.getvalue()


class ArchiveSafetyTests(unittest.TestCase):
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
