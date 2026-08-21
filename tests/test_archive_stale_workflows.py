"""Tests for the read-only stale-workflow archive check."""
from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts import cron_archive_stale_workflows


class ArchiveStaleWorkflowTests(unittest.TestCase):
    def test_check_only_reports_stale_capsule_without_moving_it(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / "state"
            capsule_dir = state_dir / "workflows"
            archive_dir = state_dir / "archive" / "workflows"
            capsule_dir.mkdir(parents=True)
            (state_dir / "active_workflows.json").write_text(
                json.dumps(
                    {
                        "workflows": [
                            {
                                "workflow_id": "WF-OLD",
                                "lifecycle": "closed",
                                "updated_at": "2000-01-01T00:00:00Z",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            capsule = capsule_dir / "WF-OLD.json"
            capsule.write_text("{}\n", encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch.object(cron_archive_stale_workflows, "DEFAULT_STATE_DIR", state_dir),
                patch.object(cron_archive_stale_workflows, "DEFAULT_CAPSULE_DIR", capsule_dir),
                patch.object(cron_archive_stale_workflows, "DEFAULT_ARCHIVE_DIR", archive_dir),
                patch.object(
                    cron_archive_stale_workflows,
                    "datetime",
                    wraps=datetime,
                ),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                cron_archive_stale_workflows.datetime.now.return_value = datetime(
                    2026, 8, 20, tzinfo=timezone.utc
                )
                code = cron_archive_stale_workflows.main(check_only=True)

            self.assertEqual(code, 1)
            self.assertTrue(capsule.exists())
            self.assertFalse(archive_dir.exists())
            self.assertIn('"archived_capsules": []', stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
