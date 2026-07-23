from __future__ import annotations

import numpy as np
import pandas as pd

from .config import StrategyConfig


def true_range(frame: pd.DataFrame) -> pd.Series:
    previous_close = frame["close"].shift(1)
    return pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def atr(frame: pd.DataFrame, period: int) -> pd.Series:
    return true_range(frame).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def build_features(frame: pd.DataFrame, config: StrategyConfig) -> pd.DataFrame:
    result = frame.copy()
    result["ema_fast"] = result["close"].ewm(
        span=config.ema_fast, adjust=False, min_periods=config.ema_fast
    ).mean()
    result["ema_slow"] = result["close"].ewm(
        span=config.ema_slow, adjust=False, min_periods=config.ema_slow
    ).mean()
    result["atr"] = atr(result, config.atr_period)
    result["prior_high"] = (
        result["high"].rolling(config.breakout_period, min_periods=config.breakout_period).max().shift(1)
    )
    result["volume_average"] = result["volume"].rolling(
        config.volume_period, min_periods=config.volume_period
    ).mean()
    result["volume_ratio"] = result["volume"] / result["volume_average"].replace(0, np.nan)
    result["ooo_momentum"] = result["close"].pct_change(config.momentum_period)

    if "oil" in result:
        result["oil_momentum"] = result["oil"].pct_change(config.momentum_period)
    else:
        result["oil_momentum"] = np.nan
    if "dxy" in result:
        result["dxy_momentum"] = result["dxy"].pct_change(config.momentum_period)
    else:
        result["dxy_momentum"] = np.nan
    if "us10y" in result:
        result["yield_change"] = result["us10y"].diff(config.momentum_period)
    else:
        result["yield_change"] = np.nan
    return result
