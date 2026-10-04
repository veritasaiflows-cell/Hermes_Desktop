#!/usr/bin/env python3
"""QA review driver: run a formal review on a pinned route and VERIFY the model that actually ran.

The admission gate checks only the *declared* reviewer model. This driver closes the failover
gap: it pins the route (``--provider`` / ``-m``), then reads the session record to learn the model
that really answered, and fails closed when that model is unknown, differs from the pinned route,
or equals the lane author's model.

Exit codes: 0 verified, 1 failed (model ran but verification failed), 2 refused (gate/request).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import helper_agent_router as gate  # noqa: E402

RECORD_SCHEMA = "qa-review-record.v1"
_SESSION_RE = re.compile(r"^\s*session_id:\s*(\S+)\s*$", re.M)

Runner = Callable[[list[str]], "tuple[int, str, str]"]
Exporter = Callable[[str], "str | None"]


def parse_session_id(stderr: str) -> str | None:
    """Return the session id the CLI printed on stderr, or None."""
    match = _SESSION_RE.search(stderr or "")
    return match.group(1) if match else None


def effective_model(export_text: str) -> str | None:
    """Return ``provider/model`` from an exported session record, or None when unknown."""
    for line in (export_text or "").splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        model, provider = record.get("model"), record.get("billing_provider")
        if isinstance(model, str) and model.strip() and isinstance(provider, str) and provider.strip():
            return f"{provider.strip()}/{model.strip()}"
    return None


def _split_route(model: str) -> tuple[str, str]:
    provider, _, name = model.partition("/")
    return (provider, name) if name else ("", provider)


def _default_runner(command: list[str]) -> tuple[int, str, str]:
    completed = subprocess.run(command, capture_output=True, text=True, timeout=1500)
    return completed.returncode, completed.stdout, completed.stderr


def _default_exporter_for(profile: str) -> Exporter:
    def export(session_id: str) -> str | None:
        # Export must run under the SAME profile that created the session.
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "session.jsonl"
            subprocess.run(
                ["hermes", "-p", profile, "sessions", "export", str(target), "--session-id", session_id],
                capture_output=True, text=True, timeout=120,
            )
            return target.read_text(encoding="utf-8") if target.is_file() else None

    return export


def _refuse(reasons: list[str]) -> dict[str, Any]:
    return {"status": "refused", "problems": reasons, "record": None}


def run_review(
    request: dict[str, Any],
    prompt_file: Path,
    out_path: Path,
    *,
    project_root: Path = PROJECT_ROOT,
    runner: Runner = _default_runner,
    exporter: Exporter | None = None,
) -> dict[str, Any]:
    """Admit, run, verify. Writes ``out_path`` only after a model call was attempted."""
    if request.get("role") != "qa" or request.get("task_class") != "review":
        return _refuse(["driver only runs role=qa, task_class=review requests"])
    admission = gate.admit_request(request, project_root=project_root)
    if admission["status"] != "admitted":
        return _refuse([f"gate: {reason}" for reason in admission["reasons"]])

    reviews_lane = request["reviews_lane"]
    row = gate._read_lane_row(project_root, reviews_lane)
    author = row["expected_model"] if row is not None else None
    if not isinstance(author, str) or not author.strip():
        return _refuse([f"reviewed lane {reviews_lane!r} has no recorded author model"])

    requested = str(request["model"]).strip()
    provider, name = _split_route(requested)
    if not provider:
        registry, _ = gate._load_role_registry(project_root)
        for route in gate._qa_routes(registry["roles"]["qa"]):
            if name in gate._route_keys(route):
                provider, name = str(route.get("provider")), str(route.get("model"))
                break
    pinned = f"{provider}/{name}"

    prompt = prompt_file.read_text(encoding="utf-8")
    command = ["hermes", "-p", "qa", "--provider", provider, "-m", name, "chat", "-Q", "--query-file", str(prompt_file)]
    returncode, stdout, stderr = runner(command)

    problems: list[str] = []
    if returncode != 0:
        problems.append(f"review process exited {returncode}")
    session_id = parse_session_id(stderr)
    export_text = (exporter or _default_exporter_for("qa"))(session_id) if session_id else None
    effective = effective_model(export_text) if export_text else None
    if effective is None:
        problems.append("effective model could not be determined from the session record")
    else:
        if gate._norm_model(effective) != gate._norm_model(pinned) or not effective.startswith(provider + "/"):
            problems.append(f"effective model {effective!r} differs from the pinned route {pinned!r}")
        if gate._norm_model(effective) == gate._norm_model(author):
            problems.append(f"effective model {effective!r} matches the lane author model {author!r}")

    verified = not problems
    record = {
        "schema": RECORD_SCHEMA,
        "reviews_lane": reviews_lane,
        "author_model": author,
        "requested_route": pinned,
        "effective_model": effective,
        "effective_model_verified": verified,
        "session_id": session_id,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "exit": returncode,
        "problems": problems,
        "verbatim_response": stdout,
    }
    out_path.write_text(json.dumps(record, indent=1), encoding="utf-8")
    return {"status": "verified" if verified else "failed", "problems": problems, "record": record}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--prompt-file", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args(argv)
    request = json.loads(args.request.read_text(encoding="utf-8"))
    result = run_review(request, args.prompt_file, args.out, project_root=args.project_root)
    print(json.dumps({k: v for k, v in result.items() if k != "record"}, indent=1))
    return {"verified": 0, "failed": 1}.get(result["status"], 2)


if __name__ == "__main__":
    raise SystemExit(main())
