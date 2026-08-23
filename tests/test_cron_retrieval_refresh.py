#!/usr/bin/env python3
"""Contract tests for the A12 retrieval-refresh cron wrapper."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts import cron_retrieval_refresh


class RetrievalRefreshCronTests(unittest.TestCase):
    def test_green_when_no_skips_or_errors(self) -> None:
        summary = {
            "source_count": 2,
            "workspace_index": {"skipped_files": 0},
            "vector_index": {"skipped_files": 0, "embedding_errors": 0},
        }
        with (
            patch.object(
                cron_retrieval_refresh, "refresh_indexes", return_value=summary
            ),
            patch.object(
                cron_retrieval_refresh,
                "refresh_routing",
                return_value=0,
                create=True,
            ) as routing_mock,
        ):
            self.assertEqual(cron_retrieval_refresh.main(), 0)
        routing_mock.assert_called_once_with()

    def test_degraded_when_files_skipped(self) -> None:
        summary = {
            "source_count": 2,
            "workspace_index": {"skipped_files": 1},
            "vector_index": {"skipped_files": 0, "embedding_errors": 0},
        }
        with (
            patch.object(
                cron_retrieval_refresh, "refresh_indexes", return_value=summary
            ),
            patch.object(
                cron_retrieval_refresh,
                "refresh_routing",
                return_value=0,
            ) as routing_mock,
        ):
            self.assertEqual(cron_retrieval_refresh.main(), 1)
        routing_mock.assert_called_once_with()

    def test_fail_when_routing_sync_fails(self) -> None:
        summary = {
            "source_count": 2,
            "workspace_index": {"skipped_files": 0},
            "vector_index": {"skipped_files": 0, "embedding_errors": 0},
        }
        with (
            patch.object(
                cron_retrieval_refresh, "refresh_indexes", return_value=summary
            ),
            patch.object(
                cron_retrieval_refresh,
                "refresh_routing",
                return_value=1,
            ),
        ):
            self.assertEqual(cron_retrieval_refresh.main(), 1)

    def test_fail_when_routing_sync_raises(self) -> None:
        summary = {
            "source_count": 2,
            "workspace_index": {"skipped_files": 0},
            "vector_index": {"skipped_files": 0, "embedding_errors": 0},
        }
        with (
            patch.object(
                cron_retrieval_refresh, "refresh_indexes", return_value=summary
            ),
            patch.object(
                cron_retrieval_refresh,
                "refresh_routing",
                side_effect=RuntimeError("boom"),
            ),
        ):
            self.assertEqual(cron_retrieval_refresh.main(), 1)

    def test_fail_when_refresh_raises(self) -> None:
        with (
            patch.object(
                cron_retrieval_refresh,
                "refresh_indexes",
                side_effect=RuntimeError("boom"),
            ),
            patch.object(
                cron_retrieval_refresh,
                "refresh_routing",
                return_value=0,
            ) as routing_mock,
        ):
            self.assertEqual(cron_retrieval_refresh.main(), 1)
        routing_mock.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
