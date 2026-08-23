#!/usr/bin/env python3
"""Contract tests for the ranked opportunity shortlist report."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from canonical.db import CanonicalDB
from scripts.top_opportunities_report import generate

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = PROJECT_ROOT / "canonical" / "schema.sql"


def _seed_database(db: CanonicalDB) -> None:
    """Insert product_candidate entities and commerce metrics for ranking."""
    provenance_id = db.add_provenance(
        source_type="test", source_ref="top-opportunities-report-test"
    )
    candidates = [
        ("Aero Travel Mug", "Supplier A", 0.85, 45.0, 2, 80.0, 30.0),
        ("Heavy Widget", "Supplier B", 0.40, 15.0, 21, 60.0, 90.0),
        ("Foldable Keyboard", "Supplier C", 0.70, 35.0, 7, 55.0, 40.0),
        # Duplicate of Aero Travel Mug on (name, supplier) — must be deduped.
        ("Aero Travel Mug", "Supplier A", 0.90, 50.0, 2, 85.0, 25.0),
    ]
    for name, supplier, viability, margin, shipping, demand, saturation in candidates:
        entity_id = db.insert(
            "entities",
            {
                "entity_type": "product_candidate",
                "name": name,
                "scope": "commerce",
                "status": "candidate",
            },
            provenance_id=provenance_id,
        )
        for metric_name, value in (
            ("commerce.viability_score", viability),
            ("commerce.margin_percent", margin),
            ("commerce.shipping_days", shipping),
            ("commerce.demand_signal", demand),
            ("commerce.saturation_signal", saturation),
        ):
            db.insert(
                "metrics",
                {
                    "metric_name": metric_name,
                    "metric_value": value,
                    "dimensions_json": json.dumps(
                        {"entity_id": entity_id, "supplier": supplier}
                    ),
                    "measured_at": "2026-08-22T00:00:00Z",
                },
                provenance_id=provenance_id,
            )


class TopOpportunitiesReportTests(unittest.TestCase):
    def _database(self) -> tuple[TemporaryDirectory, Path]:
        directory = TemporaryDirectory(ignore_cleanup_errors=True)
        path = Path(directory.name) / "efficiens.db"
        with CanonicalDB(path, schema_path=SCHEMA) as db:
            _seed_database(db)
        return directory, path

    def test_generate_ranks_by_viability_and_dedupes(self) -> None:
        directory, path = self._database()
        self.addCleanup(directory.cleanup)

        rows = generate(path, top_n=10, min_viability=0.0)

        # 4 seeded rows collapse to 3 unique (name, supplier) pairs.
        self.assertEqual(len(rows), 3)
        # Highest viability first.
        self.assertEqual(rows[0]["name"], "Aero Travel Mug")
        self.assertEqual(rows[0]["supplier"], "Supplier A")
        # Dedup keeps one of the two duplicate rows (tie-break is by created_at,
        # which is identical here, so either viability value is valid).
        self.assertIn(rows[0]["viability"], (0.85, 0.90))
        self.assertEqual(rows[1]["name"], "Foldable Keyboard")
        self.assertEqual(rows[2]["name"], "Heavy Widget")

    def test_generate_filters_by_min_viability(self) -> None:
        directory, path = self._database()
        self.addCleanup(directory.cleanup)

        rows = generate(path, top_n=10, min_viability=0.60)

        names = {row["name"] for row in rows}
        self.assertEqual(names, {"Aero Travel Mug", "Foldable Keyboard"})
        self.assertNotIn("Heavy Widget", names)

    def test_generate_respects_top_n(self) -> None:
        directory, path = self._database()
        self.addCleanup(directory.cleanup)

        rows = generate(path, top_n=2, min_viability=0.0)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["name"], "Aero Travel Mug")
        self.assertEqual(rows[1]["name"], "Foldable Keyboard")

    def test_cli_emits_json(self) -> None:
        directory, path = self._database()
        self.addCleanup(directory.cleanup)

        completed = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "top_opportunities_report.py"),
                str(path),
                "--top-n",
                "3",
                "--as-json",
            ],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        rows = json.loads(completed.stdout)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["name"], "Aero Travel Mug")

    def test_cli_help_describes_usage(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "top_opportunities_report.py"),
                "--help",
            ],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("opportunity shortlist", completed.stdout)
        self.assertIn("--top-n", completed.stdout)
        self.assertIn("--min-viability", completed.stdout)


if __name__ == "__main__":
    unittest.main()
