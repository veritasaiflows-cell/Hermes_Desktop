#!/usr/bin/env python3
"""Contract tests for the script-documentation validator."""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.script_doc_validator import validate_script_docs


class ScriptDocValidatorTests(unittest.TestCase):
    def _workspace(self) -> tuple[TemporaryDirectory, Path]:
        directory = TemporaryDirectory()
        root = Path(directory.name)
        (root / "scripts").mkdir()
        (root / "tests").mkdir()
        return directory, root

    def test_clean_script_passes(self) -> None:
        directory, root = self._workspace()
        self.addCleanup(directory.cleanup)
        (root / "scripts" / "good_script.py").write_text(
            '"""A well-documented script that does something useful."""\n\n'
            "def main() -> int:\n"
            '    """Run the thing."""\n'
            "    return 0\n",
            encoding="utf-8",
        )
        (root / "tests" / "test_good_script.py").write_text(
            "from scripts.good_script import main\n", encoding="utf-8"
        )
        (root / "README.md").write_text(
            "# docs\ngood_script.py is documented here.\n", encoding="utf-8"
        )

        report = validate_script_docs(root)

        self.assertTrue(report["ok"], report["issues"])

    def test_undocumented_script_fails(self) -> None:
        directory, root = self._workspace()
        self.addCleanup(directory.cleanup)
        (root / "scripts" / "bad_script.py").write_text(
            "def main():\n    return 0\n", encoding="utf-8"
        )

        report = validate_script_docs(root)

        self.assertFalse(report["ok"])
        codes = {issue["code"] for issue in report["issues"]}
        self.assertIn("module_docstring_missing", codes)
        self.assertIn("function_docstrings_missing", codes)
        self.assertIn("test_anchor_missing", codes)
        self.assertIn("reference_mention_missing", codes)

    def test_missing_scripts_dir_fails(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            report = validate_script_docs(root)
            self.assertFalse(report["ok"])
            self.assertEqual(report["issues"][0]["code"], "scripts_dir_missing")


if __name__ == "__main__":
    unittest.main()
