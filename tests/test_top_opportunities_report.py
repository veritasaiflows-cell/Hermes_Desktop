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


def _mark_candidate_quality(
    db: CanonicalDB,
    *,
    name: str,
    quality_status: str,
    strategic_fit_score: float = 82.0,
) -> None:
    entity_id = db.connection.execute(
        "SELECT entity_id FROM entities WHERE entity_type = ? AND name = ? ORDER BY rowid DESC LIMIT 1",
        ("product_candidate", name),
    ).fetchone()[0]
    db.insert(
        "events",
        {
            "event_type": "workflow.product_research.candidate_selected",
            "subject_type": "entities",
            "subject_id": entity_id,
            "payload_json": json.dumps(
                {
                    "quality_status": quality_status,
                    "qualification_mode": "organic_sample",
                    "strategic_fit": {
                        "score": strategic_fit_score,
                        "thesis_id": "creator-desk-reset",
                    },
                },
                sort_keys=True,
            ),
            "occurred_at": "2026-08-29T00:00:00Z",
            "recorded_at": "2026-08-29T00:00:00Z",
        },
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

        rows = generate(path, top_n=10, min_viability=0.0, include_unverified=True)

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

        rows = generate(path, top_n=10, min_viability=0.60, include_unverified=True)

        names = {row["name"] for row in rows}
        self.assertEqual(names, {"Aero Travel Mug", "Foldable Keyboard"})
        self.assertNotIn("Heavy Widget", names)

    def test_generate_respects_top_n(self) -> None:
        directory, path = self._database()
        self.addCleanup(directory.cleanup)

        rows = generate(path, top_n=2, min_viability=0.0, include_unverified=True)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["name"], "Aero Travel Mug")
        self.assertEqual(rows[1]["name"], "Foldable Keyboard")

    def test_generate_excludes_unverified_candidates_by_default(self) -> None:
        directory, path = self._database()
        self.addCleanup(directory.cleanup)

        self.assertEqual(generate(path, top_n=10, min_viability=0.0), [])

    def test_generate_includes_only_candidates_with_production_quality_evidence(self) -> None:
        directory, path = self._database()
        self.addCleanup(directory.cleanup)
        with CanonicalDB(path, schema_path=SCHEMA) as db:
            _mark_candidate_quality(
                db,
                name="Foldable Keyboard",
                quality_status="organic_sample_candidate",
            )

        rows = generate(path, top_n=10, min_viability=0.0)

        self.assertEqual([row["name"] for row in rows], ["Foldable Keyboard"])
        self.assertEqual(rows[0]["quality_status"], "organic_sample_candidate")
        self.assertEqual(rows[0]["strategic_fit_score"], 82.0)
        self.assertEqual(rows[0]["thesis_id"], "creator-desk-reset")

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
                "--include-unverified",
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
        self.assertIn("--include-unverified", completed.stdout)


if __name__ == "__main__":
    unittest.main()
