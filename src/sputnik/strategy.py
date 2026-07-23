from __future__ import annotations

import pandas as pd

from .config import StrategyConfig
from .indicators import build_features


def score_ooo(frame: pd.DataFrame, config: StrategyConfig) -> pd.DataFrame:
    result = build_features(frame, config)

    result["trend_pass"] = (
        (result["close"] > result["ema_fast"]) & (result["ema_fast"] > result["ema_slow"])
    )
    result["oil_pass"] = result["oil_momentum"].ge(config.oil_momentum_min).fillna(False)
    result["dxy_pass"] = result["dxy_momentum"].le(config.dxy_momentum_max).fillna(False)
    result["yield_pass"] = result["yield_change"].le(config.yield_change_max).fillna(False)
    result["breakout_pass"] = result["close"].gt(result["prior_high"]).fillna(False)
    result["volume_pass"] = result["volume_ratio"].ge(config.volume_ratio_min).fillna(False)

    result["score"] = (
        result["trend_pass"].astype(float) * 2.0
        + result["oil_pass"].astype(float) * 2.0
        + result["dxy_pass"].astype(float)
        + result["yield_pass"].astype(float)
        + result["breakout_pass"].astype(float) * 2.0
        + result["volume_pass"].astype(float)
    )
    result["entry_signal"] = (result["score"] >= config.entry_score) & result["atr"].notna()
    result["exit_signal"] = result["score"] <= config.exit_score

    labels = {
        "trend_pass": "trend",
        "oil_pass": "oil",
        "dxy_pass": "dxy",
        "yield_pass": "yields",
        "breakout_pass": "breakout",
        "volume_pass": "volume",
    }
    result["reason"] = result.apply(
        lambda row: ", ".join(label for column, label in labels.items() if bool(row[column])),
        axis=1,
    )
    return result
