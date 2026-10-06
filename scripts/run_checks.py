#!/usr/bin/env python3
"""Phase-0 verification runner.

Runs a small test and smoke suite to verify the substrate before workflow expansion.
"""

from __future__ import annotations

from pathlib import Path
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import time
import unittest
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from canonical.db import CanonicalDB, DEFAULT_DATABASE_PATH
from scripts import workflow_router
from scripts import wiki_bootstrap
from scripts.product_research_workflow import run_product_research
from scripts.runtime_metadata import detect_active_model
from scripts.workspace_fingerprint import correctness_snapshot
from scripts.script_doc_validator import validate_script_docs

# Pytest budget inside run_tests(). Kept below cron_test_gate TIMEOUT_SECONDS
# so fingerprinting and telemetry always have headroom, and a hang fails fast
# with partial evidence instead of cascading into the unittest fallback.
PYTEST_TIMEOUT_SECONDS = 360


def build_test_suite() -> unittest.TestSuite:
    """Discover tests independently of the caller's current working directory."""
    return unittest.defaultTestLoader.discover(str(PROJECT_ROOT / "tests"))


def _pytest_outcome_counts(output: str) -> dict[str, int]:
    """Parse executed outcomes from pytest's final timing summary only."""
    summary_line = ""
    for line in reversed(output.splitlines()):
        if re.search(r"\bin\s+\d+(?:\.\d+)?s\b", line):
            summary_line = line
            break
    outcomes = re.findall(
        r"(\d+)\s+(passed|failed|errors?|skipped|xfailed|xpassed)\b",
        summary_line,
    )
    counts: dict[str, int] = {}
    for count, label in outcomes:
        normalized = "error" if label in {"error", "errors"} else label
        counts[normalized] = counts.get(normalized, 0) + int(count)
    return {
        "test_count": sum(counts.values()),
        "test_failure_count": counts.get("failed", 0) + counts.get("error", 0),
    }


def _pytest_test_count(output: str) -> int:
    """Backward-compatible executed-test count helper."""
    return _pytest_outcome_counts(output)["test_count"]


def _pytest_failure_ids(output: str, *, limit: int | None = None) -> list[str]:
    """Return exact pytest node IDs from the final short-summary section."""
    lines = output.splitlines()
    headers = [
        index
        for index, line in enumerate(lines)
        if re.fullmatch(r"=+\s+short test summary info\s+=+", line.strip())
    ]
    if not headers:
        return []
    failure_ids: list[str] = []
    for line in lines[headers[-1] + 1 :]:
        if re.search(r"\bin\s+\d+(?:\.\d+)?s\b", line):
            break
        match = re.match(r"^(?:FAILED|ERROR)\s+(.+)$", line.strip())
        if match:
            failure_ids.append(match.group(1).split(" - ", 1)[0].strip())
    return failure_ids if limit is None else failure_ids[: max(0, limit)]


def _failure_ids_sha256(failure_ids: list[str]) -> str:
    return hashlib.sha256(
        json.dumps(
            sorted(failure_ids),
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def run_tests() -> tuple[bool, list[str], int, int, int]:
    """Discover tests independently of the caller's current working directory.

    Prefers pytest when available for faster feedback and richer reporting.
    Falls back to unittest discovery if pytest is not installed.
    """
    start = time.perf_counter_ns()
    pytest_available = True
    try:
        import pytest as _pytest  # noqa: F401
    except ImportError:
        pytest_available = False

    if pytest_available:
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "pytest", str(PROJECT_ROOT / "tests"), "-q"],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=PYTEST_TIMEOUT_SECONDS,
            )
            elapsed_ms = (time.perf_counter_ns() - start) // 1_000_000
            output = completed.stdout + completed.stderr
            failures = []
            if completed.returncode != 0:
                failures = _pytest_failure_ids(output)
            outcomes = _pytest_outcome_counts(output)
            return (
                completed.returncode == 0,
                failures,
                elapsed_ms,
                outcomes["test_count"],
                outcomes["test_failure_count"],
            )
        except subprocess.TimeoutExpired as exc:
            # Fail fast with partial evidence. Do NOT fall through to the
            # unittest rerun: it would execute the suite a second time with no
            # timeout and guarantee an outer-gate timeout with no diagnosis.
            elapsed_ms = (time.perf_counter_ns() - start) // 1_000_000
            # TimeoutExpired may carry bytes even with text=True, and one
            # stream can already be decoded. Keep diagnostics rather than
            # raising TypeError and losing the timeout proof.
            def decoded(stream: str | bytes | None) -> str:
                return stream.decode("utf-8", errors="replace") if isinstance(stream, bytes) else (stream or "")

            partial = decoded(exc.stdout) + decoded(exc.stderr)
            log_path = _write_timeout_log(partial)
            print(f"pytest timeout after {PYTEST_TIMEOUT_SECONDS}s; partial log: {log_path}")
            if partial.strip():
                print(partial[-4000:])
            outcomes = _pytest_outcome_counts(partial)
            failures = [f"pytest_timeout_after_{PYTEST_TIMEOUT_SECONDS}s"]
            failures.extend(_pytest_failure_ids(partial))
            if len(failures) == 1:
                # Mid-run timeouts rarely reach the short-summary section;
                # fall back to any FAILED lines seen so far.
                failures.extend(
                    re.findall(r"^FAILED\s+(\S+)", partial, re.MULTILINE)[:20]
                )
            return (
                False,
                failures,
                elapsed_ms,
                outcomes["test_count"],
                outcomes["test_failure_count"] + 1,
            )
        except FileNotFoundError:
            pass

    # Fallback: unittest discovery.
    suite = build_test_suite()
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    elapsed_ms = (time.perf_counter_ns() - start) // 1_000_000
    failures = [
        *(str(case) for case, _trace in result.failures),
        *(str(case) for case, _trace in result.errors),
    ]
    return (
        result.wasSuccessful(),
        failures,
        elapsed_ms,
        result.testsRun,
        len(result.failures) + len(result.errors),
    )


TIMEOUT_LOG_RETENTION_DAYS = 14


def _prune_timeout_logs(log_dir: Path) -> None:
    """Delete pytest-timeout logs older than the retention window (best effort)."""
    cutoff = time.time() - TIMEOUT_LOG_RETENTION_DAYS * 86400
    for old in log_dir.glob("pytest-timeout-*.log"):
        try:
            if old.is_file() and old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass


def _write_timeout_log(output: str) -> str:
    """Persist partial pytest output from a timed-out run for later diagnosis."""
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = PROJECT_ROOT / "tmp" / f"pytest-timeout-{stamp}.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _prune_timeout_logs(path.parent)
        path.write_text(output[-200000:], encoding="utf-8")
        return str(path)
    except OSError:
        return "<log unwritable>"


def _safe_correctness_snapshot(project_root: Path) -> dict[str, Any]:
    try:
        return {**correctness_snapshot(project_root), "fingerprint_error": None}
    except (OSError, ValueError) as exc:
        return {
            "source_fingerprint": None,
            "source_file_count": 0,
            "tested_commit": None,
            "fingerprint_error": f"{type(exc).__name__}:{exc}",
        }


def _source_proof_errors(
    before: dict[str, Any],
    after: dict[str, Any],
) -> list[str]:
    before_fingerprint = before.get("source_fingerprint")
    after_fingerprint = after.get("source_fingerprint")
    if not isinstance(before_fingerprint, str) or not isinstance(after_fingerprint, str):
        return ["source_fingerprint_unavailable"]
    if before_fingerprint != after_fingerprint:
        return ["source_changed_during_run"]
    return []


def run_phase0_smoke(database_path: Path) -> dict:
    start = time.perf_counter_ns()
    try:
        with CanonicalDB(database_path) as db:
            before_entities = db.connection.execute(
                "SELECT COUNT(*) FROM entities WHERE entity_type='product_candidate' AND scope='commerce'"
            ).fetchone()[0]
            before_metrics = db.connection.execute(
                "SELECT COUNT(*) FROM metrics WHERE metric_name='commerce.viability_score'"
            ).fetchone()[0]
            before_events = db.connection.execute(
                "SELECT COUNT(*) FROM events WHERE event_type = 'workflow.product_research.candidate_selected'"
            ).fetchone()[0]

        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.csv"
            catalog.write_text(
                "product_name,supplier_name,margin_percent,shipping_days,demand_signal,saturation_signal,cost_per_unit_usd,retail_price_usd,notes\n"
                "Hydra Bluetooth Headset,Supplier North,38,3,84,35,18,49.99,Good first run candidate\n"
                "Budget Tripod Stand,Supplier South,12,9,42,70,11,24.99,Lower priority\n",
                encoding="utf-8",
            )
            run_product_research(catalog, database_path=database_path, top_n=1, dry_run=True)
            run_product_research(catalog, database_path=database_path, top_n=1)

            with CanonicalDB(database_path) as db:
                entity_count = db.connection.execute(
                    "SELECT COUNT(*) FROM entities WHERE entity_type='product_candidate' AND scope='commerce'"
                ).fetchone()[0]
                metric_count = db.connection.execute(
                    "SELECT COUNT(*) FROM metrics WHERE metric_name='commerce.viability_score'"
                ).fetchone()[0]
                event_count = db.connection.execute(
                    "SELECT COUNT(*) FROM events WHERE event_type = 'workflow.product_research.candidate_selected'"
                ).fetchone()[0]
        elapsed_ms = (time.perf_counter_ns() - start) // 1_000_000
        return {
            "entities": entity_count,
            "metrics": metric_count,
            "events": event_count,
            "delta_entities": entity_count - before_entities,
            "delta_metrics": metric_count - before_metrics,
            "delta_events": event_count - before_events,
            "database": str(database_path),
            "duration_ms": elapsed_ms,
        }
    except Exception:
        elapsed_ms = (time.perf_counter_ns() - start) // 1_000_000
        return {
            "entities": 0,
            "metrics": 0,
            "events": 0,
            "delta_entities": 0,
            "delta_metrics": 0,
            "delta_events": 0,
            "database": str(database_path),
            "duration_ms": elapsed_ms,
            "error": "phase0_smoke_failed",
        }



def run_routing_smoke(
    *,
    project_root: Path = PROJECT_ROOT,
    state_dir: Path = PROJECT_ROOT / "state",
    index_path: Path = PROJECT_ROOT / "state" / "workflow-routing-index.json",
    database_path: Path = DEFAULT_DATABASE_PATH,
) -> dict:
    """Regenerate and validate the workflow routing surface."""
    start = time.perf_counter_ns()
    try:
        route_result = workflow_router.route_workflows(
            selector="WF-1000",
            answer="summary",
            validate=True,
            write_index=True,
            write_capsules=True,
            project_root=project_root,
            state_dir=state_dir,
            index_path=index_path,
            routing_database_path=database_path,
        )
        elapsed_ms = (time.perf_counter_ns() - start) // 1_000_000
        return {
            "routing": route_result,
            "index_path": str(index_path),
            "state_dir": str(state_dir),
            "duration_ms": elapsed_ms,
        }
    except Exception as exc:
        elapsed_ms = (time.perf_counter_ns() - start) // 1_000_000
        return {
            "routing": {},
            "index_path": str(index_path),
            "state_dir": str(state_dir),
            "duration_ms": elapsed_ms,
            "error": f"routing_smoke_failed:{type(exc).__name__}",
        }


def run_wiki_smoke(*, project_root: Path = PROJECT_ROOT) -> dict:
    """Run wiki bootstrap validation as part of startup smoke checks."""
    start = time.perf_counter_ns()
    try:
        report = wiki_bootstrap.validate_wiki(project_root=project_root)
        elapsed_ms = (time.perf_counter_ns() - start) // 1_000_000
        report["duration_ms"] = elapsed_ms
        return report
    except Exception as exc:
        elapsed_ms = (time.perf_counter_ns() - start) // 1_000_000
        return {
            "status": "error",
            "issues": [f"{type(exc).__name__}: {exc}"],
            "duration_ms": elapsed_ms,
        }


def run_script_doc_check(*, project_root: Path = PROJECT_ROOT) -> dict:
    """Run the script-documentation coverage gate."""
    start = time.perf_counter_ns()
    try:
        report = validate_script_docs(project_root)
        elapsed_ms = (time.perf_counter_ns() - start) // 1_000_000
        report["duration_ms"] = elapsed_ms
        return report
    except Exception as exc:
        elapsed_ms = (time.perf_counter_ns() - start) // 1_000_000
        return {
            "ok": False,
            "issues": [{"code": "script_doc_check_error", "message": f"{type(exc).__name__}: {exc}"}],
            "duration_ms": elapsed_ms,
        }



def run_isolated_smoke() -> dict:
    """Run all mutating smoke checks against disposable copies of control state."""
    with tempfile.TemporaryDirectory() as directory:
        isolated_root = Path(directory)
        state_dir = isolated_root / "state"
        shutil.copytree(PROJECT_ROOT / "state", state_dir)
        shutil.copytree(PROJECT_ROOT / "continuity", isolated_root / "continuity")
        database_path = isolated_root / "canonical" / "efficiens.db"
        smoke = run_phase0_smoke(database_path)
        routing = run_routing_smoke(
            project_root=isolated_root,
            state_dir=state_dir,
            index_path=isolated_root / "state" / "workflow-routing-index.json",
            database_path=database_path,
        )
        return {
            "database": str(database_path),
            "smoke": smoke,
            "routing": routing,
            "wiki": run_wiki_smoke(project_root=PROJECT_ROOT),
            "script_doc": run_script_doc_check(project_root=PROJECT_ROOT),
        }


def _record_run_check(
    database_path: Path | None,
    *,
    tests_ok: bool,
    test_failures: list[str],
    test_count: int | None = None,
    test_failure_count: int | None = None,
    test_duration_ms: int,
    smoke: dict,
    routing: dict,
    wiki: dict,
    model_or_agent: str | None = None,
    started_at: str | None = None,
    project_root: Path = PROJECT_ROOT,
    source_snapshot_before: dict[str, Any] | None = None,
    source_snapshot_after: dict[str, Any] | None = None,
) -> None:
    """Write a metadata-only telemetry row for the run_checks invocation."""
    target = Path(database_path) if database_path else DEFAULT_DATABASE_PATH
    errors: list[str] = []
    if not tests_ok:
        errors.append("unit_tests_failed")
    if smoke.get("error"):
        errors.append(smoke["error"])
    if routing.get("error"):
        errors.append(routing["error"])
    if wiki and wiki.get("status") != "fresh":
        errors.append("wiki_not_fresh")

    total_duration = test_duration_ms + smoke.get("duration_ms", 0)
    total_duration += routing.get("duration_ms", 0)
    total_duration += wiki.get("duration_ms", 0)

    source_snapshot_after = source_snapshot_after or _safe_correctness_snapshot(project_root)
    source_snapshot_before = source_snapshot_before or source_snapshot_after
    before_fingerprint = source_snapshot_before.get("source_fingerprint")
    after_fingerprint = source_snapshot_after.get("source_fingerprint")
    proof_errors = _source_proof_errors(source_snapshot_before, source_snapshot_after)
    errors.extend(proof_errors)
    fingerprint_available = "source_fingerprint_unavailable" not in proof_errors
    source_drift = "source_changed_during_run" in proof_errors
    resource_usage = {
        "test_count": (
            test_count
            if test_count is not None
            else len(test_failures) + (1 if tests_ok else 0)
        ),
        "test_failure_count": (
            test_failure_count
            if test_failure_count is not None
            else len(test_failures)
        ),
        "smoke_duration_ms": smoke.get("duration_ms", 0),
        "routing_duration_ms": routing.get("duration_ms", 0),
        "wiki_duration_ms": wiki.get("duration_ms", 0),
        "unit_test_duration_ms": test_duration_ms,
        "smoke_entities_delta": smoke.get("delta_entities", 0),
        "smoke_metrics_delta": smoke.get("delta_metrics", 0),
        "source_fingerprint": (
            after_fingerprint if fingerprint_available and not source_drift else None
        ),
        "source_fingerprint_before": before_fingerprint,
        "source_fingerprint_after": after_fingerprint,
        "source_drift": source_drift,
        "source_file_count": source_snapshot_after.get("source_file_count", 0),
        "tested_commit": source_snapshot_after.get("tested_commit"),
        "fingerprint_error": (
            source_snapshot_before.get("fingerprint_error")
            or source_snapshot_after.get("fingerprint_error")
        ),
        "test_failure_ids": [failure_id[:1024] for failure_id in test_failures[:100]],
        "test_failure_ids_sha256": _failure_ids_sha256(test_failures),
    }

    # Sizes are byte counts of the metadata-only inputs and handoff payloads.
    # Payload contents are never stored in run_metrics.
    input_size = _json_payload_size(
        {"tests_ok": tests_ok, "test_failures": test_failures}
    )
    handoff_size = _json_payload_size(
        {"smoke": smoke, "routing": routing, "wiki": wiki}
    )
    resource_usage["input_payload_bytes"] = input_size
    resource_usage["handoff_payload_bytes"] = handoff_size

    try:
        with CanonicalDB(target) as db:
            retries = _previous_retry_count(db)
            db.record_run(
                request_type="run_checks",
                route_selected="deterministic",
                tools_json=["unittest", "phase0_smoke", "routing_smoke", "wiki_smoke"],
                model_or_agent=model_or_agent,
                input_size=input_size,
                handoff_size=handoff_size,
                duration_ms=total_duration,
                resource_usage_json=resource_usage,
                errors_json=errors,
                retries=retries,
                verification_result="pass" if not errors else "fail",
                final_outcome="accepted" if not errors else "rejected",
                acceptance_status="accepted" if not errors else "rejected",
                started_at=started_at,
                completed_at=_utc_now(),
            )
    except Exception:
        # Telemetry must never break the caller's original result.
        pass


def _json_payload_size(payload: object) -> int:
    """Return the UTF-8 byte size of a metadata payload without persisting it."""
    return len(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _previous_retry_count(db: CanonicalDB) -> int:
    """Return the retry count for this run based on the most recent prior row.

    If the most recent prior run_checks row was rejected, this run is a retry:
    return prior retries + 1. If the prior run was accepted (or no prior row
    exists), the retry chain resets to 0. This gives downstream consumers a
    signal of how many consecutive failures preceded a given telemetry row.
    """
    row = db.connection.execute(
        "SELECT retries, acceptance_status FROM run_metrics"
        " WHERE request_type = 'run_checks'"
        " ORDER BY started_at DESC, rowid DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return 0
    prior_retries, prior_status = row[0] or 0, row[1]
    if prior_status == "rejected":
        return prior_retries + 1
    return 0




def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        help="Canonical database path for an explicit persistent smoke check",
    )
    parser.add_argument(
        "--persistent-smoke",
        action="store_true",
        help="Opt in to smoke checks that mutate the specified persistent database and control plane",
    )
    parser.add_argument(
        "--skip-smoke",
        action="store_true",
        help="Run unit tests only",
    )
    parser.add_argument(
        "--record-telemetry",
        action="store_true",
        help="Record a metadata-only run_metrics row after checks complete",
    )
    return parser.parse_args()


def main() -> int:
    arguments = _parse_args()
    started_at = _utc_now()
    active_model = detect_active_model()
    source_snapshot_before = _safe_correctness_snapshot(PROJECT_ROOT)
    (
        tests_ok,
        test_failures,
        test_duration_ms,
        test_count,
        test_failure_count,
    ) = run_tests()
    if not tests_ok:
        source_snapshot_after = _safe_correctness_snapshot(PROJECT_ROOT)
        if arguments.record_telemetry:
            _record_run_check(
                Path(arguments.database) if arguments.database else None,
                tests_ok=False,
                test_failures=test_failures,
                test_count=test_count,
                test_failure_count=test_failure_count,
                test_duration_ms=test_duration_ms,
                smoke={},
                routing={},
                wiki={},
                model_or_agent=active_model,
                started_at=started_at,
                source_snapshot_before=source_snapshot_before,
                source_snapshot_after=source_snapshot_after,
            )
        return 1

    if arguments.skip_smoke:
        source_snapshot_after = _safe_correctness_snapshot(PROJECT_ROOT)
        source_proof_errors = _source_proof_errors(
            source_snapshot_before,
            source_snapshot_after,
        )
        if arguments.record_telemetry:
            _record_run_check(
                Path(arguments.database) if arguments.database else None,
                tests_ok=True,
                test_failures=[],
                test_count=test_count,
                test_failure_count=test_failure_count,
                test_duration_ms=test_duration_ms,
                smoke={},
                routing={},
                wiki={},
                model_or_agent=active_model,
                started_at=started_at,
                source_snapshot_before=source_snapshot_before,
                source_snapshot_after=source_snapshot_after,
            )
        return 1 if source_proof_errors else 0

    if arguments.persistent_smoke:
        if not arguments.database:
            raise ValueError("--persistent-smoke requires --database")
        smoke = run_phase0_smoke(Path(arguments.database))
        routing_smoke = run_routing_smoke(database_path=Path(arguments.database))
        wiki_smoke = run_wiki_smoke()
        script_doc = run_script_doc_check()
    else:
        if arguments.database:
            raise ValueError("--database requires --persistent-smoke")
        isolated = run_isolated_smoke()
        smoke = isolated["smoke"]
        routing_smoke = isolated["routing"]
        wiki_smoke = isolated["wiki"]
        script_doc = isolated["script_doc"]

    print("smoke-check:", smoke)
    print("routing-check:", routing_smoke)
    print("wiki-check:", wiki_smoke)
    print("script-doc-check:", script_doc)
    source_snapshot_after = _safe_correctness_snapshot(PROJECT_ROOT)
    source_proof_errors = _source_proof_errors(
        source_snapshot_before,
        source_snapshot_after,
    )

    if arguments.record_telemetry:
        _record_run_check(
            Path(arguments.database) if arguments.database else None,
            tests_ok=True,
            test_failures=[],
            test_count=test_count,
            test_failure_count=test_failure_count,
            test_duration_ms=test_duration_ms,
            smoke=smoke,
            routing=routing_smoke,
            wiki=wiki_smoke,
            model_or_agent=active_model,
            started_at=started_at,
            source_snapshot_before=source_snapshot_before,
            source_snapshot_after=source_snapshot_after,
        )

    return (
        0
        if smoke["delta_entities"] >= 1
        and smoke["delta_metrics"] >= 1
        and smoke["delta_events"] >= 1
        and not routing_smoke["routing"].get("routing_index_stale", True)
        and wiki_smoke["status"] == "fresh"
        and script_doc.get("ok", False)
        and not source_proof_errors
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
