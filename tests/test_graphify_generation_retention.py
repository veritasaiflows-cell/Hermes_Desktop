"""Retention tests for immutable Graphify generations and their source snapshots."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


def _artifact(root: Path, name: str) -> Path:
    artifact = root / "candidates" / name / "graphify-out"
    artifact.mkdir(parents=True)
    (artifact / "graph.json").write_text(json.dumps({"nodes": [], "links": [], "n": name}), encoding="utf-8")
    (artifact / "manifest.json").write_text("{}\n", encoding="utf-8")
    (artifact / "freshness-baseline.json").write_text(
        json.dumps(
            {
                "schema": "graphify-freshness-baseline.v1",
                "graph_sha256": "a" * 64,
                "manifest_sha256": "b" * 64,
                "source_hashes": {},
            }
        ),
        encoding="utf-8",
    )
    return artifact


def _publish(root: Path, generation_id: str) -> None:
    from scripts import graphify_generation

    snapshot = root / "source" / "graphify-candidates" / generation_id
    (snapshot / "scripts").mkdir(parents=True)
    (snapshot / "scripts" / "example.py").write_text("x = 1\n", encoding="utf-8")
    graphify_generation.publish_generation(
        root,
        _artifact(root, generation_id),
        generation_id=generation_id,
        source_fingerprint={"scripts/example.py": "c" * 64},
        snapshot_path=f"source/graphify-candidates/{generation_id}",
    )


def _generations(root: Path) -> list[str]:
    return sorted(p.name for p in (root / "graphify-out" / "generations").iterdir())


def _snapshots(root: Path) -> list[str]:
    return sorted(p.name for p in (root / "source" / "graphify-candidates").iterdir())


class GenerationRetentionTests(unittest.TestCase):
    IDS = ["g-20260101T000000Z-a", "g-20260102T000000Z-b", "g-20260103T000000Z-c",
           "g-20260104T000000Z-d", "g-20260105T000000Z-e"]

    def test_keeps_newest_n_and_removes_their_snapshots(self) -> None:
        from scripts import graphify_generation

        with TemporaryDirectory() as directory:
            root = Path(directory)
            for gid in self.IDS:
                _publish(root, gid)
            report = graphify_generation.prune_generations(root, keep=3)
            self.assertEqual(report["removed_generations"], self.IDS[:2])
            self.assertEqual(_generations(root), self.IDS[2:])
            self.assertEqual(_snapshots(root), self.IDS[2:])
            self.assertEqual(
                graphify_generation.resolve_current_generation(root).generation_id, self.IDS[-1]
            )

    def test_selected_generation_is_never_removed_even_when_oldest(self) -> None:
        from scripts import graphify_generation

        with TemporaryDirectory() as directory:
            root = Path(directory)
            _publish(root, self.IDS[0])
            pointer = graphify_generation.current_pointer_bytes(root)
            for gid in self.IDS[1:]:
                _publish(root, gid)
            graphify_generation.restore_current_pointer(root, pointer)
            report = graphify_generation.prune_generations(root, keep=2)
            kept = _generations(root)
            self.assertIn(self.IDS[0], kept)
            self.assertEqual(kept, [self.IDS[0], *self.IDS[-2:]])
            self.assertNotIn(self.IDS[0], report["removed_generations"])
            self.assertEqual(
                graphify_generation.resolve_current_generation(root).generation_id, self.IDS[0]
            )

    def test_dry_run_removes_nothing(self) -> None:
        from scripts import graphify_generation

        with TemporaryDirectory() as directory:
            root = Path(directory)
            for gid in self.IDS:
                _publish(root, gid)
            report = graphify_generation.prune_generations(root, keep=3, dry_run=True)
            self.assertEqual(report["removed_generations"], self.IDS[:2])
            self.assertEqual(_generations(root), self.IDS)
            self.assertEqual(_snapshots(root), self.IDS)

    def test_keep_below_two_is_rejected_to_preserve_rollback(self) -> None:
        from scripts import graphify_generation

        with TemporaryDirectory() as directory:
            root = Path(directory)
            _publish(root, self.IDS[0])
            with self.assertRaises(graphify_generation.GraphifyGenerationError):
                graphify_generation.prune_generations(root, keep=1)

    def test_invalid_pointer_fails_closed_without_deleting(self) -> None:
        from scripts import graphify_generation

        with TemporaryDirectory() as directory:
            root = Path(directory)
            for gid in self.IDS:
                _publish(root, gid)
            (root / "graphify-out" / "current-generation.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(graphify_generation.GraphifyGenerationError):
                graphify_generation.prune_generations(root, keep=3)
            self.assertEqual(_generations(root), self.IDS)

    def test_snapshot_path_outside_candidates_dir_is_not_deleted(self) -> None:
        from scripts import graphify_generation

        with TemporaryDirectory() as directory:
            root = Path(directory)
            precious = root / "scripts"
            precious.mkdir()
            (precious / "keep.py").write_text("x = 1\n", encoding="utf-8")
            graphify_generation.publish_generation(
                root,
                _artifact(root, "g-20260101T000000Z-x"),
                generation_id="g-20260101T000000Z-x",
                source_fingerprint={"scripts/keep.py": "c" * 64},
                snapshot_path="scripts",
            )
            for gid in self.IDS[1:4]:
                _publish(root, gid)
            graphify_generation.prune_generations(root, keep=3)
            self.assertTrue((precious / "keep.py").is_file())
            self.assertNotIn("g-20260101T000000Z-x", _generations(root))

    def test_snapshot_shared_with_retained_generation_is_kept(self) -> None:
        from scripts import graphify_generation

        with TemporaryDirectory() as directory:
            root = Path(directory)
            shared = root / "source" / "graphify-candidates" / "shared"
            (shared / "scripts").mkdir(parents=True)
            (shared / "scripts" / "example.py").write_text("x = 1\n", encoding="utf-8")
            for gid in self.IDS[:3]:
                graphify_generation.publish_generation(
                    root,
                    _artifact(root, gid),
                    generation_id=gid,
                    source_fingerprint={"scripts/example.py": "c" * 64},
                    snapshot_path="source/graphify-candidates/shared",
                )
            report = graphify_generation.prune_generations(root, keep=2)
            self.assertEqual(report["removed_generations"], self.IDS[:1])
            self.assertEqual(report["removed_snapshots"], [])
            self.assertTrue((shared / "scripts" / "example.py").is_file())


if __name__ == "__main__":
    unittest.main()
