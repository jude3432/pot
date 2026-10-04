import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from config.currency import old_to_new, new_to_old, require_new_integer, require_old_multiple_of_rate


class CurrencyConversionTests(unittest.TestCase):
    def test_new_to_ichancy_old(self):
        self.assertEqual(new_to_old(250), 25000)
        self.assertEqual(new_to_old(1), 100)

    def test_ichancy_old_to_new(self):
        self.assertEqual(old_to_new(25000), Decimal("250.00"))
        self.assertEqual(old_to_new(100), Decimal("1.00"))

    def test_invalid_fractional_or_non_multiple_amounts(self):
        with self.assertRaises(ValueError):
            require_new_integer("250.5")
        with self.assertRaises(ValueError):
            require_old_multiple_of_rate(25050)
        with self.assertRaises(ValueError):
            new_to_old(-1)

    def test_round_trip(self):
        for amount in (1, 250, 10000):
            self.assertEqual(int(old_to_new(new_to_old(amount))), amount)


if __name__ == "__main__":
    unittest.main()
