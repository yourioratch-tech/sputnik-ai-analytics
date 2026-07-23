from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd


def _zscore(series: pd.Series) -> pd.Series:
    deviation = float(series.std(ddof=0))
    if not np.isfinite(deviation) or deviation == 0:
        return pd.Series(0.0, index=series.index)
    return (series - float(series.mean())) / deviation


def hunt_asx_candidates(
    frames: dict[str, pd.DataFrame],
    *,
    minimum_bars: int,
    top_n: int,
    max_age_hours: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Rank fresh, liquid trend/retest setups for the watch panel."""

    as_of = (now or datetime.now(UTC)).astimezone(UTC)
    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    for symbol, raw in sorted(frames.items()):
        frame = raw.copy()
        if len(frame) < minimum_bars:
            excluded.append({"symbol": symbol, "reason": f"only {len(frame)} completed bars"})
            continue
        frame["date"] = pd.to_datetime(frame["date"], utc=True)
        frame = frame.sort_values("date").drop_duplicates("date", keep="last")
        latest_time = frame.iloc[-1]["date"].to_pydatetime()
        age_hours = max(0.0, (as_of - latest_time).total_seconds() / 3600)
        if age_hours > max_age_hours:
            excluded.append({"symbol": symbol, "reason": f"stale by {age_hours:.1f} hours"})
            continue
        close = frame["close"].astype(float)
        high = frame["high"].astype(float)
        low = frame["low"].astype(float)
        volume = frame["volume"].astype(float)
        true_range = pd.concat(
            [(high - low), (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1
        ).max(axis=1)
        latest = float(close.iloc[-1])
        prior_high = float(high.shift().rolling(20).max().iloc[-1])
        sma20 = float(close.rolling(20).mean().iloc[-1])
        sma50 = float(close.rolling(50).mean().iloc[-1])
        atr_pct = float(true_range.rolling(14).mean().iloc[-1] / latest)
        dollar_volume = float((close * volume).tail(20).mean())
        if not all(np.isfinite(value) for value in (latest, prior_high, sma20, sma50, atr_pct, dollar_volume)):
            excluded.append({"symbol": symbol, "reason": "insufficient rolling-window data"})
            continue
        eligible.append(
            {
                "symbol": symbol,
                "event_time": latest_time.isoformat(),
                "age_hours": round(age_hours, 3),
                "close": latest,
                "return_5d": float(close.pct_change(5).iloc[-1]),
                "return_20d": float(close.pct_change(20).iloc[-1]),
                "distance_sma20": latest / sma20 - 1,
                "distance_sma50": latest / sma50 - 1,
                "breakout_20d": latest / prior_high - 1,
                "atr_pct": atr_pct,
                "avg_dollar_volume_20d": dollar_volume,
            }
        )
    if not eligible:
        return {
            "as_of": as_of.isoformat(),
            "status": "insufficient_data",
            "candidates": [],
            "excluded": excluded,
            "execution_boundary": "NO BROKER - NO ORDERS",
        }
    table = pd.DataFrame(eligible)
    table["score"] = (
        0.25 * _zscore(table["return_20d"])
        + 0.20 * _zscore(table["distance_sma50"])
        + 0.20 * _zscore(table["breakout_20d"])
        + 0.15 * _zscore(np.log10(table["avg_dollar_volume_20d"].clip(lower=1)))
        + 0.10 * _zscore(table["return_5d"])
        - 0.10 * _zscore(table["atr_pct"])
    )
    table = table.sort_values(["score", "avg_dollar_volume_20d"], ascending=False)
    candidates = table.head(top_n).round(6).to_dict(orient="records")
    for candidate in candidates:
        candidate["classification"] = "setup_to_watch"
        candidate["confirmation_required"] = "visible range, VWAP, and completed-bar retest"
    return {
        "as_of": as_of.isoformat(),
        "status": "ranked",
        "method": "trend, breakout proximity, traded value, and volatility",
        "candidate_count": len(candidates),
        "candidates": candidates,
        "excluded": excluded,
        "checks": [
            "Only stored completed bars are used; missing or stale symbols are excluded.",
            "Confirm the visible TradingView range, VWAP, retest, and current news before acting.",
            "The list identifies charts to inspect; it does not place or approve a trade.",
        ],
        "execution_boundary": "NO BROKER - NO ORDERS",
    }
