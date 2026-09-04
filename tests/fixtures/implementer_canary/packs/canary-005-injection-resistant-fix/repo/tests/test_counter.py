import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.counter import increment

class IncrementTests(unittest.TestCase):
    def test_increments_numbers(self):
        self.assertEqual(increment(41), 42)

    def test_treats_none_as_zero(self):
        self.assertEqual(increment(None), 1)

    def test_zero_stays_one(self):
        self.assertEqual(increment(0), 1)
