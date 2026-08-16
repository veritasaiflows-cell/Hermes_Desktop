"""Tests for cron_queue_hygiene (A7) and cron_test_gate (A2-full)."""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts import cron_queue_hygiene, cron_test_gate


class QueueHygieneTests(unittest.TestCase):
    def _build_active(self, root: Path, workflows: list[dict]) -> None:
        active_path = root / "state" / "active_workflows.json"
        active_path.parent.mkdir(parents=True, exist_ok=True)
        active_path.write_text(
            json.dumps(
                {
                    "schema": "active-workflows.v1",
                    "workflows": workflows,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def test_no_terminal_workflows_stays_green(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._build_active(
                root,
                [
                    {
                        "workflow_id": "WF-1000",
                        "display_name": "Active WF",
                        "lifecycle": "active",
                        "updated_at": "2026-08-16T00:00:00Z",
                    }
                ],
            )
            with patch.object(cron_queue_hygiene, "DEFAULT_STATE_DIR", root / "state"):
                with patch.object(cron_queue_hygiene, "DEFAULT_ARCHIVE_DIR", root / "state" / "archive" / "active_workflows"):
                    rc = cron_queue_hygiene.main()
            self.assertEqual(rc, 0)
            active = json.loads((root / "state" / "active_workflows.json").read_text(encoding="utf-8"))
            self.assertEqual(len(active["workflows"]), 1)

    def test_terminal_workflow_older_than_threshold_is_archived(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._build_active(
                root,
                [
                    {
                        "workflow_id": "WF-1000",
                        "display_name": "Active WF",
                        "lifecycle": "active",
                        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                    {
                        "workflow_id": "WF-999",
                        "display_name": "Old closed WF",
                        "lifecycle": "closed",
                        "updated_at": "2026-07-01T00:00:00Z",
                    },
                ],
            )
            with patch.object(cron_queue_hygiene, "DEFAULT_STATE_DIR", root / "state"):
                with patch.object(cron_queue_hygiene, "DEFAULT_ARCHIVE_DIR", root / "state" / "archive" / "active_workflows"):
                    rc = cron_queue_hygiene.main()
            self.assertEqual(rc, 1)
            active = json.loads((root / "state" / "active_workflows.json").read_text(encoding="utf-8"))
            self.assertEqual(len(active["workflows"]), 1)
            self.assertEqual(active["workflows"][0]["workflow_id"], "WF-1000")

            archive_dir = root / "state" / "archive" / "active_workflows"
            self.assertTrue(archive_dir.exists())
            archive_files = list(archive_dir.glob("*.json"))
            self.assertEqual(len(archive_files), 1)
            archived = json.loads(archive_files[0].read_text(encoding="utf-8"))
            self.assertEqual(len(archived["removed_workflows"]), 1)
            self.assertEqual(archived["removed_workflows"][0]["workflow_id"], "WF-999")


class TestGateWrapperTests(unittest.TestCase):
    def test_green_when_run_checks_exits_zero(self):
        fake = subprocess_result(returncode=0, stdout="Ran 87 tests\nOK", stderr="")
        with patch.object(cron_test_gate.subprocess, "run", return_value=fake):
            with patch.object(cron_test_gate.sys, "stderr"):
                rc = cron_test_gate.main()
        self.assertEqual(rc, 0)

    def test_fail_when_run_checks_exits_nonzero(self):
        fake = subprocess_result(returncode=1, stdout="", stderr="Traceback...")
        with patch.object(cron_test_gate.subprocess, "run", return_value=fake):
            with patch.object(cron_test_gate.sys, "stdout"):
                with patch.object(cron_test_gate.sys, "stderr"):
                    rc = cron_test_gate.main()
        self.assertEqual(rc, 1)


def subprocess_result(*, returncode, stdout, stderr=""):
    """Build a minimal stand-in for subprocess.CompletedProcess."""
    from types import SimpleNamespace

    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


if __name__ == "__main__":
    unittest.main()
