"""Regression tests for Graphify immutable-generation publication."""
from __future__ import annotations

import json
import shutil
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class GraphifyGenerationPublicationTests(unittest.TestCase):
    def _artifact(self, root: Path) -> Path:
        artifact = root / "candidate" / "graphify-out"
        artifact.mkdir(parents=True)
        (artifact / "graph.json").write_text(
            json.dumps({"nodes": [], "links": []}) + "\n",
            encoding="utf-8",
        )
        (artifact / "manifest.json").write_text("{}\n", encoding="utf-8")
        (artifact / "freshness-baseline.json").write_text(
            json.dumps(
                {
                    "schema": "graphify-freshness-baseline.v1",
                    "graph_sha256": "a" * 64,
                    "manifest_sha256": "b" * 64,
                    "source_hashes": {},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return artifact

    def test_publish_selects_complete_generation_without_mutating_legacy_graph(self) -> None:
        from scripts import graphify_generation

        with TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "graphify-out"
            legacy.mkdir()
            legacy_graph = legacy / "graph.json"
            legacy_graph.write_text('{"legacy": true}\n', encoding="utf-8")
            before = legacy_graph.read_bytes()

            selected = graphify_generation.publish_generation(
                root,
                self._artifact(root),
                generation_id="g-001",
                source_fingerprint={"scripts/example.py": "c" * 64},
                snapshot_path="source/graphify-candidates/g-001",
            )

            self.assertEqual(legacy_graph.read_bytes(), before)
            self.assertEqual(selected.generation_id, "g-001")
            self.assertEqual(
                selected.graph_path,
                root / "graphify-out" / "generations" / "g-001" / "graph.json",
            )
            self.assertTrue(selected.graph_path.is_file())
            self.assertFalse(selected.graph_path.stat().st_mode & stat.S_IWRITE)
            pointer = json.loads((legacy / "current-generation.json").read_text(encoding="utf-8"))
            self.assertEqual(pointer["generation_id"], "g-001")
            self.assertEqual(pointer["artifact_tree_sha256"], selected.artifact_tree_sha256)
            self.assertEqual(graphify_generation.resolve_current_generation(root), selected)

    def test_pointer_restore_recovers_the_previous_complete_generation(self) -> None:
        from scripts import graphify_generation

        with TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "graphify-out"
            legacy.mkdir()
            first_candidate = self._artifact(root)
            first = graphify_generation.publish_generation(
                root,
                first_candidate,
                generation_id="g-001",
                source_fingerprint={"scripts/example.py": "a" * 64},
                snapshot_path="source/graphify-candidates/g-001",
            )
            previous_pointer = graphify_generation.current_pointer_bytes(root)
            second_candidate = root / "second-candidate"
            shutil.copytree(first_candidate, second_candidate)
            (second_candidate / "graph.json").write_text('{"version": 2}\n', encoding="utf-8")
            graphify_generation.publish_generation(
                root,
                second_candidate,
                generation_id="g-002",
                source_fingerprint={"scripts/example.py": "b" * 64},
                snapshot_path="source/graphify-candidates/g-002",
            )

            graphify_generation.restore_current_pointer(root, previous_pointer)

            self.assertEqual(graphify_generation.resolve_current_generation(root), first)


class GraphifySourceSnapshotTests(unittest.TestCase):
    def test_source_snapshot_is_immutable_and_detects_live_source_drift(self) -> None:
        from scripts import cron_graphify_code_refresh

        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("canonical", "scripts", "tests"):
                (root / name).mkdir(parents=True)
            source = root / "scripts" / "example.py"
            source.write_text("VALUE = 1\n", encoding="utf-8")

            snapshot = cron_graphify_code_refresh.create_source_snapshot(root, "g-001")

            self.assertEqual(
                snapshot.workspace_root,
                root / "source" / "graphify-candidates" / "g-001" / "workspace",
            )
            self.assertEqual(
                (snapshot.workspace_root / "scripts" / "example.py").read_text(encoding="utf-8"),
                "VALUE = 1\n",
            )
            self.assertTrue(cron_graphify_code_refresh.snapshot_matches_live_source(root, snapshot))
            source.write_text("VALUE = 2\n", encoding="utf-8")
            self.assertFalse(cron_graphify_code_refresh.snapshot_matches_live_source(root, snapshot))


class GraphifyOneShotPromotionTests(unittest.TestCase):
    def test_promotion_refuses_live_source_drift_before_atomic_selection(self) -> None:
        from scripts import cron_graphify_code_refresh

        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "graphify-out").mkdir()
            snapshot = cron_graphify_code_refresh.SourceSnapshot(
                generation_id="g-001",
                snapshot_dir=root / "source" / "graphify-candidates" / "g-001",
                workspace_root=root / "source" / "graphify-candidates" / "g-001" / "workspace",
                source_fingerprint={"scripts/example.py": "a" * 64},
            )
            events: list[str] = []

            result = cron_graphify_code_refresh.run_one_shot_promotion(
                root,
                "g-001",
                create_snapshot=lambda _root, _generation: snapshot,
                snapshot_matches=lambda _root, _snapshot: len(events) == 0,
                build_candidate=lambda _snapshot: events.append("build") or root / "candidate" / "graphify-out",
                validate_candidate=lambda _snapshot, _candidate: events.append("validate"),
                publish=lambda *_args, **_kwargs: events.append("publish"),
            )

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["reason"], "live_source_changed_before_promotion")
            self.assertEqual(events, ["build", "validate"])

    def test_failed_post_promotion_verification_restores_the_prior_pointer(self) -> None:
        from scripts import cron_graphify_code_refresh

        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "graphify-out").mkdir()
            snapshot = cron_graphify_code_refresh.SourceSnapshot(
                generation_id="g-001",
                snapshot_dir=root / "source" / "graphify-candidates" / "g-001",
                workspace_root=root / "source" / "graphify-candidates" / "g-001" / "workspace",
                source_fingerprint={"scripts/example.py": "a" * 64},
            )
            events: list[str] = []

            result = cron_graphify_code_refresh.run_one_shot_promotion(
                root,
                "g-001",
                create_snapshot=lambda _root, _generation: snapshot,
                snapshot_matches=lambda _root, _snapshot: True,
                build_candidate=lambda _snapshot: events.append("build") or root / "candidate" / "graphify-out",
                validate_candidate=lambda _snapshot, _candidate: events.append("validate"),
                capture_pointer=lambda _root, **_kwargs: b"prior-pointer",
                publish=lambda *_args, **_kwargs: events.append("publish"),
                post_publish_check=lambda _root: events.append("postcheck")
                or {"status": "stale", "issues": [{"code": "source_changed"}]},
                restore_pointer=lambda _root, pointer, **_kwargs: events.append(
                    f"restore:{pointer.decode('ascii')}"
                ),
            )

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["reason"], "post_promotion_verification_failed")
            self.assertEqual(
                events,
                ["build", "validate", "publish", "postcheck", "restore:prior-pointer"],
            )


class GraphifyCandidateBuildTests(unittest.TestCase):
    def test_candidate_build_runs_against_the_snapshot_not_live_workspace(self) -> None:
        from scripts import cron_graphify_code_refresh

        with TemporaryDirectory() as directory:
            root = Path(directory)
            workspace_root = root / "source" / "graphify-candidates" / "g-001" / "workspace"
            workspace_root.mkdir(parents=True)
            snapshot = cron_graphify_code_refresh.SourceSnapshot(
                generation_id="g-001",
                snapshot_dir=workspace_root.parent,
                workspace_root=workspace_root,
                source_fingerprint={"scripts/example.py": "a" * 64},
            )
            commands: list[tuple[tuple[str, ...], Path]] = []

            def runner(command: tuple[str, ...], cwd: Path) -> None:
                commands.append((command, cwd))
                graph_dir = workspace_root / "graphify-out"
                graph_dir.mkdir()
                (graph_dir / "graph.json").write_text("{}\n", encoding="utf-8")

            graph_path = cron_graphify_code_refresh.build_candidate_graph(snapshot, runner=runner)

            self.assertEqual(graph_path, workspace_root / "graphify-out" / "graph.json")
            self.assertEqual(commands[0][1], workspace_root)
            self.assertIn(str(workspace_root), commands[0][0])
            self.assertIn("--code-only", commands[0][0])
            self.assertNotIn(str(root), commands[0][0])


class GraphifyCandidateValidationTests(unittest.TestCase):
    def test_candidate_validation_requires_all_gates_before_publication(self) -> None:
        from scripts import cron_graphify_code_refresh

        with TemporaryDirectory() as directory:
            root = Path(directory)
            workspace_root = root / "source" / "graphify-candidates" / "g-001" / "workspace"
            graph_path = workspace_root / "graphify-out" / "graph.json"
            graph_path.parent.mkdir(parents=True)
            graph_path.write_text("{}\n", encoding="utf-8")
            snapshot = cron_graphify_code_refresh.SourceSnapshot(
                generation_id="g-001",
                snapshot_dir=workspace_root.parent,
                workspace_root=workspace_root,
                source_fingerprint={"scripts/example.py": "a" * 64},
            )
            events: list[str] = []

            result = cron_graphify_code_refresh.validate_candidate_graph(
                snapshot,
                graph_path,
                reconcile_candidate=lambda candidate_root, *, graph_path: events.append(
                    f"reconcile:{candidate_root == workspace_root}:{graph_path.name}"
                ),
                write_candidate_baseline=lambda candidate_root: events.append(
                    f"baseline:{candidate_root == workspace_root}"
                ),
                check_candidate_freshness=lambda candidate_root: events.append(
                    f"freshness:{candidate_root == workspace_root}"
                )
                or {"status": "fresh", "issues": []},
                diagnose_candidate=lambda candidate_graph: events.append(
                    f"diagnose:{candidate_graph == graph_path}"
                )
                or {
                    "summary": {
                        "non_object_edges": 0,
                        "missing_endpoint_edges": 0,
                        "dangling_endpoint_edges": 0,
                        "self_loop_edges": 0,
                        "post_build_error": "",
                    }
                },
                check_candidate_contract=lambda candidate_graph: events.append(
                    f"contract:{candidate_graph == graph_path}"
                )
                or {"status": "pass"},
            )

            self.assertEqual(result["status"], "accepted")
            self.assertEqual(
                events,
                [
                    "reconcile:True:graph.json",
                    "baseline:True",
                    "freshness:True",
                    "diagnose:True",
                    "contract:True",
                ],
            )


if __name__ == "__main__":
    unittest.main()
