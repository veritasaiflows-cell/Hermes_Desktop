"""Contract tests for scripts/qa_review_driver.py (effective-model verified QA review).

The driver must never trust the route it *asked* for: it reads the model the session actually ran
on and fails closed if that is unknown, differs from the pinned route, or equals the lane author.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

import qa_review_driver as driver  # noqa: E402
from test_helper_agent_router import _LaneFixture, _install_registry  # noqa: E402

SOL = "openai-codex/gpt-6.1-sol"
ASTRA = "openai-codex/gpt-6-astra"
SONNET = "anthropic/claude-sonnet-5-5"
LANE = "WF-1000::helper-gate-fixture"


def _session(model: str, provider: str) -> str:
    return json.dumps({"id": "20261003_000000_abcdef", "model": model, "billing_provider": provider}) + "\n"


class _Fakes:
    """Injected runner/exporter so no model is ever called."""

    def __init__(self, ran_model: str, ran_provider: str, stderr: str = "session_id: 20261003_000000_abcdef\n",
                 stdout: str = "VERDICT: PASS\n", returncode: int = 0, export_missing: bool = False) -> None:
        self.ran = (ran_model, ran_provider)
        self.stderr, self.stdout, self.returncode = stderr, stdout, returncode
        self.export_missing = export_missing
        self.run_calls: list[list[str]] = []
        self.export_calls: list[str] = []

    def run(self, command: list[str]) -> tuple[int, str, str]:
        self.run_calls.append(command)
        return self.returncode, self.stdout, self.stderr

    def export(self, session_id: str) -> str | None:
        self.export_calls.append(session_id)
        return None if self.export_missing else _session(*self.ran)


class QaReviewDriverTests(unittest.TestCase):
    def _setup(self, root: Path, author: str) -> tuple[Path, Path]:
        _install_registry(root)
        fixture = _LaneFixture(root)
        fixture.lease()
        connection = sqlite3.connect(str(fixture.manager.register_path))
        try:
            connection.execute("UPDATE lanes SET expected_model=? WHERE lane_id=?", (author, LANE))
            connection.commit()
        finally:
            connection.close()
        prompt = root / "prompt.txt"
        prompt.write_text("review this pack", encoding="utf-8")
        return prompt, root / "out.json"

    def _request(self, model: str) -> dict:
        return {
            "schema": "helper-agent-request.v1", "task_id": "drv-001", "task_class": "review",
            "phase": "pre-implementation", "mode": "read-only", "role": "qa", "model": model,
            "reviews_lane": LANE, "reviewer_model": model, "objective": "Independent review.",
            "scope": "Read-only.", "allowed_toolsets": ["read_files", "search_files"],
            "allowed_writes": [], "max_duration_minutes": 30, "owner": "agent-main",
        }

    def _go(self, author: str, route: str, fakes: _Fakes) -> tuple[dict, Path, Path]:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            prompt, out = self._setup(root, author)
            result = driver.run_review(self._request(route), prompt, out, project_root=root,
                                       runner=fakes.run, exporter=fakes.export)
            record = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
            return result, record, out

    # --- happy paths, both author directions -------------------------------------------------
    def test_sol_authored_lane_reviewed_on_sonnet_is_verified(self) -> None:
        fakes = _Fakes("claude-sonnet-5-5", "anthropic")
        result, record, _ = self._go(SOL, SONNET, fakes)
        self.assertEqual(result["status"], "verified", result)
        self.assertEqual(record["effective_model"], "anthropic/claude-sonnet-5-5")
        self.assertTrue(record["effective_model_verified"])

    def test_sonnet_authored_lane_reviewed_on_opus_is_verified(self) -> None:
        fakes = _Fakes("claude-opus-5-5", "anthropic")
        result, record, _ = self._go(SONNET, "anthropic/claude-opus-5-5", fakes)
        self.assertEqual(result["status"], "verified", result)
        self.assertEqual(record["effective_model"], "anthropic/claude-opus-5-5")
        self.assertTrue(record["effective_model_verified"])

    def test_route_is_pinned_with_provider_and_model_flags(self) -> None:
        fakes = _Fakes("claude-sonnet-5-5", "anthropic")
        self._go(SOL, SONNET, fakes)
        command = fakes.run_calls[0]
        self.assertIn("--provider", command)
        self.assertEqual(command[command.index("--provider") + 1], "anthropic")
        self.assertEqual(command[command.index("-m") + 1], "claude-sonnet-5-5")
        self.assertIn("--query-file", command)
        self.assertEqual(command[command.index("-p") + 1], "qa")

    # --- fail closed -------------------------------------------------------------------------
    def test_failover_onto_the_author_model_is_a_failed_review(self) -> None:
        # asked for Sonnet, but the session actually ran on Sol, the lane author's model
        fakes = _Fakes("gpt-6.1-sol", "openai-codex")
        result, record, _ = self._go(SOL, SONNET, fakes)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(any("author" in p for p in result["problems"]), result["problems"])
        self.assertFalse(record["effective_model_verified"])

    def test_effective_model_differing_from_the_pinned_route_fails(self) -> None:
        fakes = _Fakes("gpt-6-astra", "openai-codex")
        result, _, _ = self._go(SOL, SONNET, fakes)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(any("pinned route" in p for p in result["problems"]), result["problems"])

    def test_unknown_effective_model_fails_closed(self) -> None:
        for fakes in (_Fakes("x", "y", stderr=""), _Fakes("x", "y", export_missing=True)):
            with self.subTest(stderr=fakes.stderr, missing=fakes.export_missing):
                result, record, _ = self._go(SOL, SONNET, fakes)
                self.assertEqual(result["status"], "failed")
                self.assertFalse(record["effective_model_verified"])
                self.assertTrue(any("effective model" in p for p in result["problems"]), result["problems"])

    def test_runner_nonzero_exit_is_a_failed_review(self) -> None:
        fakes = _Fakes("claude-sonnet-5-5", "anthropic", returncode=1, stdout="")
        result, _, _ = self._go(SOL, SONNET, fakes)
        self.assertEqual(result["status"], "failed")

    def test_author_match_in_the_request_is_refused_before_any_model_call(self) -> None:
        fakes = _Fakes("gpt-6.1-sol", "openai-codex")
        result, record, _ = self._go(SOL, SOL, fakes)
        self.assertEqual(result["status"], "refused")
        self.assertEqual(fakes.run_calls, [])
        self.assertIsNone(record)

    def test_bare_model_spelling_cannot_slip_past_the_author_check(self) -> None:
        fakes = _Fakes("gpt-6.1-sol", "openai-codex")
        result, _, _ = self._go("gpt-6.1-sol", "gpt-6.1-sol", fakes)
        self.assertEqual(result["status"], "refused")
        self.assertEqual(fakes.run_calls, [])

    def test_model_outside_the_qa_chain_is_refused(self) -> None:
        # Sol is no longer a QA route (operator rebinding 2026-10-06).
        fakes = _Fakes("gpt-6.1-sol", "openai-codex")
        result, _, _ = self._go(SONNET, SOL, fakes)
        self.assertEqual(result["status"], "refused")
        self.assertEqual(fakes.run_calls, [])

    def test_non_review_request_is_refused(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            prompt, out = self._setup(root, SOL)
            request = self._request(SONNET)
            request["task_class"] = "implementation"
            fakes = _Fakes("claude-sonnet-5-5", "anthropic")
            result = driver.run_review(request, prompt, out, project_root=root, runner=fakes.run, exporter=fakes.export)
        self.assertEqual(result["status"], "refused")
        self.assertEqual(fakes.run_calls, [])

    def test_record_carries_provenance_and_the_verbatim_response(self) -> None:
        fakes = _Fakes("claude-sonnet-5-5", "anthropic", stdout="VERDICT: PASS\nok\n")
        _, record, _ = self._go(SOL, SONNET, fakes)
        for key in ("reviews_lane", "author_model", "requested_route", "effective_model", "session_id",
                    "prompt_sha256", "verbatim_response", "effective_model_verified"):
            self.assertIn(key, record)
        self.assertEqual(record["author_model"], SOL)
        self.assertEqual(record["verbatim_response"], "VERDICT: PASS\nok\n")
        self.assertEqual(record["session_id"], "20261003_000000_abcdef")


class ParsingTests(unittest.TestCase):
    def test_session_id_is_read_from_stderr(self) -> None:
        self.assertEqual(driver.parse_session_id("warn\nsession_id: 20261003_1_ab\n"), "20261003_1_ab")
        self.assertIsNone(driver.parse_session_id("nothing here"))

    def test_effective_model_reads_provider_and_model(self) -> None:
        self.assertEqual(driver.effective_model(_session("gpt-6.1-sol", "openai-codex")), "openai-codex/gpt-6.1-sol")
        for bad in ("", "not json", json.dumps({"id": "x"}) + "\n", "[]"):
            with self.subTest(bad=bad):
                self.assertIsNone(driver.effective_model(bad))


if __name__ == "__main__":
    unittest.main()
