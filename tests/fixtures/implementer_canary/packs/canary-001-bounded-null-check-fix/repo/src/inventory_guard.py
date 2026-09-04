"""Fixture-only inventory reorder guard used by the implementer canary.

Frozen for case canary-001-bounded-null-check-fix. This module is not part of
the production workspace; it exists solely to give a candidate Implementer
Bot one bounded, deterministic single-file bug to fix under a leased,
disposable sandbox.
"""


def needs_reorder(quantity_on_hand, reorder_threshold):
    """Return True when stock has fallen to or below the reorder threshold.

    BUG (intentional, frozen): this function has no guard for an unknown
    on-hand quantity. When `quantity_on_hand` is None it must be treated as
    "needs reorder review" (return True) rather than raising TypeError. The
    existing `<=` comparison behavior for known numeric quantities must be
    preserved exactly.
    """
    return quantity_on_hand <= reorder_threshold
