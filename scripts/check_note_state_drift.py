#!/usr/bin/env python3
"""Note/state drift monitor: authored workflow facts must appear in the note.

Each workflow is described twice: ``state/workflows/WF-*.json`` (machine-owned
routing record) and ``continuity/WF-*.md`` (the human-readable note). When an
operator adds a blocker or stop line to the state record and forgets the note,
the note silently becomes a misleading summary of a workflow that is actually
blocked -- and the note is what a human (or an agent restoring context) reads
first.

Scope is deliberately narrow: only **authored** facts are checked.
``dependency_blockers`` and ``graph_dependency_blockers`` are derived by the
router from other workflows' state and re-generated on every refresh; requiring
them in a hand-written note would duplicate a machine-owned field, which
``GOVERNANCE.md`` forbids (one authoritative owner per rule). They are reported
as context only and never cause a failure.

Read-only. Exits 1 when an authored fact is absent from its note.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    # Direct/cron invocation (python scripts/check_note_state_drift.py) has
    # scripts/ -- not the repo root -- on sys.path, so `from scripts import ...`
    # needs the root added, matching the other cron scripts.
    sys.path.insert(0, str(PROJECT_ROOT))
DEFAULT_STATE_DIR = PROJECT_ROOT / "state" / "workflows"
DEFAULT_ACK_PATH = PROJECT_ROOT / "state" / "note-drift-acknowledged.json"

# A fact rewritten in the note (same meaning, different wording) is reported as
# a paraphrase rather than drift: notes are prose and exact echoes of the state
# string are not required. Below this ratio the fact is treated as absent.
#
# This is a LEXICAL check. It cannot recognise that "no paying client
# engagement exists" and "no buyer conversation has taken place" mean the same
# thing, and loosening the threshold until it does would make it accept
# genuinely missing facts. Semantic coverage is instead declared explicitly in
# DEFAULT_ACK_PATH, where a human states which note passage covers the fact.
PARAPHRASE_RATIO = 0.80

# Authored list fields to check. Derived fields are excluded by name below.
CHECKED_FIELDS = ("blockers", "stop_lines")
DERIVED_FIELDS = ("dependency_blockers", "graph_dependency_blockers")


def _utc_now() -> str:
    """Return the current UTC time as a compact ISO-8601 stamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize(text: str) -> str:
    """Lowercase, strip markdown emphasis/bullets, and collapse whitespace.

    Notes bold their blockers and wrap lines at ~80 chars, so a byte comparison
    against the state string fails for reasons that have nothing to do with
    drift. Normalizing both sides makes the comparison about content.
    """
    text = text.lower()
    text = re.sub(r"[*_`]+", "", text)
    text = re.sub(r"^\s*[-+*]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s+", " ", text)
    return text.strip().strip(".")


def _note_blocks(raw_note: str) -> list[str]:
    """Split a note into normalized comparison blocks.

    Notes wrap bullets across several physical lines, so line-by-line matching
    scores a faithful paraphrase as low as ~0.48 and reports false drift. A
    block is one bullet item (including its indented continuation lines) or one
    paragraph, which is the unit a human actually wrote.
    """
    blocks: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            joined = _normalize(" ".join(current))
            if joined:
                blocks.append(joined)
            current.clear()

    for line in raw_note.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("```"):
            flush()
            continue
        starts_item = re.match(r"^\s*[-+*]\s+", line) is not None
        if starts_item:
            flush()
        current.append(stripped)
    flush()
    return blocks


def _classify(fact: str, note_text: str, note_blocks: list[str]) -> tuple[str, float, str | None]:
    """Return (verdict, ratio, closest_block) for one authored fact.

    Verdicts: ``present`` (normalized substring of the note), ``paraphrased``
    (best block similarity >= PARAPHRASE_RATIO), or ``missing``.
    """
    needle = _normalize(fact)
    if not needle:
        return "present", 1.0, None
    if needle in note_text:
        return "present", 1.0, None

    best_ratio = 0.0
    best_block: str | None = None
    for block in note_blocks:
        ratio = difflib.SequenceMatcher(None, needle, block).ratio()
        # A short fact restated inside a long block scores low on whole-string
        # ratio (the block's extra prose counts against it), so also score the
        # best needle-sized window of the block. A sliding window is used
        # rather than find_longest_match, which anchors on whatever common
        # substring happens to be longest and can align the window on the wrong
        # part of the block (measured: 0.594 for a near-verbatim restatement
        # that a sliding window scores 0.920).
        if len(block) > len(needle):
            step = max(1, len(needle) // 8)
            for start in range(0, len(block) - len(needle) + 1, step):
                window = block[start : start + len(needle)]
                ratio = max(ratio, difflib.SequenceMatcher(None, needle, window).ratio())
        if ratio > best_ratio:
            best_ratio, best_block = ratio, block

    verdict = "paraphrased" if best_ratio >= PARAPHRASE_RATIO else "missing"
    return verdict, round(best_ratio, 3), best_block


def _fact_key(workflow_id: str, field: str, fact: str) -> str:
    """Return the stable acknowledgment key for one authored fact.

    Keyed on the NORMALIZED fact text, so re-wording the state record's blocker
    invalidates its acknowledgment and the check fires again -- an ack covers
    one specific claim, never a field in perpetuity.
    """
    digest = hashlib.sha256(_normalize(fact).encode("utf-8")).hexdigest()[:16]
    return f"{workflow_id}:{field}:{digest}"


def load_acknowledgments(ack_path: Path) -> dict[str, dict[str, Any]]:
    """Load acknowledged (semantically covered) facts, keyed by _fact_key."""
    if not ack_path.is_file():
        return {}
    payload = json.loads(ack_path.read_text(encoding="utf-8"))
    return payload.get("acknowledged", {})


def _resolve_note(record: dict[str, Any], project_root: Path) -> Path | None:
    """Resolve a state record's ``continuity_note`` to a path.

    The field is stored with Windows separators; ``PurePath`` handling keeps the
    check portable when the same repo is read on another platform.
    """
    raw = record.get("continuity_note")
    if not raw:
        return None
    return project_root / Path(str(raw).replace("\\", "/"))


def _relative(path: Path, project_root: Path) -> str:
    """Return ``path`` relative to ``project_root`` with forward slashes, never raising.

    ``--state-dir`` may be relative or outside the project, and a record's
    ``continuity_note`` may point anywhere. Reporting must not crash the scan:
    resolve both sides, and fall back to the absolute path when the target is
    outside the root.
    """
    try:
        resolved = path.resolve()
        root = project_root.resolve()
    except OSError:
        return str(path).replace("\\", "/")
    try:
        return str(resolved.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(resolved).replace("\\", "/")


def _inside(path: Path, project_root: Path) -> bool:
    """Return whether ``path`` resolves to a location under ``project_root``."""
    try:
        path.resolve().relative_to(project_root.resolve())
    except (OSError, ValueError):
        return False
    return True


def check_workflow(
    state_path: Path,
    project_root: Path,
    acknowledgments: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compare one workflow's authored facts against its continuity note."""
    acknowledgments = acknowledgments or {}
    record_name = _relative(state_path, project_root)
    try:
        record = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(record, dict):
            raise ValueError("top-level JSON value is not an object")
    except (OSError, ValueError) as error:
        # A corrupt or unreadable record is a finding, not a crash: one bad file
        # must not stop the scan (or the roster check) from reporting.
        return {
            "workflow_id": state_path.stem,
            "state_record": record_name,
            "note": None,
            "missing": [],
            "paraphrased": [],
            "acknowledged": [],
            "derived_facts_skipped": 0,
            "error": f"state record unreadable or invalid: {error}",
        }
    workflow_id = record.get("workflow_id", state_path.stem)
    report: dict[str, Any] = {
        "workflow_id": workflow_id,
        "state_record": record_name,
        "note": None,
        "missing": [],
        "paraphrased": [],
        "acknowledged": [],
        "derived_facts_skipped": 0,
        "error": None,
    }

    note_path = _resolve_note(record, project_root)
    if note_path is None:
        report["error"] = "state record has no continuity_note field"
        return report
    report["note"] = _relative(note_path, project_root)
    if not _inside(note_path, project_root):
        report["error"] = "continuity note path resolves outside the project root"
        return report
    if not note_path.is_file():
        report["error"] = "continuity note file does not exist"
        return report

    try:
        raw_note = note_path.read_text(encoding="utf-8")
    except (OSError, ValueError) as error:
        report["error"] = f"continuity note unreadable: {error}"
        return report
    note_text = _normalize(raw_note)
    note_blocks = _note_blocks(raw_note)

    derived = {
        _normalize(item)
        for field in DERIVED_FIELDS
        for item in record.get(field, []) or []
        if isinstance(item, str)
    }

    for field in CHECKED_FIELDS:
        for fact in record.get(field, []) or []:
            if not isinstance(fact, str):
                continue
            if _normalize(fact) in derived:
                report["derived_facts_skipped"] += 1
                continue
            verdict, ratio, closest = _classify(fact, note_text, note_blocks)
            if verdict == "present":
                continue
            entry = {"field": field, "fact": fact, "best_ratio": ratio, "closest_note_block": closest}
            if verdict == "missing":
                ack = acknowledgments.get(_fact_key(workflow_id, field, fact))
                if ack:
                    report["acknowledged"].append(
                        {**entry, "covered_by": ack.get("covered_by"), "ack_reason": ack.get("reason")}
                    )
                    continue
                entry["ack_key"] = _fact_key(workflow_id, field, fact)
            report["paraphrased" if verdict == "paraphrased" else "missing"].append(entry)

    # effective_status is authored state a reader must not have to infer.
    status = record.get("effective_status")
    if isinstance(status, str) and status and _normalize(status) not in note_text:
        report["missing"].append(
            {"field": "effective_status", "fact": status, "best_ratio": 0.0, "closest_note_block": None}
        )

    return report


def check_roster(project_root: Path, require_fleet: bool = False) -> dict[str, Any]:
    """Verify the WF-1200 note's generated role roster against the registry.

    The registry owns role bindings; the note carries a generated mirror; the
    ``WF-1200`` workflow record is the independent fleet-presence signal. All
    three live under ``project_root`` (``--state-dir`` only selects which
    records are scanned for authored-fact drift). All three must be regular
    files. Any one present while another is missing (or is not a regular file)
    is drift, so removing the artifacts one at a time can never end in a clean
    pass.

    The only skip is "no sign of a fleet at all" when ``require_fleet`` is
    False, which is reserved for projects that genuinely have no fleet (library
    use, ``--allow-no-fleet``). The CLI fails closed by default: a workspace
    that is supposed to have a fleet must not pass because everything was
    deleted.
    """
    registry = project_root / "state" / "fleet-role-registry.json"
    note = project_root / "continuity" / "WF-1200-Agent-Fleet-Roles.md"
    record = project_root / "state" / "workflows" / "WF-1200.json"
    note_rel = _relative(note, project_root)
    artifacts = {"registry": registry, "roster note": note, "WF-1200 record": record}
    present = [name for name, path in artifacts.items() if path.exists()]
    if not present:
        if require_fleet:
            return {
                "status": "drift",
                "reason": "fleet is required but registry, roster note, and WF-1200 record are all absent",
                "note": note_rel,
            }
        return {"status": "skipped", "reason": "no fleet registry, roster note, or WF-1200 record", "note": None}
    broken = [name for name, path in artifacts.items() if not path.is_file()]
    if broken:
        return {
            "status": "drift",
            "reason": f"fleet artifact missing or not a regular file: {', '.join(broken)}",
            "note": note_rel,
        }
    from scripts import fleet_roster_block  # local import: A19 must run from a bare checkout

    result = fleet_roster_block.check_note(registry, note, source=fleet_roster_block.DEFAULT_SOURCE_LABEL)
    return {
        "status": "ok" if result.ok else "drift",
        "reason": result.reason,
        "note": note_rel,
    }


def has_drift(result: dict[str, Any]) -> bool:
    """Return whether a scan result should fail the check."""
    return bool(result["workflows_with_drift"] or result.get("roster_drift"))


def scan(
    state_dir: Path,
    project_root: Path,
    ack_path: Path | None = None,
    require_fleet: bool = False,
) -> dict[str, Any]:
    """Check every workflow state record in ``state_dir``."""
    acknowledgments = load_acknowledgments(ack_path) if ack_path else {}
    reports = [
        check_workflow(path, project_root, acknowledgments)
        for path in sorted(state_dir.glob("WF-*.json"))
    ]
    roster = check_roster(project_root, require_fleet)
    return {
        "generated_at": _utc_now(),
        "workflows_checked": len(reports),
        "workflows_with_drift": sum(1 for r in reports if r["missing"] or r["error"]),
        "roster": roster,
        "roster_drift": 1 if roster["status"] == "drift" else 0,
        "missing_total": sum(len(r["missing"]) for r in reports),
        "paraphrased_total": sum(len(r["paraphrased"]) for r in reports),
        # Surfaced on every run: an acknowledgment suppresses a failure, so it
        # must stay visible rather than silently shrinking the checked surface.
        "acknowledged_total": sum(len(r["acknowledged"]) for r in reports),
        "paraphrase_ratio_threshold": PARAPHRASE_RATIO,
        "workflows": reports,
    }


def main(argv: list[str] | None = None) -> int:
    """Scan for note/state drift; exit 1 when an authored fact is missing."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state-dir", type=Path, default=DEFAULT_STATE_DIR,
        help="directory of WF-*.json state records",
    )
    parser.add_argument(
        "--ack-file", type=Path, default=DEFAULT_ACK_PATH,
        help="JSON file declaring facts a note covers in different words",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="always print the full JSON report, even when green",
    )
    parser.add_argument(
        "--allow-no-fleet", action="store_true",
        help="treat a workspace with no fleet artifacts at all as clean (default: fail closed)",
    )
    args = parser.parse_args(argv)
    require_fleet = not args.allow_no_fleet

    now = _utc_now()
    if not args.state_dir.is_dir():
        # No workflow records to scan, but the roster mirror is independent of
        # them: an early "nothing to check" exit must not mask roster drift.
        roster = check_roster(PROJECT_ROOT, require_fleet)
        result = {
            "generated_at": now,
            "workflows_checked": 0,
            "workflows_with_drift": 0,
            "roster": roster,
            "roster_drift": 1 if roster["status"] == "drift" else 0,
            "workflows": [],
        }
        if has_drift(result):
            print(f"NOTE DRIFT DEGRADED {now}")
            print(json.dumps(result, indent=2))
            return 1
        print(f"NOTE DRIFT OK {now} no_state_dir", file=sys.stderr)
        return 0

    result = scan(args.state_dir, PROJECT_ROOT, args.ack_file, require_fleet)

    # Drift is evaluated BEFORE the empty-scan shortcut: an empty workflow scan
    # still has a roster to verify and must not report it as clean.
    if has_drift(result):
        print(f"NOTE DRIFT DEGRADED {now}")
        print(json.dumps(result, indent=2))
        return 1

    if result["workflows_checked"] == 0:
        # An empty scan is "nothing to check", not "everything verified".
        print(f"NOTE DRIFT OK {now} no_workflows", file=sys.stderr)
        if args.json:
            print(json.dumps(result, indent=2))
        return 0

    scope = (
        f"checked={result['workflows_checked']} "
        f"paraphrased={result['paraphrased_total']} "
        f"acknowledged={result['acknowledged_total']} "
        f"roster={result['roster']['status']}"
    )
    print(f"NOTE DRIFT OK {now} {scope}", file=sys.stderr)
    if args.json:
        print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
