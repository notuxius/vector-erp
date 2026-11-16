import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from . import pages_deploy


class PagesDeployTests(unittest.TestCase):
    def test_paginates_and_counts_all_projects_in_current_month(self):
        calls = []

        def api(path):
            calls.append(path)
            if '/deployments' not in path:
                return [{'name': 'a'}, {'name': 'b'}]
            if 'page=1' in path:
                return [{'created_on': '2026-10-31T00:00:00Z'}] * 25
            return [{'created_on': '2026-10-01T00:00:00Z'}, {'created_on': '2026-09-30T23:59:59Z'}]

        self.assertEqual(pages_deploy.monthly_deployments(api, datetime(2026, 10, 31, tzinfo=timezone.utc)), 52)
        self.assertEqual(len(calls), 5)

    def test_threshold_and_api_failure_block_deployment_and_release_lock(self):
        for result in [475, RuntimeError('API unavailable')]:
            with tempfile.TemporaryDirectory() as directory, patch.object(pages_deploy, 'ROOT', Path(directory)), \
                    patch('sys.argv', ['pages_deploy', 'mtech-design-preview']), \
                    patch.object(pages_deploy, 'monthly_deployments', side_effect=result if isinstance(result, Exception) else None, return_value=result), \
                    patch.object(pages_deploy.subprocess, 'run') as run:
                with self.assertRaises(RuntimeError):
                    pages_deploy.main()
                run.assert_not_called()
                self.assertFalse((Path(directory) / '.artifacts/pages-deploy.lock').exists())

    def test_existing_lock_is_not_removed(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(pages_deploy, 'ROOT', Path(directory)), \
                patch('sys.argv', ['pages_deploy', 'mtech-design-preview']):
            lock = Path(directory) / '.artifacts/pages-deploy.lock'
            lock.parent.mkdir()
            lock.write_text('other process')
            with self.assertRaises(RuntimeError):
                pages_deploy.main()
            self.assertTrue(lock.exists())


if __name__ == '__main__':
    unittest.main()
