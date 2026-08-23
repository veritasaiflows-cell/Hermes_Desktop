#!/usr/bin/env python3
"""Contract tests for the A3 routing-cache hygiene sweep."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from scripts import cron_routing_cache_sweep


class RoutingCacheSweepTests(unittest.TestCase):
    def test_green_when_database_absent(self) -> None:
        with patch.object(
            cron_routing_cache_sweep, "DEFAULT_DATABASE", MagicMock(exists=lambda: False)
        ):
            self.assertEqual(cron_routing_cache_sweep.main(), 0)

    def test_evicts_expired_and_mismatched_rows(self) -> None:
        context = MagicMock()
        context.__enter__.return_value.clear_expired_routing_cache.return_value = 3
        context.__enter__.return_value.delete_routing_cache_with_mismatched_signatures.return_value = 2
        context.__enter__.return_value.connection.execute.return_value.fetchone.return_value = [5]
        with patch.object(
            cron_routing_cache_sweep, "DEFAULT_DATABASE", MagicMock(exists=lambda: True)
        ):
            with patch.object(cron_routing_cache_sweep, "CanonicalDB", return_value=context):
                self.assertEqual(cron_routing_cache_sweep.main(), 0)

    def test_fail_when_database_raises(self) -> None:
        context = MagicMock()
        context.__enter__.side_effect = RuntimeError("corrupt")
        with patch.object(
            cron_routing_cache_sweep, "DEFAULT_DATABASE", MagicMock(exists=lambda: True)
        ):
            with patch.object(cron_routing_cache_sweep, "CanonicalDB", return_value=context):
                self.assertEqual(cron_routing_cache_sweep.main(), 1)


if __name__ == "__main__":
    unittest.main()
