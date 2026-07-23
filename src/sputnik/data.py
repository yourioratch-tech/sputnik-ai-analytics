from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

PRICE_COLUMNS = ("date", "open", "high", "low", "close", "volume")
MACRO_COLUMNS = ("oil", "dxy", "us10y", "us30y", "gold", "silver", "copper")


def _normalise_columns(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result.columns = [str(column).strip().lower() for column in result.columns]
    return result


def validate_prices(frame: pd.DataFrame) -> pd.DataFrame:
    frame = _normalise_columns(frame)
    missing = sorted(set(PRICE_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f"price data missing columns: {', '.join(missing)}")

    result = frame.loc[:, PRICE_COLUMNS].copy()
    result["date"] = pd.to_datetime(result["date"], errors="raise", utc=True)
    for column in PRICE_COLUMNS[1:]:
        result[column] = pd.to_numeric(result[column], errors="raise")

    if result["date"].duplicated().any():
        duplicates = result.loc[result["date"].duplicated(), "date"].dt.date.astype(str).tolist()
        raise ValueError(f"duplicate price dates: {duplicates[:3]}")
    if (result[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("OHLC values must be positive")
    if (result["volume"] < 0).any():
        raise ValueError("volume cannot be negative")
    if (result["high"] < result[["open", "close", "low"]].max(axis=1)).any():
        raise ValueError("high must be at least open, close, and low")
    if (result["low"] > result[["open", "close", "high"]].min(axis=1)).any():
        raise ValueError("low must be at most open, close, and high")

    return result.sort_values("date").reset_index(drop=True)


def validate_macro(frame: pd.DataFrame) -> pd.DataFrame:
    frame = _normalise_columns(frame)
    if "date" not in frame:
        raise ValueError("macro data missing column: date")
    available = [column for column in MACRO_COLUMNS if column in frame]
    if not available:
        raise ValueError(f"macro data needs at least one of: {', '.join(MACRO_COLUMNS)}")

    result = frame.loc[:, ["date", *available]].copy()
    result["date"] = pd.to_datetime(result["date"], errors="raise", utc=True)
    for column in available:
        result[column] = pd.to_numeric(result[column], errors="coerce")
        if (result[column].dropna() <= 0).any():
            raise ValueError(f"macro column '{column}' must contain positive values")
    if result["date"].duplicated().any():
        raise ValueError("macro data contains duplicate dates")
    return result.sort_values("date").reset_index(drop=True)


def read_prices(path: str | Path) -> pd.DataFrame:
    return validate_prices(pd.read_csv(path))


def read_macro(path: str | Path) -> pd.DataFrame:
    return validate_macro(pd.read_csv(path))


def merge_point_in_time(prices: pd.DataFrame, macro: pd.DataFrame | None) -> pd.DataFrame:
    prices = validate_prices(prices)
    if macro is None:
        return prices
    macro = validate_macro(macro)
    return pd.merge_asof(
        prices.sort_values("date"),
        macro.sort_values("date"),
        on="date",
        direction="backward",
        allow_exact_matches=True,
    )


def make_demo_market(days: int = 900, seed: int = 42) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Create deterministic, correlated data for software testing only."""
    if days < 100:
        raise ValueError("demo requires at least 100 days")
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2022-01-03", periods=days, tz="UTC")

    regime = np.sin(np.arange(days) / 60.0) * 0.0015
    oil_returns = 0.00015 + regime + rng.normal(0, 0.018, days)
    dxy_returns = 0.00005 - regime * 0.35 + rng.normal(0, 0.0035, days)
    yield_changes = rng.normal(0, 0.035, days) - regime * 4

    oil = 75 * np.exp(np.cumsum(oil_returns))
    dxy = 102 * np.exp(np.cumsum(dxy_returns))
    us10y = np.clip(3.5 + np.cumsum(yield_changes) / 20, 0.5, 8.0)
    us30y = np.clip(us10y + 0.25 + rng.normal(0, 0.04, days), 0.5, 9.0)
    gold = 1_850 * np.exp(np.cumsum(0.00015 - dxy_returns * 0.25 + rng.normal(0, 0.008, days)))
    silver = 23 * np.exp(np.cumsum(0.0002 - dxy_returns * 0.15 + rng.normal(0, 0.014, days)))
    copper = 3.8 * np.exp(np.cumsum(0.0001 + regime * 0.4 + rng.normal(0, 0.010, days)))

    ooo_returns = (
        0.62 * oil_returns
        - 0.08 * dxy_returns
        - 0.001 * yield_changes
        - 0.00015
        + rng.normal(0, 0.009, days)
    )
    close = 5.0 * np.exp(np.cumsum(ooo_returns))
    overnight = rng.normal(0, 0.006, days)
    open_ = np.r_[close[0] * (1 + overnight[0]), close[:-1] * (1 + overnight[1:])]
    spread = np.abs(rng.normal(0.010, 0.005, days))
    high = np.maximum(open_, close) * (1 + spread)
    low = np.minimum(open_, close) * np.maximum(0.01, 1 - spread)
    volume = rng.lognormal(mean=13.0, sigma=0.45, size=days).astype(int)

    prices = pd.DataFrame(
        {
            "date": dates,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )
    macro = pd.DataFrame(
        {
            "date": dates,
            "oil": oil,
            "dxy": dxy,
            "us10y": us10y,
            "us30y": us30y,
            "gold": gold,
            "silver": silver,
            "copper": copper,
        }
    )
    return validate_prices(prices), validate_macro(macro)
