#!/usr/bin/env python3
"""Generate and verify the WF-1200 role-roster block in the continuity note.

``state/fleet-role-registry.json`` is the single owner of the role -> model
bindings (the admission gate enforces it). The continuity note used to carry a
hand-written copy of that table, which went stale the moment a binding changed.
This tool renders the table from the registry between BEGIN/END markers so the
note is a generated mirror, and ``--check`` (also run by A19) fails when the
mirror differs from what the registry would produce.

Safety contract for ``--write``:
* Only the marked region is rewritten; surrounding prose is preserved byte for
  byte. Markers must be exact, line-anchored, and appear exactly once each in
  BEGIN-then-END order. Anything else is *refused*, never guessed at.
* A note with mixed or stray line endings, or invalid UTF-8, is refused.
* The note is replaced atomically (temp file in the same directory +
  ``os.replace``); a failed write leaves the original untouched and no temp file.
* The provenance line names the registry actually used, so a custom
  ``--registry`` cannot masquerade as the live one.

The block is deterministic and carries no timestamps, so a check is a plain
string comparison. It deliberately does NOT state lane-exception *consumption*:
that is lane-register state, owned by ``concurrent_lane_manager.py``.

Exit codes: ``--check`` 0 in sync / 1 drift; ``--write`` 0 ok / 2 refused.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
DEFAULT_REGISTRY = PROJECT_ROOT / "state" / "fleet-role-registry.json"
DEFAULT_NOTE = PROJECT_ROOT / "continuity" / "WF-1200-Agent-Fleet-Roles.md"
DEFAULT_SOURCE_LABEL = "state/fleet-role-registry.json"

BEGIN_PREFIX = "<!-- BEGIN GENERATED: fleet-role-roster"
END_MARKER = "<!-- END GENERATED: fleet-role-roster -->"
_BEGIN_RE = re.compile(
    r"^<!-- BEGIN GENERATED: fleet-role-roster(?: \(source: [^)\n]*\))? -->[ \t]*$", re.MULTILINE
)
_END_RE = re.compile(r"^<!-- END GENERATED: fleet-role-roster -->[ \t]*$", re.MULTILINE)


def begin_marker(source: str = DEFAULT_SOURCE_LABEL) -> str:
    return f"{BEGIN_PREFIX} (source: {source}; do not edit by hand) -->"


BEGIN_MARKER = begin_marker()

ROLE_ORDER = ("governor", "architect", "implementer", "senior_engineer", "qa", "researcher")


class RosterError(ValueError):
    """The registry or note cannot be rendered/edited safely."""


@dataclass(frozen=True)
class RosterCheck:
    ok: bool
    reason: str = ""


def _binding(entry: dict[str, Any]) -> str:
    return f"{entry['provider']}/{entry['model']}"


def _ordered_roles(roles: dict[str, Any]) -> list[str]:
    known = [name for name in ROLE_ORDER if name in roles]
    return known + sorted(name for name in roles if name not in ROLE_ORDER)


def _gate(entry: dict[str, Any]) -> str:
    parts: list[str] = []
    if "min_lane_retry_count" in entry:
        parts.append(f"repair cycle >= {entry['min_lane_retry_count']}")
    if "max_lane_retry_count" in entry:
        parts.append(f"repair cycle <= {entry['max_lane_retry_count']}")
    return "; ".join(parts) or "-"


def _source_label(registry_path: Path) -> str:
    """Name the registry honestly and unambiguously.

    The canonical relative path is used only for the live registry. Any other
    registry gets ``custom:<sanitized-name>@<hash>``. Both the displayed name and
    the hash come from the same case-normalized resolved path, so two spellings
    of one file (``registry.json`` vs ``REGISTRY.JSON`` on a case-insensitive
    filesystem, or ``./registry.json``) cannot yield different labels, while two
    different files that share a basename still can. No path character (such as
    ``)``) can break the marker line.
    """
    try:
        resolved = registry_path.resolve()
        if resolved == DEFAULT_REGISTRY.resolve():
            return DEFAULT_SOURCE_LABEL
    except OSError:
        resolved = registry_path.absolute()
    normalized = os.path.normcase(str(resolved))
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(normalized)) or "registry"
    return f"custom:{safe_name}@{digest}"


_UNSAFE_CELL = re.compile(r"[\x00-\x1f\x7f|`]")


def _text(value: Any, what: str) -> str:
    """Require a non-empty string that cannot break the Markdown table or marker."""
    if not isinstance(value, str) or not value.strip():
        raise RosterError(f"{what} must be a non-empty string, got {value!r}")
    if _UNSAFE_CELL.search(value) or "-->" in value or "<!--" in value:
        raise RosterError(f"{what} contains characters that would break the generated Markdown: {value!r}")
    return value


def _strict_int(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RosterError(f"{what} must be an integer, got {value!r}")
    return value


def _validate(registry: Any) -> None:
    """Validate the whole registry shape up front; never coerce a wrong type to 'none'."""
    if not isinstance(registry, dict):
        raise RosterError("registry is not a mapping")
    roles = registry.get("roles")
    if not isinstance(roles, dict) or not roles:
        raise RosterError("registry has no 'roles' mapping")
    for name, entry in roles.items():
        _text(name, "role name")
        if not isinstance(entry, dict):
            raise RosterError(f"role {name!r} is not a mapping")
        for key in ("profile", "provider", "model", "status"):
            _text(entry.get(key), f"role {name!r} field {key!r}")
        if "modes" in entry:
            if not isinstance(entry["modes"], list):
                raise RosterError(f"role {name!r} field 'modes' must be a list")
            for mode in entry["modes"]:
                _text(mode, f"role {name!r} mode")
        for key in ("min_lane_retry_count", "max_lane_retry_count"):
            if key in entry:
                _strict_int(entry[key], f"role {name!r} field {key!r}")
        if "fallback_providers" in entry:
            if not isinstance(entry["fallback_providers"], list):
                raise RosterError(f"role {name!r} field 'fallback_providers' must be a list")
            for item in entry["fallback_providers"]:
                if not isinstance(item, dict):
                    raise RosterError(f"role {name!r} fallback entry is not a mapping: {item!r}")
                _text(item.get("provider"), f"role {name!r} fallback provider")
                _text(item.get("model"), f"role {name!r} fallback model")
    if "lane_exceptions" in registry:
        exceptions = registry["lane_exceptions"]
        if not isinstance(exceptions, list):
            raise RosterError("registry 'lane_exceptions' must be a list")
        for item in exceptions:
            if not isinstance(item, dict):
                raise RosterError(f"lane exception entry is not a mapping: {item!r}")
            _text(item.get("role"), "lane exception role")
            _text(item.get("lane_id"), "lane exception lane_id")


def render_block(registry: dict[str, Any], source: str = DEFAULT_SOURCE_LABEL) -> str:
    """Render the deterministic roster block for ``registry``."""
    _text(source, "source label")
    if ")" in source:
        raise RosterError("source label may not contain ')'")
    _validate(registry)
    roles = registry["roles"]

    lines = [
        begin_marker(source),
        "",
        f"Generated from `{source}` (the owner; the admission gate",
        "enforces the registry, not this table). Regenerate with",
        "`python scripts/fleet_roster_block.py --write`. Do not edit by hand.",
        "",
        "| Role | Profile | Binding | Registry status | Modes | Lane retry gate | Fallback chain |",
        "|---|---|---|---|---|---|---|",
    ]
    for name in _ordered_roles(roles):
        entry = roles[name]
        modes = ", ".join(entry.get("modes", [])) or "-"
        chain = " -> ".join(f"`{_binding(item)}`" for item in entry.get("fallback_providers", [])) or "-"
        lines.append(
            f"| {name} | `{entry['profile']}` | `{_binding(entry)}` | "
            f"{entry['status']} | {modes} | {_gate(entry)} | {chain} |"
        )
    fallbacks = roles.get("governor", {}).get("fallback_providers", [])
    lines.append("")
    if fallbacks:
        chain = ", ".join(f"`{_binding(item)}`" for item in fallbacks)
        lines.append(f"Governor parent-only fallback chain: {chain}.")
    else:
        lines.append("Governor parent-only fallback chain: none.")

    exceptions = registry.get("lane_exceptions", [])
    lines.append("")
    if exceptions:
        lines.append("Recorded one-time lane exceptions (lane register owns their status):")
        for item in exceptions:
            lines.append(f"- `{item['role']}` / `{item['lane_id']}`")
    else:
        lines.append("Recorded one-time lane exceptions: none.")
    lines.extend(["", END_MARKER])
    return "\n".join(lines) + "\n"


def _locate(note_text: str) -> tuple[int, int] | None:
    """Return the (start, end) span of the marked block, None if no markers, or raise."""
    begins = list(_BEGIN_RE.finditer(note_text))
    ends = list(_END_RE.finditer(note_text))
    if not begins and not ends:
        return None
    if len(begins) != 1 or len(ends) != 1:
        raise RosterError(
            f"malformed markers: found {len(begins)} BEGIN and {len(ends)} END (exactly 1 of each required)"
        )
    if ends[0].start() < begins[0].start():
        raise RosterError("malformed markers: END precedes BEGIN")
    end = ends[0].end()
    if note_text[end : end + 1] == "\n":
        end += 1
    return begins[0].start(), end


def extract_block(note_text: str) -> str | None:
    """Return the marked block (inclusive) from LF-normalized text, or None."""
    span = _locate(note_text)
    return None if span is None else note_text[span[0] : span[1]]


def apply_block(note_text: str, block: str) -> str:
    """Replace the marked region with ``block``; refuse absent or malformed markers."""
    span = _locate(note_text)
    if span is None:
        raise RosterError("note has no BEGIN/END fleet-role-roster markers")
    return note_text[: span[0]] + block + note_text[span[1] :]


def check_note(registry_path: Path, note_path: Path, source: str | None = None) -> RosterCheck:
    """Compare the note's block with what the registry renders."""
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return RosterCheck(False, f"registry unreadable: {error}")
    try:
        expected = render_block(registry, source or _source_label(registry_path))
    except RosterError as error:
        return RosterCheck(False, f"registry cannot be rendered: {error}")
    if not note_path.is_file():
        return RosterCheck(False, f"note file does not exist: {note_path.name}")
    try:
        text = note_path.read_bytes().decode("utf-8")
    except (OSError, ValueError) as error:
        return RosterCheck(False, f"note unreadable: {error}")
    try:
        actual = extract_block(text.replace("\r\n", "\n"))
    except RosterError as error:
        return RosterCheck(False, f"note {error}")
    if actual is None:
        return RosterCheck(False, "note has no BEGIN/END fleet-role-roster markers")
    if actual != expected:
        return RosterCheck(False, "roster block is out of date versus the registry; run --write")
    return RosterCheck(True)


def _line_ending(raw: bytes) -> str:
    """Return '\\n' or '\\r\\n' for a uniform note; refuse mixed or stray-CR endings."""
    crlf = raw.count(b"\r\n")
    lone_lf = raw.count(b"\n") - crlf
    stray_cr = raw.count(b"\r") - crlf
    if stray_cr or (crlf and lone_lf):
        raise RosterError("note has mixed or stray line endings; normalize it before --write")
    return "\r\n" if crlf else "\n"


def _atomic_write(path: Path, data: bytes) -> None:
    """Replace ``path`` with ``data`` via a same-directory temp file."""
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        shutil.copymode(path, temp_name)
        os.replace(temp_name, path)
    except BaseException:
        # A read-only target makes copymode() leave a read-only temp file on
        # Windows, and unlink() then fails; clear the attribute first so a failed
        # write never leaves a full-content temp file behind.
        for cleanup in (lambda: os.chmod(temp_name, stat.S_IREAD | stat.S_IWRITE), lambda: os.unlink(temp_name)):
            try:
                cleanup()
            except OSError:
                pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--note", type=Path, default=DEFAULT_NOTE)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="exit 1 if the note block is stale")
    mode.add_argument("--write", action="store_true", help="regenerate the marked block")
    args = parser.parse_args(argv)

    if args.check:
        result = check_note(args.registry, args.note)
        if result.ok:
            print("ROSTER OK", file=sys.stderr)
            return 0
        print(f"ROSTER DRIFT: {result.reason}")
        return 1

    try:
        registry = json.loads(args.registry.read_text(encoding="utf-8"))
        block = render_block(registry, _source_label(args.registry))
        raw = args.note.read_bytes()
        text = raw.decode("utf-8")
        eol = _line_ending(raw)
        updated = apply_block(text.replace("\r\n", "\n"), block)
        if eol == "\r\n":
            updated = updated.replace("\n", "\r\n")
        updated_bytes = updated.encode("utf-8")
        if updated_bytes != raw:
            _atomic_write(args.note, updated_bytes)
    except (OSError, ValueError) as error:
        print(f"ROSTER WRITE REFUSED: {error}", file=sys.stderr)
        return 2
    print("ROSTER WRITTEN", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
