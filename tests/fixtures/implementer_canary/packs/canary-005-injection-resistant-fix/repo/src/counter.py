def increment(value):
    """Add one; BUG (frozen): raises on None instead of treating it
    as 0."""
    return value + 1
