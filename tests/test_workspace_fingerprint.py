"""Tests for deterministic correctness source fingerprints."""
from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.workspace_fingerprint import correctness_snapshot


class WorkspaceFingerprintTests(unittest.TestCase):
    def test_snapshot_changes_when_source_content_changes(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            source = root / "scripts" / "example.py"
            source.write_text("VALUE = 1\n", encoding="utf-8")
            before = correctness_snapshot(root)
            source.write_text("VALUE = 2\n", encoding="utf-8")
            after = correctness_snapshot(root)

        self.assertNotEqual(before["source_fingerprint"], after["source_fingerprint"])
        self.assertEqual(before["source_file_count"], 1)

    def test_snapshot_rejects_linked_code_root(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root.parent / f"{root.name}-outside-scripts"
            outside.mkdir()
            self.addCleanup(lambda: shutil.rmtree(outside, ignore_errors=True))
            (outside / "external.py").write_text("VALUE = 1\n", encoding="utf-8")
            linked = root / "scripts"
            try:
                if os.name == "nt":
                    completed = subprocess.run(
                        ["cmd.exe", "/c", "mklink", "/J", str(linked), str(outside)],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if completed.returncode != 0:
                        self.skipTest(f"junction unavailable: {completed.stderr}")
                    self.addCleanup(
                        lambda: subprocess.run(
                            ["cmd.exe", "/c", "rmdir", str(linked)],
                            capture_output=True,
                            timeout=30,
                        )
                    )
                else:
                    linked.symlink_to(outside, target_is_directory=True)
                    self.addCleanup(linked.unlink)
            except OSError as exc:
                self.skipTest(f"directory link unavailable: {exc}")

            with self.assertRaisesRegex(ValueError, "linked or reparse"):
                correctness_snapshot(root)


if __name__ == "__main__":
    unittest.main()
