#!/usr/bin/env python3
"""Cron-friendly operational health gate (A2).

Runs bounded, deterministic operational checks and prints a single short
line to stderr on success or a multi-line diagnostic block on stdout on
failure. Exits 0 on green, non-zero on any failure. Designed for `no_agent`
cron use:

    silent when green -> nothing delivered
    alerts on any error -> the message is the diagnostic

Checks performed:
  1. python scripts/workflow_router.py WF-1000 --answer summary --validate
  2. python scripts/wiki_bootstrap.py validate  (wiki manifest integrity)
  3. python scripts/cron_alias_sweep.py  (alias consistency)
  4. python scripts/cron_registration_validator.py  (cron targets exist)
  5. python scripts/vector_memory_index.py status  (recall index non-empty)

Graph depth checks (orphan sweep, coverage drift) intentionally live on the
hourly A10 job and the A11 brief, not here: one drift should page one job.

This is NOT a code-correctness gate. The full unit/smoke suite is run by
cron_test_gate.py (separate schedule). A2 is a fast operational liveness
probe for the live control plane.

Stale wiki is NOT a hard failure on the watchdog path -- it is reported
as a soft "stale" status so the daily A1 regen can correct it without
alerting. A stale (but populated) vector index is likewise soft: A12
refreshes it every 6h and source-direct fallback exists. Hard failures are
routing errors, alias drift, cron registration drift, an empty or missing
recall index, and uncaught exceptions.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
TIMEOUT_SECONDS = 300


def _run(label: str, args: list[str]) -> dict:
    """Run a bounded subprocess and return a dict: {exit, stdout, stderr}.

    Returning a dict (rather than a positional tuple) keeps the contract
    self-documenting and is what the wrapper tests pin.
    """
    try:
        completed = subprocess.run(
            [PYTHON, *args],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
        return {"exit": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}
    except subprocess.TimeoutExpired:
        return {"exit": 124, "stdout": "", "stderr": f"timeout after {TIMEOUT_SECONDS}s"}
    except Exception as exc:  # pragma: no cover - defensive
        return {"exit": 125, "stdout": "", "stderr": f"spawn failed: {exc}"}


def main() -> int:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    results: list[dict] = []

    # 1. Routing freshness -- lightweight hard gate.
    routing = _run(
        "routing",
        ["scripts/workflow_router.py", "WF-1000", "--answer", "summary", "--validate"],
    )
    routing_status = "unknown"
    if routing["exit"] == 0 and routing["stdout"].strip():
        try:
            routing_status = json.loads(routing["stdout"]).get("routing_freshness", {}).get("status")
        except json.JSONDecodeError:
            pass
    results.append(
        {
            "check": "routing",
            "exit": routing["exit"],
            "passed": routing["exit"] == 0 and routing_status in {"fresh", "cached"},
            "routing_status": routing_status,
            "stderr_tail": routing["stderr"].strip().splitlines()[-3:] if routing["stderr"].strip() else [],
        }
    )

    # 2. Wiki manifest validation -- soft gate (stale is recoverable by A1).
    wiki = _run("wiki_validate", ["scripts/wiki_bootstrap.py", "validate"])
    code, out, err = wiki["exit"], wiki["stdout"], wiki["stderr"]
    wiki_status = None
    if code in (0, 2) and out.strip():
        try:
            wiki_status = json.loads(out).get("status")
        except json.JSONDecodeError:
            pass
    results.append(
        {
            "check": "wiki_validate",
            "exit": code,
            "passed": code == 0,
            "wiki_status": wiki_status,
            "stderr_tail": err.strip().splitlines()[-3:] if err.strip() else [],
        }
    )

    # 3. Alias consistency -- hard gate (dead alias = routing drift).
    aliases = _run("alias_sweep", ["scripts/cron_alias_sweep.py"])
    alias_code = aliases["exit"]
    results.append(
        {
            "check": "alias_sweep",
            "exit": alias_code,
            "passed": alias_code == 0,
            "stderr_tail": aliases["stderr"].strip().splitlines()[-3:] if aliases["stderr"].strip() else [],
        }
    )

    # 4. Cron registration validator -- hard gate (missing targets alert before a job fires).
    cron_reg = _run("cron_registration", ["scripts/cron_registration_validator.py"])
    cron_reg_code = cron_reg["exit"]
    results.append(
        {
            "check": "cron_registration",
            "exit": cron_reg_code,
            "passed": cron_reg_code == 0,
            "stderr_tail": cron_reg["stderr"].strip().splitlines()[-3:] if cron_reg["stderr"].strip() else [],
        }
    )

    # 5. Vector memory index -- hard gate only when empty or missing. A stale
    #    but populated index is a soft warning (A12 refreshes every 6h and
    #    source-direct fallback exists), mirroring the wiki-stale policy.
    #    Graph depth checks (orphan sweep, coverage drift) intentionally
    #    live on the hourly A10 job and the A11 brief, not here.
    vector_status = _run("vector_memory_status", ["scripts/vector_memory_index.py", "status"])
    vector_code = vector_status["exit"]
    vector_payload: dict = {}
    if vector_status["stdout"].strip():
        try:
            parsed = json.loads(vector_status["stdout"])
            if isinstance(parsed, dict):
                vector_payload = parsed
        except json.JSONDecodeError:
            pass
    vector_document_count = vector_payload.get("document_count", 0)
    if not isinstance(vector_document_count, int):
        vector_document_count = 0
    vector_stale_signal = (
        vector_payload.get("status") in {"stale", "degraded"}
        or (
            isinstance(vector_payload.get("stale_source_count"), int)
            and vector_payload["stale_source_count"] > 0
        )
    )
    vector_populated = vector_document_count > 0
    vector_passed = vector_code == 0 and vector_populated
    vector_soft = vector_stale_signal and vector_populated and not vector_passed
    results.append(
        {
            "check": "vector_memory_status",
            "exit": vector_code,
            "passed": vector_passed or vector_soft,
            "stale": vector_soft,
            "document_count": vector_document_count,
            "stderr_tail": vector_status["stderr"].strip().splitlines()[-3:] if vector_status["stderr"].strip() else [],
        }
    )

    hard_fail = [r for r in results if not r["passed"] and r["check"] in {
        "routing",
        "alias_sweep",
        "cron_registration",
        "vector_memory_status",
    }]
    soft_warn = [r for r in results if r["check"] == "wiki_validate" and wiki_status == "stale"]
    soft_warn.extend(r for r in results if r["check"] == "vector_memory_status" and r.get("stale"))

    if hard_fail:
        print(f"HEALTH FAIL {now}")
        print(json.dumps({"hard_fail": hard_fail, "soft_warn": soft_warn}, indent=2))
        return 1

    # Green path: keep STDOUT EMPTY so a no_agent cron stays silent. The OK
    # line goes to stderr for log trails only (not delivered).
    if soft_warn:
        names = ",".join(r["check"] for r in soft_warn)
        print(f"HEALTH OK (soft warnings: {names}) {now}", file=sys.stderr)
        return 0

    print(f"HEALTH OK {now}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
