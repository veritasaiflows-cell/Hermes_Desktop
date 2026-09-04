"""Pin pytest collection to exclude frozen canary fixture packs.

The implementer/researcher canary packs under ``tests/fixtures/`` contain
intentionally failing (RED) executable repos for candidate-bot qualification.
They must never be collected by the workspace suite: unittest discovery
already skips them, and pytest must too, otherwise the shared correctness
gate fails on a frozen broken fixture.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PytestCollectionContractTests(unittest.TestCase):
    def test_pytest_does_not_collect_frozen_canary_fixture_packs(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "tests", "-q", "--collect-only"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=240,
        )
        collected = completed.stdout.splitlines()
        fixture_paths = [
            line
            for line in collected
            if line.strip().startswith("tests/fixtures")
            and "::" in line
        ]
        self.assertEqual(
            fixture_paths,
            [],
            "pytest must not collect frozen canary fixture packs; "
            f"add norecursedirs to pytest.ini. Collected: {fixture_paths[:5]}",
        )


if __name__ == "__main__":
    unittest.main()