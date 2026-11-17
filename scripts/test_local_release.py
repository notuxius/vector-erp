import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import local_release as release


class LocalReleaseTests(unittest.TestCase):
    def test_only_deployable_changes_trigger_a_build(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(release, "ROOT", Path(tmp)):
            root = release.ROOT
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            app = root / "apps/warehouse_core"
            app.mkdir(parents=True)
            source = app / "sample.py"
            source.write_text("v1")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            before = release.fingerprint()
            (root / ".gitignore").write_text("*.pyc\n")
            (app / "sample.pyc").write_bytes(b"cache")
            (root / "README.md").write_text("docs")
            self.assertEqual(before, release.fingerprint())
            new = app / "new.py"
            new.touch()
            self.assertNotEqual(before, release.fingerprint())
            new.unlink()
            source.write_text("v2")
            self.assertNotEqual(before, release.fingerprint())
            source.unlink()
            self.assertNotEqual(before, release.fingerprint())

    def test_pack_refuses_changed_sources_or_another_release_before_export(self):
        record = {"source": "accepted", "images": {}}
        with tempfile.TemporaryDirectory() as tmp, patch.object(release, "READY", Path(tmp) / "ready.json"):
            release.READY.write_text(json.dumps(record))
            for approved, current in [(release.release_id(record), "edited"), ("other", "accepted")]:
                with patch.object(release, "fingerprint", return_value=current), self.assertRaises(RuntimeError):
                    release.pack(approved)


if __name__ == "__main__":
    unittest.main()
