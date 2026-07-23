import unittest
from dataclasses import replace

from sputnik.backtest import run_backtest
from sputnik.config import AppConfig
from sputnik.data import make_demo_market, merge_point_in_time
from sputnik.metrics import calculate_metrics


class BacktestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        prices, macro = make_demo_market(450, seed=9)
        cls.frame = merge_point_in_time(prices, macro)

    def test_backtest_is_deterministic_and_balanced(self):
        config = AppConfig()
        first = run_backtest(self.frame, config)
        second = run_backtest(self.frame, config)
        self.assertAlmostEqual(first.final_equity, second.final_equity)
        self.assertEqual(len(first.trades), len(second.trades))
        self.assertFalse(first.equity["equity"].isna().any())
        self.assertTrue((first.equity["equity"] > 0).all())
        self.assertTrue((first.trade_frame()["holding_bars"] >= 0).all())

    def test_high_entry_threshold_reduces_or_preserves_trade_count(self):
        base = AppConfig()
        loose = run_backtest(self.frame, replace(base, strategy=replace(base.strategy, entry_score=5)))
        strict = run_backtest(self.frame, replace(base, strategy=replace(base.strategy, entry_score=8)))
        self.assertLessEqual(len(strict.trades), len(loose.trades))

    def test_metrics_have_expected_bounds(self):
        config = AppConfig()
        result = run_backtest(self.frame, config)
        metrics = calculate_metrics(result, config.analytics)
        self.assertGreaterEqual(metrics["win_rate"], 0)
        self.assertLessEqual(metrics["win_rate"], 1)
        self.assertLessEqual(metrics["max_drawdown"], 0)
        self.assertGreaterEqual(metrics["exposure"], 0)
        self.assertLessEqual(metrics["exposure"], 1)


if __name__ == "__main__":
    unittest.main()
