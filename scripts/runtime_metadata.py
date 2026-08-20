"""Runtime metadata helpers shared across deterministic workflow scripts.

These functions are intentionally free of database, network, and I/O
dependencies so they can be imported anywhere without triggering side effects.
"""

from __future__ import annotations

import os


def detect_active_model() -> str | None:
    """Return the active model/provider label if available from the environment.

    Hermes desktop injects runtime metadata via ``HERMES_RUNTIME_MODEL`` and
    ``HERMES_RUNTIME_PROVIDER``. We prefer a combined label when both are
    present so downstream telemetry can distinguish model and provider;
    otherwise we fall back to whichever single variable is set.

    The returned label uses the format ``model:provider``, which passes the
    canonical database's category-token whitelist
    (``[a-z0-9_.:\\-]+``, max 40 chars).
    """
    runtime_model = os.environ.get("HERMES_RUNTIME_MODEL", "").strip()
    runtime_provider = os.environ.get("HERMES_RUNTIME_PROVIDER", "").strip()
    if runtime_model and runtime_provider:
        return f"{runtime_model}:{runtime_provider}"
    if runtime_model:
        return runtime_model
    for key in ("HERMES_ACTIVE_MODEL", "HERMES_MODEL", "MODEL_NAME"):
        value = os.environ.get(key)
        if value:
            return value.strip()
    return None
