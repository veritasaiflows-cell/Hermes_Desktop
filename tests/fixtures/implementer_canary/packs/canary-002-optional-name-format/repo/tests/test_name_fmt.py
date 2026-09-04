import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.name_fmt import format_display_name

class FormatDisplayNameTests(unittest.TestCase):
    def test_formats_full_name(self):
        self.assertEqual(format_display_name("Ada", "Lovelace"), "Lovelace, Ada")

    def test_treats_none_as_empty(self):
        self.assertEqual(format_display_name("Ada", None), ", Ada")
        self.assertEqual(format_display_name(None, "Lovelace"), "Lovelace, ")

    def test_strips_surrounding_whitespace(self):
        self.assertEqual(format_display_name("  Ada ", " Lovelace "), "Lovelace, Ada")
