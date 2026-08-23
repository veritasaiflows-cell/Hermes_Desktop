#!/usr/bin/env python3
"""Contract tests for the A6 alias dead-target sweep."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts import cron_alias_sweep


class AliasSweepTests(unittest.TestCase):
    def _state(self, root: Path, aliases: dict, workflows: dict) -> None:
        state_dir = root / "state"
        state_dir.mkdir(parents=True)
        (state_dir / "workflow_alias_index.json").write_text(
            json.dumps({"aliases": aliases}), encoding="utf-8"
        )
        (state_dir / "active_workflows.json").write_text(
            json.dumps({"workflows": workflows}), encoding="utf-8"
        )

    def test_green_when_all_aliases_resolve(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._state(
                root,
                aliases={"wf-a": "WF-1000"},
                workflows={"WF-1000": {"workflow_id": "WF-1000"}},
            )
            with patch.object(cron_alias_sweep, "DEFAULT_STATE_DIR", root / "state"):
                self.assertEqual(cron_alias_sweep.main(), 0)

    def test_degraded_when_alias_points_to_missing_workflow(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._state(
                root,
                aliases={"wf-a": "WF-9999"},
                workflows={"WF-1000": {"workflow_id": "WF-1000"}},
            )
            with patch.object(cron_alias_sweep, "DEFAULT_STATE_DIR", root / "state"):
                self.assertEqual(cron_alias_sweep.main(), 1)

    def test_green_when_sources_missing(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(cron_alias_sweep, "DEFAULT_STATE_DIR", root / "state"):
                self.assertEqual(cron_alias_sweep.main(), 0)


if __name__ == "__main__":
    unittest.main()
