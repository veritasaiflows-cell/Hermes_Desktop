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
  5. python scripts/graph_memory.py validate  (graph orphan/contradiction sweep)
  6. python scripts/cron_graph_freshness.py  (graph coverage drift)
  7. python scripts/vector_memory_index.py status  (recall index non-empty)

This is NOT a code-correctness gate. The full unit/smoke suite is run by
cron_test_gate.py (separate schedule). A2 is a fast operational liveness
probe for the live control plane.

Stale wiki is NOT a hard failure on the watchdog path -- it is reported
as a soft "stale" status so the daily A1 regen can correct it without
alerting. Hard failures are routing errors, alias drift, graph integrity
or coverage drift, empty recall index, and uncaught exceptions.
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

    # 5. Graph integrity sweep -- hard gate (orphan edges or duplicate active
    #    triples mean the durable graph has drifted from canonical state).
    graph = _run("graph_validate", ["scripts/graph_memory.py", "validate"])
    graph_code = graph["exit"]
    results.append(
        {
            "check": "graph_validate",
            "exit": graph_code,
            "passed": graph_code == 0,
            "stderr_tail": graph["stderr"].strip().splitlines()[-3:] if graph["stderr"].strip() else [],
        }
    )

    # 6. Graph coverage drift -- hard gate (canonical records that should have
    #    edges but do not, e.g. a workflow run whose output is not wired).
    graph_freshness = _run("graph_freshness", ["scripts/cron_graph_freshness.py"])
    graph_freshness_code = graph_freshness["exit"]
    results.append(
        {
            "check": "graph_freshness",
            "exit": graph_freshness_code,
            "passed": graph_freshness_code == 0,
            "stderr_tail": graph_freshness["stderr"].strip().splitlines()[-3:] if graph_freshness["stderr"].strip() else [],
        }
    )

    # 7. Vector memory index non-empty -- hard gate (recall must be populated).
    vector_status = _run("vector_memory_status", ["scripts/vector_memory_index.py", "status"])
    vector_code = vector_status["exit"]
    vector_document_count = 0
    if vector_code == 0 and vector_status["stdout"].strip():
        try:
            vector_document_count = json.loads(vector_status["stdout"]).get("document_count", 0)
        except json.JSONDecodeError:
            pass
    results.append(
        {
            "check": "vector_memory_status",
            "exit": vector_code,
            "passed": vector_code == 0 and vector_document_count > 0,
            "document_count": vector_document_count,
            "stderr_tail": vector_status["stderr"].strip().splitlines()[-3:] if vector_status["stderr"].strip() else [],
        }
    )

    hard_fail = [r for r in results if not r["passed"] and r["check"] in {
        "routing",
        "alias_sweep",
        "cron_registration",
        "graph_validate",
        "graph_freshness",
        "vector_memory_status",
    }]
    soft_warn = [r for r in results if r["check"] == "wiki_validate" and wiki_status == "stale"]

    if hard_fail:
        print(f"HEALTH FAIL {now}")
        print(json.dumps({"hard_fail": hard_fail, "soft_warn": soft_warn}, indent=2))
        return 1

    # Green path: keep STDOUT EMPTY so a no_agent cron stays silent. The OK
    # line goes to stderr for log trails only (not delivered).
    if soft_warn:
        print(f"HEALTH OK (wiki stale) {now}", file=sys.stderr)
        return 0

    print(f"HEALTH OK {now}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
