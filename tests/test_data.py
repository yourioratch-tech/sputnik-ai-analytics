import unittest

import pandas as pd

from sputnik.data import make_demo_market, merge_point_in_time, validate_prices


class DataTests(unittest.TestCase):
    def test_demo_data_validates(self):
        prices, macro = make_demo_market(120, seed=1)
        merged = merge_point_in_time(prices, macro)
        self.assertEqual(len(merged), 120)
        self.assertIn("oil", merged)

    def test_future_macro_is_not_joined(self):
        prices = pd.DataFrame(
            {
                "date": ["2026-01-02"],
                "open": [5],
                "high": [6],
                "low": [4],
                "close": [5.5],
                "volume": [100],
            }
        )
        macro = pd.DataFrame({"date": ["2026-01-03"], "oil": [80]})
        merged = merge_point_in_time(prices, macro)
        self.assertTrue(pd.isna(merged.iloc[0]["oil"]))

    def test_invalid_high_fails(self):
        prices = pd.DataFrame(
            {
                "date": ["2026-01-02"],
                "open": [5],
                "high": [4],
                "low": [3],
                "close": [5.5],
                "volume": [100],
            }
        )
        with self.assertRaisesRegex(ValueError, "high"):
            validate_prices(prices)


if __name__ == "__main__":
    unittest.main()
