import pandas as pd

from sputnik.gap_study import run_weekend_gap_study
from sputnik.models import RegimeRule


def market_frame(periods=100):
    dates = pd.bdate_range("2025-01-01", periods=periods, tz="UTC")
    frame = pd.DataFrame(
        {
            "date": dates,
            "open": 100.0,
            "high": 102.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1000.0,
        }
    )
    frame.loc[frame["date"].dt.weekday == 0, "open"] = 101.0
    return frame


def factor_frame(periods=100):
    frame = market_frame(periods)
    frame["close"] = [100 * (1.01**index) for index in range(periods)]
    frame["high"] = frame[["open", "close"]].max(axis=1) + 1
    frame["low"] = frame[["open", "close"]].min(axis=1) - 1
    return frame


def test_weekend_gap_frequency_and_regime_rule_are_explicit():
    report = run_weekend_gap_study(
        {"ASX:OOO": market_frame()},
        {"oil": factor_frame()},
        [RegimeRule(field="oil_return", operator="gt", value=0)],
        cost_bps=10,
        minimum_samples=5,
    )
    result = report["symbols"]["ASX:OOO"]
    assert result["baseline"]["samples"] > 10
    assert result["baseline"]["historical_positive_rate"] == 1.0
    assert result["baseline"]["probability_type"] == "historical_frequency_not_forecast"
    assert result["conditioned"]["research_gate"] == "pass"
    assert "NO BROKER" in report["execution_boundary"]
