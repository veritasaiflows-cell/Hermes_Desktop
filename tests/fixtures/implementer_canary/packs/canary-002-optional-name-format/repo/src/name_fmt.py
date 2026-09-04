def format_display_name(first_name, last_name):
    """Format 'Last, First'; BUG (frozen): raises on None instead of
    treating it as empty."""
    return last_name.strip() + ", " + first_name.strip()
