from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def empirical_forecast(
    prices: pd.DataFrame,
    horizon_bars: int,
    lookback_bars: int,
    minimum_samples: int,
) -> dict[str, Any]:
    if prices.empty:
        raise ValueError("no completed bars available")
    data = prices.copy()
    data["date"] = pd.to_datetime(data["date"], utc=True)
    data = data.sort_values("date").drop_duplicates("date").tail(lookback_bars)
    forward = data["close"].shift(-horizon_bars) / data["close"] - 1
    samples = forward.replace([np.inf, -np.inf], np.nan).dropna()
    count = int(len(samples))
    latest_close = float(data.iloc[-1]["close"])
    quantiles = {
        label: float(samples.quantile(level)) if count else None
        for label, level in (("p05", 0.05), ("p25", 0.25), ("p50", 0.50), ("p75", 0.75), ("p95", 0.95))
    }
    projected = {
        label: latest_close * (1 + value) if value is not None else None
        for label, value in quantiles.items()
    }
    positive = int((samples > 0).sum())
    return {
        "method": "empirical_historical_distribution",
        "horizon_bars": horizon_bars,
        "samples": count,
        "research_gate": "pass" if count >= minimum_samples else "insufficient_sample",
        "as_of": data.iloc[-1]["date"].isoformat(),
        "latest_close": latest_close,
        "historical_positive_rate": positive / count if count else None,
        "mean_return": float(samples.mean()) if count else None,
        "return_quantiles": quantiles,
        "projected_price_quantiles": projected,
        "probability_type": "historical_frequency_not_calibrated_live_probability",
        "limitations": [
            "This baseline assumes the stored historical return distribution is relevant.",
            "It is not conditioned on the current regime and is not walk-forward calibrated.",
            "Projected prices are distribution anchors, not targets or trade instructions.",
            "Webhook history must be audited for missing bars and corporate actions.",
        ],
        "execution_boundary": "RESEARCH ONLY - NO BROKER - NO ORDERS",
    }
