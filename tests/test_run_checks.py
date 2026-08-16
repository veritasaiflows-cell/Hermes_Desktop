from pathlib import Path
from tempfile import TemporaryDirectory
import os
import unittest

from scripts import run_checks


class RunChecksTests(unittest.TestCase):
    def test_test_discovery_is_rooted_at_the_project_when_cwd_changes(self):
        original_cwd = Path.cwd()
        with TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                suite = run_checks.build_test_suite()
            finally:
                os.chdir(original_cwd)

        self.assertGreater(suite.countTestCases(), 0)

    def test_isolated_smoke_uses_temporary_database_and_control_plane(self):
        result = run_checks.run_isolated_smoke()

        self.assertGreaterEqual(result["smoke"]["delta_entities"], 1)
        self.assertGreaterEqual(result["smoke"]["delta_metrics"], 1)
        self.assertGreaterEqual(result["smoke"]["delta_events"], 1)
        self.assertFalse(result["routing"]["routing"]["routing_index_stale"])
        self.assertTrue(result["database"].endswith("efficiens.db"))


if __name__ == "__main__":
    unittest.main()
