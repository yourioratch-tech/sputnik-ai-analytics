import unittest

from sputnik.config import StrategyConfig
from sputnik.data import make_demo_market, merge_point_in_time
from sputnik.strategy import score_ooo


class StrategyTests(unittest.TestCase):
    def test_score_is_bounded_and_reasons_are_auditable(self):
        prices, macro = make_demo_market(200, seed=4)
        scored = score_ooo(merge_point_in_time(prices, macro), StrategyConfig())
        self.assertTrue(scored["score"].between(0, 9).all())
        self.assertTrue(scored["reason"].map(lambda value: isinstance(value, str)).all())

    def test_breakout_uses_prior_window(self):
        prices, macro = make_demo_market(200, seed=5)
        scored = score_ooo(merge_point_in_time(prices, macro), StrategyConfig())
        passed = scored["breakout_pass"]
        self.assertTrue((scored.loc[passed, "close"] > scored.loc[passed, "prior_high"]).all())


if __name__ == "__main__":
    unittest.main()
