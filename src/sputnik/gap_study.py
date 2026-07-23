from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from .models import RegimeRule


def _wilson_interval(successes: int, samples: int, z: float = 1.96) -> tuple[float, float]:
    if samples == 0:
        return (math.nan, math.nan)
    probability = successes / samples
    denominator = 1 + z**2 / samples
    centre = (probability + z**2 / (2 * samples)) / denominator
    distance = z * math.sqrt(
        probability * (1 - probability) / samples + z**2 / (4 * samples**2)
    ) / denominator
    return (max(0.0, centre - distance), min(1.0, centre + distance))


def build_factor_frame(factors: dict[str, pd.DataFrame]) -> pd.DataFrame | None:
    combined: pd.DataFrame | None = None
    for name, frame in factors.items():
        if frame.empty:
            continue
        values = frame.loc[:, ["date", "close"]].copy()
        values["date"] = pd.to_datetime(values["date"], utc=True)
        values[f"{name}_return"] = values["close"].pct_change()
        values = values.loc[:, ["date", f"{name}_return"]].dropna()
        combined = values if combined is None else pd.merge_asof(
            combined.sort_values("date"),
            values.sort_values("date"),
            on="date",
            direction="backward",
        )
    return combined


def weekend_observations(
    prices: pd.DataFrame, factor_frame: pd.DataFrame | None = None
) -> pd.DataFrame:
    if prices.empty:
        return pd.DataFrame()
    data = prices.copy()
    data["date"] = pd.to_datetime(data["date"], utc=True)
    data = data.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    data["next_date"] = data["date"].shift(-1)
    data["next_open"] = data["open"].shift(-1)
    data["calendar_days"] = (data["next_date"] - data["date"]).dt.total_seconds() / 86_400
    data["gross_gap_return"] = data["next_open"] / data["close"] - 1
    # Friday is the normal case; a >=2-day closure also captures exchange-holiday weekends.
    observations = data.loc[
        (data["date"].dt.weekday == 4) & data["calendar_days"].between(2, 5),
        ["date", "next_date", "close", "next_open", "calendar_days", "gross_gap_return"],
    ].copy()
    if factor_frame is not None and not factor_frame.empty:
        observations = pd.merge_asof(
            observations.sort_values("date"),
            factor_frame.sort_values("date"),
            on="date",
            direction="backward",
        )
    return observations


def apply_rules(frame: pd.DataFrame, rules: list[RegimeRule]) -> pd.DataFrame:
    result = frame
    operations = {
        "gt": lambda series, value: series > value,
        "ge": lambda series, value: series >= value,
        "lt": lambda series, value: series < value,
        "le": lambda series, value: series <= value,
        "eq": lambda series, value: series == value,
    }
    for rule in rules:
        if rule.field not in result:
            return result.iloc[0:0]
        result = result.loc[operations[rule.operator](result[rule.field], rule.value).fillna(False)]
    return result


def summarise_gaps(
    frame: pd.DataFrame, cost_bps: float, minimum_samples: int
) -> dict[str, Any]:
    returns = frame["gross_gap_return"].replace([np.inf, -np.inf], np.nan).dropna()
    samples = int(len(returns))
    successes = int((returns > 0).sum())
    low, high = _wilson_interval(successes, samples)
    net = returns - cost_bps / 10_000
    positive_rate = successes / samples if samples else None
    odds = successes / (samples - successes) if samples > successes else None
    return {
        "samples": samples,
        "positive_gaps": successes,
        "historical_positive_rate": positive_rate,
        "positive_rate_wilson_95": [low, high] if samples else None,
        "historical_odds_ratio": odds,
        "mean_gross_gap_return": float(returns.mean()) if samples else None,
        "median_gross_gap_return": float(returns.median()) if samples else None,
        "mean_net_gap_return": float(net.mean()) if samples else None,
        "p05_gross_gap_return": float(returns.quantile(0.05)) if samples else None,
        "p95_gross_gap_return": float(returns.quantile(0.95)) if samples else None,
        "best_gross_gap_return": float(returns.max()) if samples else None,
        "worst_gross_gap_return": float(returns.min()) if samples else None,
        "cost_bps": cost_bps,
        "research_gate": "pass" if samples >= minimum_samples else "insufficient_sample",
        "probability_type": "historical_frequency_not_forecast",
    }


def run_weekend_gap_study(
    targets: dict[str, pd.DataFrame],
    factors: dict[str, pd.DataFrame],
    rules: list[RegimeRule],
    cost_bps: float,
    minimum_samples: int,
) -> dict[str, Any]:
    factor_frame = build_factor_frame(factors)
    results: dict[str, Any] = {}
    for symbol, prices in targets.items():
        baseline = weekend_observations(prices, factor_frame)
        conditioned = apply_rules(baseline, rules)
        results[symbol] = {
            "baseline": summarise_gaps(baseline, cost_bps, minimum_samples),
            "conditioned": summarise_gaps(conditioned, cost_bps, minimum_samples),
            "start": baseline["date"].min().isoformat() if not baseline.empty else None,
            "end": baseline["date"].max().isoformat() if not baseline.empty else None,
        }
    return {
        "symbols": results,
        "rules": [rule.model_dump(mode="json") for rule in rules],
        "limitations": [
            "Friday close to next-session open is a historical research convention, not an executable quote.",
            "TradingView webhook history is complete only if every completed bar was delivered.",
            "Survivorship, delistings, corporate actions, spread, auction liquidity, and taxes require separate review.",
            "A conditional historical rate is not a calibrated probability forecast.",
        ],
        "execution_boundary": "RESEARCH ONLY - NO BROKER - NO ORDERS",
    }
