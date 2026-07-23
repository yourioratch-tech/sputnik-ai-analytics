from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from .backtest import BacktestResult
from .config import AnalyticsConfig


def calculate_metrics(result: BacktestResult, config: AnalyticsConfig) -> dict[str, Any]:
    equity = result.equity.set_index("date")["equity"].astype(float)
    returns = equity.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    elapsed_days = max(1, (equity.index[-1] - equity.index[0]).days)
    years = elapsed_days / 365.25
    total_return = result.final_equity / result.initial_capital - 1
    cagr = (result.final_equity / result.initial_capital) ** (1 / years) - 1
    annual_volatility = returns.std(ddof=1) * math.sqrt(config.annualization)
    daily_risk_free = (1 + config.risk_free_rate) ** (1 / config.annualization) - 1
    excess = returns - daily_risk_free
    sharpe = (
        excess.mean() / returns.std(ddof=1) * math.sqrt(config.annualization)
        if returns.std(ddof=1) > 0
        else 0.0
    )
    drawdown = equity / equity.cummax() - 1
    trades = result.trade_frame()
    wins = trades.loc[trades["pnl"] > 0, "pnl"] if not trades.empty else pd.Series(dtype=float)
    losses = trades.loc[trades["pnl"] < 0, "pnl"] if not trades.empty else pd.Series(dtype=float)
    gross_profit = float(wins.sum())
    gross_loss = abs(float(losses.sum()))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit else 0)
    first_price = float(result.equity.iloc[0]["close"])
    last_price = float(result.equity.iloc[-1]["close"])

    return {
        "start": equity.index[0].isoformat(),
        "end": equity.index[-1].isoformat(),
        "observations": len(equity),
        "initial_capital": result.initial_capital,
        "final_equity": result.final_equity,
        "total_return": total_return,
        "cagr": cagr,
        "annual_volatility": annual_volatility,
        "sharpe": sharpe,
        "max_drawdown": float(drawdown.min()),
        "trades": int(len(trades)),
        "win_rate": float((trades["pnl"] > 0).mean()) if not trades.empty else 0.0,
        "profit_factor": profit_factor,
        "average_trade_pnl": float(trades["pnl"].mean()) if not trades.empty else 0.0,
        "median_holding_bars": float(trades["holding_bars"].median()) if not trades.empty else 0.0,
        "exposure": float((result.equity["position"] > 0).mean()),
        "fees": float(trades["fees"].sum()) if not trades.empty else 0.0,
        "benchmark_return": last_price / first_price - 1,
    }
