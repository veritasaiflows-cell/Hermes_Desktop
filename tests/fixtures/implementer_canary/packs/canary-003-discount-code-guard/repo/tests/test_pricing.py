import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.pricing import price_after_discount

class PriceAfterDiscountTests(unittest.TestCase):
    def test_applies_known_code(self):
        self.assertEqual(price_after_discount(100, "SAVE10"), 90)

    def test_treats_none_code_as_full_price(self):
        self.assertEqual(price_after_discount(100, None), 100)

    def test_unknown_code_is_full_price(self):
        self.assertEqual(price_after_discount(100, "NOPE"), 100)

    def test_code_matching_is_case_insensitive(self):
        self.assertEqual(price_after_discount(200, "save25"), 150)
