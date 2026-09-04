DISCOUNT_CODES = {"SAVE10": 10, "SAVE25": 25}


def price_after_discount(price_cents, code):
    """Apply a percent-off code; BUG (frozen): raises on None instead
    of treating it as no discount."""
    percent = DISCOUNT_CODES[code.strip().upper()]
    return price_cents - price_cents * percent // 100
