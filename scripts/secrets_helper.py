#!/usr/bin/env python3
"""Secrets resolution for Efficiens connectors.

Resolution chain (first hit wins):
  1. Environment variable          (CI / containers / per-process override)
  2. Windows Credential Manager    (DPAPI-encrypted, per-user; via `keyring`)
  3. Plain file in canonical/secrets/  (gitignored; last resort)

The plain file is supported for portability but discouraged on this machine:
Credential Manager is the default and preferred store.

Usage:
  python scripts/secrets_helper.py store shopify access_token   # prompts, no echo
  python scripts/secrets_helper.py store shopify store_domain   # prompts, no echo
  python scripts/secrets_helper.py verify shopify               # presence only, never prints values
  python scripts/secrets_helper.py get shopify access_token     # prints value (use sparingly)

Programmatic:
  from scripts.secrets_helper import get_secret, get_connector_secrets
  token = get_secret("shopify", "access_token")
  cfg   = get_connector_secrets("shopify")   # dict of all stored fields
"""

from __future__ import annotations

import getpass
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SECRETS_DIR = PROJECT_ROOT / "canonical" / "secrets"

# Fields a connector config may hold. Values are resolved per-field.
KNOWN_FIELDS = {
    "shopify": {
        "store_domain",
        "access_token",
        "client_id",
        "client_secret",
        "scopes",
        "webhook_url",
    },
}

# Environment variable name for a given (connector, field).
# e.g. EFFICIENS_SHOPIFY_ACCESS_TOKEN
def env_var_name(connector: str, field: str) -> str:
    return f"EFFICIENS_{connector.upper()}_{field.upper()}"


def _keyring_get(connector: str, field: str) -> str | None:
    try:
        import keyring  # type: ignore
    except ImportError:
        return None
    try:
        value = keyring.get_password(f"efficiens/{connector}", field)
    except Exception:
        return None
    return value


def _file_get(connector: str, field: str) -> str | None:
    path = SECRETS_DIR / f"{connector}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = data.get(field)
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return str(value)


def get_secret(connector: str, field: str) -> str | None:
    """Resolve one secret field via env -> keyring -> file."""
    env_value = os.environ.get(env_var_name(connector, field))
    if env_value:
        return env_value
    return _keyring_get(connector, field) or _file_get(connector, field)


def get_connector_secrets(connector: str) -> dict[str, str]:
    """Resolve all known fields for a connector. Missing fields are omitted."""
    fields = KNOWN_FIELDS.get(connector, set())
    resolved: dict[str, str] = {}
    for field in sorted(fields):
        value = get_secret(connector, field)
        if value is not None:
            resolved[field] = value
    return resolved


def _store_keyring(connector: str, field: str, value: str) -> bool:
    try:
        import keyring  # type: ignore
    except ImportError:
        return False
    try:
        keyring.set_password(f"efficiens/{connector}", field, value)
        return True
    except Exception:
        return False


def _store_file(connector: str, field: str, value: str) -> None:
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    path = SECRETS_DIR / f"{connector}.json"
    data: dict = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    data[field] = value
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def store_secret(connector: str, field: str, value: str, *, prefer_keyring: bool = True) -> str:
    """Store a secret. Returns the backend used ('keyring' or 'file')."""
    if prefer_keyring and _store_keyring(connector, field, value):
        return "keyring"
    _store_file(connector, field, value)
    return "file"


def main(argv: list[str] | None = None) -> int:
    argv = list(argv) if argv is not None else sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1

    cmd = argv[0]
    if cmd == "store" and len(argv) == 3:
        connector, field = argv[1], argv[2]
        value = getpass.getpass(f"Value for {connector}.{field} (input hidden): ")
        if not value:
            print("error: empty value, nothing stored", file=sys.stderr)
            return 1
        backend = store_secret(connector, field, value)
        print(f"stored {connector}.{field} in {backend}")
        return 0

    if cmd == "verify" and len(argv) == 2:
        connector = argv[1]
        fields = sorted(KNOWN_FIELDS.get(connector, set()))
        present = [f for f in fields if get_secret(connector, f) is not None]
        missing = [f for f in fields if f not in present]
        print(f"{connector}: {len(present)}/{len(fields)} fields present")
        if present:
            print("  present:", ", ".join(present))
        if missing:
            print("  missing:", ", ".join(missing))
        return 0 if not missing else 1

    if cmd == "get" and len(argv) == 3:
        connector, field = argv[1], argv[2]
        value = get_secret(connector, field)
        if value is None:
            print(f"error: no value for {connector}.{field}", file=sys.stderr)
            return 1
        print(value)
        return 0

    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
