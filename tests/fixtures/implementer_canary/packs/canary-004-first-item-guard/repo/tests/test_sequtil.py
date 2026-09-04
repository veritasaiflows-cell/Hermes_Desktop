import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.sequtil import first_item

class FirstItemTests(unittest.TestCase):
    def test_returns_first_element(self):
        self.assertEqual(first_item([3, 1, 2]), 3)

    def test_treats_none_as_missing(self):
        self.assertIsNone(first_item(None))

    def test_empty_sequence_is_missing(self):
        self.assertIsNone(first_item([]))
