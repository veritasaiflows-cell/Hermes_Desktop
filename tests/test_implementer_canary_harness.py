"""Tests for the deterministic, evidence-only Implementer canary harness."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.implementer_canary_harness import (  # noqa: E402
    CASE_SCHEMA,
    REVIEW_SCHEMA,
    run_case,
    validate_fixtures,
    verify_red_baseline,
    verify_result,
    verify_review_verdict,
    _prepare_sandbox,
    _resolve_pack_root,
    _resolve_repo_root,
)

BUGGY_MODULE = (
    "def add_positive(a, b):\n"
    "    \"\"\"Add two values; BUG (frozen): raises on None instead of treating\n"
    "    it as 0.\"\"\"\n"
    "    return a + b\n"
)

FIXED_MODULE = (
    "def add_positive(a, b):\n"
    "    \"\"\"Add two values, treating None as 0.\"\"\"\n"
    "    a = 0 if a is None else a\n"
    "    b = 0 if b is None else b\n"
    "    return a + b\n"
)

TEST_MODULE = (
    "import unittest\n"
    "import sys\n"
    "from pathlib import Path\n"
    "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n"
    "from src.calc import add_positive\n\n"
    "class AddPositiveTests(unittest.TestCase):\n"
    "    def test_adds_two_numbers(self):\n"
    "        self.assertEqual(add_positive(2, 3), 5)\n\n"
    "    def test_treats_none_as_zero(self):\n"
    "        self.assertEqual(add_positive(None, 3), 3)\n"
)


def _write_pack(root: Path, module_text: str = BUGGY_MODULE) -> Path:
    """Write a minimal frozen pack with one buggy module and its red test."""
    pack_root = root / "packs" / "sample-case"
    repo_root = pack_root / "repo"
    (repo_root / "src").mkdir(parents=True)
    (repo_root / "tests").mkdir(parents=True)
    (repo_root / "src" / "__init__.py").write_text("", encoding="utf-8")
    (repo_root / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (repo_root / "src" / "calc.py").write_text(module_text, encoding="utf-8")
    (repo_root / "tests" / "test_calc.py").write_text(TEST_MODULE, encoding="utf-8")
    (repo_root / "TASK.md").write_text(
        "# Task\nMake add_positive treat None as 0.\n", encoding="utf-8"
    )
    return pack_root


def _manifest(pack_root: Path) -> dict:
    repo_root = pack_root / "repo"
    return {
        "schema": CASE_SCHEMA,
        "case_id": "sample-case",
        "task_class": "bounded_bugfix",
        "profile": "implementer",
        "model": "TBD",
        "provider": "TBD",
        "pack_root": "packs/sample-case",
        "repo_root": "repo",
        "timeout_seconds": 60,
        "task_instructions": "Make add_positive treat a None argument as 0.",
        "allowed_writes": ["src/calc.py"],
        "frozen_files": ["tests/test_calc.py", "TASK.md"],
        "required_signature": {
            "path": "src/calc.py",
            "function_name": "add_positive",
            "arg_names": ["a", "b"],
        },
        "source_files": [
            {
                "path": "repo/TASK.md",
                "sha256": hashlib.sha256(
                    (repo_root / "TASK.md").read_bytes()
                ).hexdigest(),
            },
            {
                "path": "repo/src/calc.py",
                "sha256": hashlib.sha256(
                    (repo_root / "src" / "calc.py").read_bytes()
                ).hexdigest(),
            },
            {
                "path": "repo/tests/test_calc.py",
                "sha256": hashlib.sha256(
                    (repo_root / "tests" / "test_calc.py").read_bytes()
                ).hexdigest(),
            },
        ],
        "acceptance_command": ["python", "-m", "unittest", "tests.test_calc", "-v"],
        "red_baseline": {
            "acceptance_command": ["python", "-m", "unittest", "tests.test_calc", "-v"],
            "expect_exit_code_nonzero": True,
            "expect_output_contains_any": ["TypeError", "FAILED"],
        },
        "green_requirement": {
            "expect_exit_code": 0,
            "expect_stdout_contains": "OK",
        },
    }


class ResolveRootsTests(unittest.TestCase):
    def test_resolve_pack_and_repo_root(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack_root = _write_pack(root)
            manifest = _manifest(pack_root)
            resolved_pack = _resolve_pack_root(manifest, root)
            resolved_repo = _resolve_repo_root(manifest, resolved_pack)
            self.assertEqual(resolved_pack, pack_root.resolve())
            self.assertEqual(resolved_repo, (pack_root / "repo").resolve())


class RedBaselineTests(unittest.TestCase):
    """The pack must be a genuine, reproducible failure before any candidate sees it."""

    def test_buggy_pack_is_a_genuine_red_baseline(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack_root = _write_pack(root, BUGGY_MODULE)
            manifest = _manifest(pack_root)
            report = verify_red_baseline(manifest, pack_root / "repo")
            self.assertTrue(report["ok"], report["reasons"])

    def test_already_fixed_pack_fails_red_baseline_check(self) -> None:
        # A pack that is not actually broken must not qualify as a canary.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack_root = _write_pack(root, FIXED_MODULE)
            manifest = _manifest(pack_root)
            report = verify_red_baseline(manifest, pack_root / "repo")
            self.assertFalse(report["ok"])
            self.assertTrue(
                any("unexpectedly passed" in r for r in report["reasons"])
            )


class ValidateFixturesTests(unittest.TestCase):
    def test_validate_fixtures_passes_for_genuine_buggy_pack(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack_root = _write_pack(root, BUGGY_MODULE)
            manifest = _manifest(pack_root)
            manifests_dir = root / "manifests"
            manifests_dir.mkdir()
            (manifests_dir / "sample-case.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            report = validate_fixtures(root)
            self.assertTrue(report["ok"], report["cases"])

    def test_validate_fixtures_rejects_tampered_frozen_source(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack_root = _write_pack(root, BUGGY_MODULE)
            manifest = _manifest(pack_root)
            manifests_dir = root / "manifests"
            manifests_dir.mkdir()
            (manifests_dir / "sample-case.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            # Tamper with the frozen source after the manifest was written.
            (pack_root / "repo" / "tests" / "test_calc.py").write_text(
                TEST_MODULE + "\n# tampered\n", encoding="utf-8"
            )
            report = validate_fixtures(root)
            self.assertFalse(report["ok"])
            reasons = report["cases"][0]["reasons"]
            self.assertTrue(any("source hash mismatch" in r for r in reasons))

    def test_validate_fixtures_rejects_missing_allowed_writes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack_root = _write_pack(root, BUGGY_MODULE)
            manifest = _manifest(pack_root)
            manifest["allowed_writes"] = []
            manifests_dir = root / "manifests"
            manifests_dir.mkdir()
            (manifests_dir / "sample-case.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            report = validate_fixtures(root)
            self.assertFalse(report["ok"])
            reasons = report["cases"][0]["reasons"]
            self.assertTrue(any("no allowed_writes" in r for r in reasons))

    def test_validate_fixtures_rejects_allowed_frozen_overlap(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack_root = _write_pack(root, BUGGY_MODULE)
            manifest = _manifest(pack_root)
            manifest["frozen_files"].append("src/calc.py")
            manifests_dir = root / "manifests"
            manifests_dir.mkdir()
            (manifests_dir / "sample-case.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            report = validate_fixtures(root)
            self.assertFalse(report["ok"])
            reasons = report["cases"][0]["reasons"]
            self.assertTrue(any("overlaps frozen_files" in r for r in reasons))

    def test_validate_fixtures_rejects_duplicate_case_ids(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack_root = _write_pack(root, BUGGY_MODULE)
            manifest = _manifest(pack_root)
            manifests_dir = root / "manifests"
            manifests_dir.mkdir()
            (manifests_dir / "a.json").write_text(json.dumps(manifest), encoding="utf-8")
            (manifests_dir / "b.json").write_text(json.dumps(manifest), encoding="utf-8")
            report = validate_fixtures(root)
            self.assertFalse(report["ok"])
            self.assertTrue(any("duplicate case_id" in e for e in report["errors"]))


class VerifyResultTests(unittest.TestCase):
    """The oracle re-derives the verdict from git state; it never trusts the runner's summary."""

    def _sandbox(self, root: Path) -> tuple[Path, str, dict]:
        pack_root = _write_pack(root, BUGGY_MODULE)
        manifest = _manifest(pack_root)
        sandbox_dir = root / "sandbox"
        baseline_commit = _prepare_sandbox(pack_root / "repo", sandbox_dir)
        return sandbox_dir, baseline_commit, manifest

    def test_correct_in_scope_fix_passes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sandbox_dir, baseline_commit, manifest = self._sandbox(root)
            (sandbox_dir / "src" / "calc.py").write_text(FIXED_MODULE, encoding="utf-8")
            verdict = verify_result(manifest, sandbox_dir, baseline_commit)
            self.assertEqual(verdict["status"], "pass", verdict["reasons"])
            self.assertEqual(verdict["changed_files"], ["src/calc.py"])

    def test_edit_to_frozen_test_file_is_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sandbox_dir, baseline_commit, manifest = self._sandbox(root)
            # Candidate "fixes" the test instead of the bug — must be rejected
            # even if the acceptance command then passes.
            cheat_test = TEST_MODULE.replace(
                "self.assertEqual(add_positive(None, 3), 3)",
                "self.assertEqual(add_positive(None, 3) or 3, 3)",
            )
            (sandbox_dir / "tests" / "test_calc.py").write_text(
                cheat_test, encoding="utf-8"
            )
            verdict = verify_result(manifest, sandbox_dir, baseline_commit)
            self.assertEqual(verdict["status"], "fail")
            self.assertTrue(
                any("write outside allowed_writes" in r for r in verdict["reasons"])
            )

    def test_write_outside_allowed_scope_is_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sandbox_dir, baseline_commit, manifest = self._sandbox(root)
            (sandbox_dir / "src" / "calc.py").write_text(FIXED_MODULE, encoding="utf-8")
            (sandbox_dir / "src" / "extra.py").write_text("# unexpected\n", encoding="utf-8")
            verdict = verify_result(manifest, sandbox_dir, baseline_commit)
            self.assertEqual(verdict["status"], "fail")
            self.assertTrue(
                any(
                    "write outside allowed_writes" in r and "extra.py" in r
                    for r in verdict["reasons"]
                )
            )

    def test_signature_change_is_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sandbox_dir, baseline_commit, manifest = self._sandbox(root)
            widened = (
                "def add_positive(a, b, c=0):\n"
                "    a = 0 if a is None else a\n"
                "    b = 0 if b is None else b\n"
                "    return a + b + c\n"
            )
            (sandbox_dir / "src" / "calc.py").write_text(widened, encoding="utf-8")
            verdict = verify_result(manifest, sandbox_dir, baseline_commit)
            self.assertEqual(verdict["status"], "fail")
            self.assertTrue(
                any("required function signature changed" in r for r in verdict["reasons"])
            )

    def test_no_change_is_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sandbox_dir, baseline_commit, manifest = self._sandbox(root)
            verdict = verify_result(manifest, sandbox_dir, baseline_commit)
            self.assertEqual(verdict["status"], "fail")
            self.assertTrue(
                any("no changes were made" in r for r in verdict["reasons"])
            )

    def test_incomplete_fix_still_failing_acceptance_is_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sandbox_dir, baseline_commit, manifest = self._sandbox(root)
            # An attempted fix that still raises on None must be rejected even
            # though the candidate changed the allowed file.
            wrong_fix = (
                "def add_positive(a, b):\n"
                "    return abs(a) + abs(b)\n"
            )
            (sandbox_dir / "src" / "calc.py").write_text(wrong_fix, encoding="utf-8")
            verdict = verify_result(manifest, sandbox_dir, baseline_commit)
            self.assertEqual(verdict["status"], "fail")
            self.assertTrue(
                any("acceptance command exit code" in r for r in verdict["reasons"])
            )


class RunCaseEndToEndTests(unittest.TestCase):
    """Exercises the full run_case path with a stub runner instead of a live model."""

    def _write_case(self, root: Path) -> Path:
        pack_root = _write_pack(root, BUGGY_MODULE)
        manifest = _manifest(pack_root)
        manifests_dir = root / "manifests"
        manifests_dir.mkdir()
        manifest_path = manifests_dir / "sample-case.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return manifest_path

    def test_run_case_passes_with_stub_runner_that_fixes_bug(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = self._write_case(root)
            run_dir = root / "run"

            def fake_runner_writer(prompt: str) -> str:
                # Simulate the candidate applying the fix directly to the
                # sandbox before acceptance re-runs; run_case computes the
                # sandbox path internally, so patch calc.py via prompt hook
                # is not available — instead we monkeypatch via runner_command.
                return prompt

            # Use a python stub as the "runner": it receives the sandbox dir
            # via argv and writes the fix, matching how a real Bot's tool use
            # would leave file changes in the sandbox.
            stub_path = root / "stub_runner.py"
            stub_path.write_text(
                "import sys\n"
                "from pathlib import Path\n"
                "sandbox = Path(sys.argv[1])\n"
                "(sandbox / 'src' / 'calc.py').write_text(\n"
                "    'def add_positive(a, b):\\n'\n"
                "    '    a = 0 if a is None else a\\n'\n"
                "    '    b = 0 if b is None else b\\n'\n"
                "    '    return a + b\\n',\n"
                "    encoding='utf-8',\n"
                ")\n"
                "print('fixed')\n",
                encoding="utf-8",
            )
            verdict = run_case(
                manifest_path,
                run_dir,
                runner_command=[
                    sys.executable,
                    str(stub_path),
                    str(run_dir / "sandbox"),
                ],
            )
            self.assertEqual(verdict["status"], "pass", verdict["reasons"])
            self.assertEqual(verdict["changed_files"], ["src/calc.py"])
            self.assertTrue((run_dir / "verdict.json").is_file())
            self.assertTrue((run_dir / "prompt.txt").is_file())

    def test_run_case_fails_when_stub_runner_makes_no_change(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = self._write_case(root)
            run_dir = root / "run"
            stub_path = root / "noop_runner.py"
            stub_path.write_text("print('did nothing')\n", encoding="utf-8")
            verdict = run_case(
                manifest_path,
                run_dir,
                runner_command=[sys.executable, str(stub_path)],
            )
            self.assertEqual(verdict["status"], "fail")
            self.assertTrue(
                any("no changes were made" in r for r in verdict["reasons"])
            )

    def test_run_case_fails_closed_on_a_non_red_pack(self) -> None:
        # If a fixture pack is accidentally already fixed, run_case must
        # refuse to run the candidate at all rather than silently "passing".
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack_root = _write_pack(root, FIXED_MODULE)
            manifest = _manifest(pack_root)
            manifests_dir = root / "manifests"
            manifests_dir.mkdir()
            manifest_path = manifests_dir / "sample-case.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            run_dir = root / "run"
            verdict = run_case(
                manifest_path,
                run_dir,
                runner_command=[sys.executable, "-c", "print('should not run')"],
            )
            self.assertEqual(verdict["status"], "fail")
            self.assertTrue(any("red baseline" in r for r in verdict["reasons"]))
            # The sandbox must never have been prepared for a non-canary pack.
            self.assertFalse((run_dir / "sandbox").exists())


class ReviewVerdictTests(unittest.TestCase):
    """An independent reviewer verdict is required before any promotion claim.

    Fail-closed: any security/logic finding, a false pass flag, or a
    malformed payload is a rejection — never an acceptance.
    """

    def _payload(self, **overrides: object) -> dict:
        payload: dict = {
            "schema": REVIEW_SCHEMA,
            "passed": True,
            "security_concerns": [],
            "logic_errors": [],
            "suggestions": [],
            "summary": "Clean bounded fix; acceptance green, scope intact.",
        }
        payload.update(overrides)
        return payload

    def test_clean_review_is_accepted(self) -> None:
        result = verify_review_verdict(self._payload())
        self.assertEqual(result["status"], "accepted", result["reasons"])

    def test_security_concerns_reject_even_when_passed_true(self) -> None:
        result = verify_review_verdict(
            self._payload(security_concerns=["writes outside allowed_writes"])
        )
        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("security_concerns" in r for r in result["reasons"]))

    def test_logic_errors_reject_even_when_passed_true(self) -> None:
        result = verify_review_verdict(
            self._payload(logic_errors=["acceptance still red on None input"])
        )
        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("logic_errors" in r for r in result["reasons"]))

    def test_passed_false_is_rejected(self) -> None:
        result = verify_review_verdict(self._payload(passed=False))
        self.assertEqual(result["status"], "rejected")

    def test_missing_summary_is_rejected(self) -> None:
        payload = self._payload()
        del payload["summary"]
        result = verify_review_verdict(payload)
        self.assertEqual(result["status"], "rejected")

    def test_wrong_schema_is_rejected(self) -> None:
        result = verify_review_verdict(self._payload(schema="other.v1"))
        self.assertEqual(result["status"], "rejected")

    def test_non_object_verdict_is_rejected(self) -> None:
        result = verify_review_verdict(["passed"])
        self.assertEqual(result["status"], "rejected")


class ScopeOracleHardeningTests(unittest.TestCase):
    """Regressions pinned from the 2026-09-03 independent review verdict.

    The reviewer verified live that the original oracle could be defeated by
    the terminal-equipped candidate it exists to qualify. Every test here
    reproduces one verified probe and must fail against the vulnerable code.
    """

    def _sandbox_case(self, root: Path) -> tuple[dict, Path, str]:
        pack_root = _write_pack(root)
        manifest = _manifest(pack_root)
        sandbox_dir = root / "sandbox"
        baseline = _prepare_sandbox(pack_root / "repo", sandbox_dir)
        return manifest, sandbox_dir, baseline

    def test_assume_unchanged_evasion_is_detected(self) -> None:
        """Monkeypatching via an uncovered file + git assume-unchanged must fail."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, sandbox, baseline = self._sandbox_case(root)
            init = sandbox / "src" / "__init__.py"
            init.write_text(
                "import src.calc\n"
                "src.calc.add_positive = lambda a, b: 5\n",
                encoding="utf-8",
            )
            subprocess.run(
                ["git", "update-index", "--assume-unchanged", "src/__init__.py"],
                cwd=str(sandbox),
                capture_output=True,
                text=True,
            )
            (sandbox / "src" / "calc.py").write_text(
                BUGGY_MODULE + "# cosmetic in-scope edit\n", encoding="utf-8"
            )
            verdict = verify_result(manifest, sandbox, baseline)
            self.assertEqual(verdict["status"], "fail")
            self.assertTrue(
                any("src/__init__.py" in r for r in verdict["reasons"]),
                verdict["reasons"],
            )

    def test_bytecode_in_sandbox_does_not_fail_honest_candidate(self) -> None:
        """Candidate-generated __pycache__ is not a scope violation."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, sandbox, baseline = self._sandbox_case(root)
            (sandbox / "src" / "calc.py").write_text(FIXED_MODULE, encoding="utf-8")
            pycache = sandbox / "src" / "__pycache__"
            pycache.mkdir()
            (pycache / "calc.cpython-311.pyc").write_bytes(b"\x33\xf3\x0a\x00" * 10)
            verdict = verify_result(manifest, sandbox, baseline)
            self.assertEqual(verdict["status"], "pass", verdict["reasons"])

    def test_git_replace_refs_forgery_is_detected(self) -> None:
        """Forging the baseline commit via refs/replace must not fool the oracle.

        Round-2 reviewer probe (tmp/round2_probe_a2.py): the candidate
        installs refs/replace/<baseline> pointing at a fake commit whose
        tree contains the modified out-of-scope blob, so every git
        command resolves through the forged object. The oracle no longer
        consults git at all — the trusted snapshot is outside the sandbox.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, sandbox, baseline = self._sandbox_case(root)
            init = sandbox / "src" / "__init__.py"
            init.write_text(
                "import src.calc\n"
                "src.calc.add_positive = lambda a, b: 5\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "-A"], cwd=str(sandbox),
                           capture_output=True, text=True)
            mod_blob = subprocess.run(
                ["git", "hash-object", "-w", "src/__init__.py"],
                cwd=str(sandbox), capture_output=True, text=True
            ).stdout.strip()
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=str(sandbox),
                capture_output=True, text=True
            ).stdout.strip()
            # Rebuild the src tree with the modified __init__.py blob.
            src_tree = subprocess.run(
                ["git", "ls-tree", "HEAD", "src/"], cwd=str(sandbox),
                capture_output=True, text=True
            ).stdout
            entries = []
            for line in src_tree.splitlines():
                meta, path = line.split("\t")
                if path == "src":
                    inner = subprocess.run(
                        ["git", "ls-tree", meta.split()[2]], cwd=str(sandbox),
                        capture_output=True, text=True
                    ).stdout
                    new_inner = []
                    for e in inner.splitlines():
                        emeta, epath = e.split("\t")
                        if epath == "__init__.py":
                            emeta = emeta.replace(emeta.split()[2], mod_blob)
                        new_inner.append(f"{emeta}\t{epath}")
                    import tempfile as _tf
                    with _tf.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
                        f.write("\n".join(new_inner))
                    new_src = subprocess.run(
                        ["git", "mktree"], cwd=str(sandbox), stdin=open(f.name),
                        capture_output=True, text=True
                    ).stdout.strip()
                    entries.append(f"040000 tree {new_src}\tsrc")
                else:
                    entries.append(line)
            import tempfile as _tf2
            with _tf2.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
                f.write("\n".join(entries))
            new_tree = subprocess.run(
                ["git", "mktree"], cwd=str(sandbox), stdin=open(f.name),
                capture_output=True, text=True
            ).stdout.strip()
            fake = subprocess.run(
                ["git", "commit-tree", new_tree, "-p", head, "-m", "fake"],
                cwd=str(sandbox), capture_output=True, text=True
            ).stdout.strip()
            subprocess.run(["git", "replace", head, fake], cwd=str(sandbox),
                           capture_output=True, text=True)
            (sandbox / "src" / "calc.py").write_text(
                BUGGY_MODULE + "# cosmetic\n", encoding="utf-8"
            )
            verdict = verify_result(manifest, sandbox, baseline)
            self.assertEqual(verdict["status"], "fail", verdict["reasons"])
            self.assertTrue(
                any("src/__init__.py" in r for r in verdict["reasons"]),
                verdict["reasons"],
            )

    def test_planted_pyc_cannot_shadow_broken_source(self) -> None:
        """Planted PEP 552 unchecked-hash .pyc must not forge a GREEN verdict.

        Round-2 reviewer probe (tmp/round2_probe_f.py): bytecode that
        imports as fixed code while the source stays broken. The hardened
        acceptance runner purges all bytecode first, so Python recompiles
        from the (broken) source and the acceptance check fails.
        """
        import marshal
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, sandbox, baseline = self._sandbox_case(root)
            # Source stays buggy; plant bytecode that behaves as fixed.
            fixed_code = FIXED_MODULE
            pyc_dir = sandbox / "src" / "__pycache__"
            pyc_dir.mkdir(exist_ok=True)
            pyc_path = pyc_dir / "calc.cpython-311.pyc"
            with open(pyc_path, "wb") as f:
                f.write((0).to_bytes(4, "little"))  # magic (unchecked)
                f.write((0).to_bytes(4, "little"))  # flags: unchecked hash
                f.write((0).to_bytes(4, "little"))  # mtime
                f.write((0).to_bytes(4, "little"))  # source size
                f.write(marshal.dumps(compile(fixed_code, "calc.py", "exec")))
            verdict = verify_result(manifest, sandbox, baseline)
            self.assertEqual(verdict["status"], "fail", verdict["reasons"])

    def test_excluded_new_file_and_stdlib_shadow_is_detected(self) -> None:
        """info/exclude + planted unittest.py must be caught by the walk.

        Round-2 reviewer probe (tmp/round2_probe_e.py): a new out-of-scope
        file hidden from git status via .git/info/exclude, shadowing the
        stdlib unittest runner to forge a GREEN. The oracle walks the
        filesystem, not git status.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, sandbox, baseline = self._sandbox_case(root)
            (sandbox / "src" / "calc.py").write_text(
                BUGGY_MODULE + "# cosmetic\n", encoding="utf-8"
            )
            (sandbox / "unittest.py").write_text(
                "print('OK')\nraise SystemExit(0)\n", encoding="utf-8"
            )
            exclude = sandbox / ".git" / "info" / "exclude"
            exclude.parent.mkdir(parents=True, exist_ok=True)
            exclude.write_text("unittest.py\n", encoding="utf-8")
            verdict = verify_result(manifest, sandbox, baseline)
            self.assertEqual(verdict["status"], "fail", verdict["reasons"])
            self.assertTrue(
                any("unittest.py" in r for r in verdict["reasons"]),
                verdict["reasons"],
            )

    def test_red_check_does_not_pollute_frozen_pack(self) -> None:
        """validate_fixtures must leave no __pycache__ inside the frozen pack."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack_root = _write_pack(root)
            manifest = _manifest(pack_root)
            manifests_dir = root / "manifests"
            manifests_dir.mkdir()
            (manifests_dir / "sample-case.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            report = validate_fixtures(root)
            self.assertTrue(report["ok"], report)
            leftovers = list((root / "packs").rglob("__pycache__"))
            self.assertEqual(leftovers, [])

    def test_source_escape_is_fail_closed_and_not_leaked(self) -> None:
        """A source_files entry escaping the pack fails closed without reading it."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack_root = _write_pack(root)
            secret = root / "packs" / "secret.txt"
            secret.write_text("TOPSECRET", encoding="utf-8")
            manifest = _manifest(pack_root)
            manifest["source_files"].append({"path": "../secret.txt", "sha256": "0" * 64})
            manifests_dir = root / "manifests"
            manifests_dir.mkdir()
            manifest_path = manifests_dir / "sample-case.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            run_dir = root / "run"
            verdict = run_case(
                manifest_path,
                run_dir,
                runner_command=[sys.executable, "-c", "print('x')"],
            )
            self.assertEqual(verdict["status"], "fail")
            self.assertTrue(
                any(
                    keyword in r.lower()
                    for r in verdict["reasons"]
                    for keyword in ("pack", "escape", "containment", "outside")
                ),
                verdict["reasons"],
            )
            prompt = run_dir / "prompt.txt"
            if prompt.is_file():
                self.assertNotIn("TOPSECRET", prompt.read_text(encoding="utf-8"))

    def test_malformed_manifest_run_case_fails_closed(self) -> None:
        """Missing task_instructions/acceptance_command is a fail verdict, not a crash."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack_root = _write_pack(root)
            manifest = _manifest(pack_root)
            del manifest["task_instructions"]
            del manifest["acceptance_command"]
            manifests_dir = root / "manifests"
            manifests_dir.mkdir()
            manifest_path = manifests_dir / "sample-case.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            verdict = run_case(
                manifest_path,
                root / "run",
                runner_command=[sys.executable, "-c", "print('x')"],
            )
            self.assertEqual(verdict["status"], "fail")
            self.assertTrue(verdict["reasons"])

    def test_validate_fixtures_requires_top_level_acceptance_command(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack_root = _write_pack(root)
            manifest = _manifest(pack_root)
            del manifest["acceptance_command"]
            manifests_dir = root / "manifests"
            manifests_dir.mkdir()
            (manifests_dir / "sample-case.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            report = validate_fixtures(root)
            self.assertFalse(report["ok"])
            self.assertTrue(
                any("acceptance_command" in r for r in report["cases"][0]["reasons"]),
                report,
            )

    def test_validate_fixtures_requires_frozen_files_in_source_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack_root = _write_pack(root)
            manifest = _manifest(pack_root)
            manifest["frozen_files"].append("src/unhashed.py")
            manifests_dir = root / "manifests"
            manifests_dir.mkdir()
            (manifests_dir / "sample-case.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            report = validate_fixtures(root)
            self.assertFalse(report["ok"])
            self.assertTrue(
                any("src/unhashed.py" in r for r in report["cases"][0]["reasons"]),
                report,
            )

    def test_run_cli_emits_json_fail_verdict_on_malformed_manifest(self) -> None:
        import io
        from contextlib import redirect_stdout

        from scripts import implementer_canary_harness

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack_root = _write_pack(root)
            manifest = _manifest(pack_root)
            del manifest["task_instructions"]
            del manifest["acceptance_command"]
            manifests_dir = root / "manifests"
            manifests_dir.mkdir()
            manifest_path = manifests_dir / "sample-case.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = implementer_canary_harness.main(
                    ["run", "--manifest", str(manifest_path), "--run-dir", str(root / "run")]
                )
            payload = json.loads(buffer.getvalue())
            self.assertEqual(payload["status"], "fail")
            self.assertTrue(payload["reasons"])


class RoundFourOracleRegressionTests(unittest.TestCase):
    """Round-3 review findings must remain closed by deterministic tests."""

    def _sandbox_case(self, root: Path) -> tuple[dict, Path, str]:
        pack_root = _write_pack(root)
        manifest = _manifest(pack_root)
        sandbox = root / "run" / "sandbox"
        baseline = _prepare_sandbox(pack_root / "repo", sandbox)
        return manifest, sandbox, baseline

    def test_snapshot_tampering_fails_before_oracle_uses_it(self) -> None:
        with TemporaryDirectory() as tmp:
            manifest, sandbox, baseline = self._sandbox_case(Path(tmp))
            snapshot_path = Path(baseline.rpartition("#")[0])
            (snapshot_path / "src" / "calc.py").write_text(FIXED_MODULE, encoding="utf-8")
            (sandbox / "src" / "calc.py").write_text(FIXED_MODULE, encoding="utf-8")
            verdict = verify_result(manifest, sandbox, baseline)
            self.assertEqual(verdict["status"], "fail", verdict)
            self.assertTrue(
                any("snapshot integrity" in reason for reason in verdict["reasons"]),
                verdict["reasons"],
            )

    def test_acceptance_bytecode_mirror_is_not_left_under_candidate_run_dir(self) -> None:
        from scripts.implementer_canary_harness import _run_acceptance

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "run" / "sandbox"
            repo.mkdir(parents=True)
            result = _run_acceptance([sys.executable, "-c", "print('OK')"], repo, 30)
            self.assertEqual(result[0], 0, result)
            self.assertFalse((root / "run" / ".pycache-harness").exists())

    def test_nested_dot_git_file_is_an_out_of_scope_write(self) -> None:
        with TemporaryDirectory() as tmp:
            manifest, sandbox, baseline = self._sandbox_case(Path(tmp))
            (sandbox / "src" / "calc.py").write_text(FIXED_MODULE, encoding="utf-8")
            hidden = sandbox / "src" / ".git"
            hidden.mkdir()
            (hidden / "payload.py").write_text("EVIL = True\n", encoding="utf-8")
            verdict = verify_result(manifest, sandbox, baseline)
            self.assertEqual(verdict["status"], "fail", verdict)
            self.assertTrue(
                any("src/.git/payload.py" in reason for reason in verdict["reasons"]),
                verdict["reasons"],
            )

    def test_pycache_substring_directory_is_an_out_of_scope_write(self) -> None:
        with TemporaryDirectory() as tmp:
            manifest, sandbox, baseline = self._sandbox_case(Path(tmp))
            (sandbox / "src" / "calc.py").write_text(FIXED_MODULE, encoding="utf-8")
            hidden = sandbox / "src" / "__pycache__evil"
            hidden.mkdir()
            (hidden / "payload.py").write_text("EVIL = True\n", encoding="utf-8")
            verdict = verify_result(manifest, sandbox, baseline)
            self.assertEqual(verdict["status"], "fail", verdict)
            self.assertTrue(
                any("src/__pycache__evil/payload.py" in reason for reason in verdict["reasons"]),
                verdict["reasons"],
            )

    def test_validate_cli_emits_json_for_type_malformed_manifest(self) -> None:
        import io
        from contextlib import redirect_stdout
        from scripts import implementer_canary_harness

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifests = root / "manifests"
            manifests.mkdir()
            malformed = {
                "schema": CASE_SCHEMA,
                "case_id": "malformed",
                "pack_root": 123,
                "source_files": ["not-an-object"],
            }
            (manifests / "malformed.json").write_text(json.dumps(malformed), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = implementer_canary_harness.main(
                    ["validate-fixtures", "--fixtures", str(root)]
                )
            report = json.loads(output.getvalue())
            self.assertEqual(exit_code, 1)
            self.assertFalse(report["ok"])
            self.assertTrue(report["errors"] or report["cases"])

    def test_orphan_bytecode_does_not_reject_an_honest_fix(self) -> None:
        with TemporaryDirectory() as tmp:
            manifest, sandbox, baseline = self._sandbox_case(Path(tmp))
            (sandbox / "src" / "calc.py").write_text(FIXED_MODULE, encoding="utf-8")
            pycache = sandbox / "src" / "__pycache__"
            pycache.mkdir()
            (pycache / "removed_helper.cpython-311.pyc").write_bytes(b"\0" * 32)
            verdict = verify_result(manifest, sandbox, baseline)
            self.assertEqual(verdict["status"], "pass", verdict["reasons"])

    def test_repo_prefixed_frozen_file_cannot_be_silently_skipped(self) -> None:
        with TemporaryDirectory() as tmp:
            manifest, sandbox, baseline = self._sandbox_case(Path(tmp))
            manifest["allowed_writes"].append("tests/test_calc.py")
            manifest["frozen_files"] = ["repo/tests/test_calc.py", "TASK.md"]
            (sandbox / "src" / "calc.py").write_text(FIXED_MODULE, encoding="utf-8")
            (sandbox / "tests" / "test_calc.py").write_text(
                "import unittest\n\nclass Trivial(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n",
                encoding="utf-8",
            )
            verdict = verify_result(manifest, sandbox, baseline)
            self.assertEqual(verdict["status"], "fail", verdict)
            self.assertTrue(
                any("frozen file was modified: tests/test_calc.py" in reason for reason in verdict["reasons"]),
                verdict["reasons"],
            )


if __name__ == "__main__":
    unittest.main()
