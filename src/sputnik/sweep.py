from __future__ import annotations

from dataclasses import replace
from itertools import product

import pandas as pd

from .backtest import run_backtest
from .config import AppConfig
from .metrics import calculate_metrics


def parameter_sweep(frame: pd.DataFrame, base: AppConfig) -> pd.DataFrame:
    rows: list[dict[str, float | int]] = []
    for entry_score, stop_atr, target_atr in product((5.0, 6.0, 7.0), (1.5, 2.0, 2.5), (2.5, 3.5, 4.5)):
        if entry_score <= base.strategy.exit_score:
            continue
        config = replace(
            base,
            strategy=replace(base.strategy, entry_score=entry_score),
            risk=replace(base.risk, stop_atr=stop_atr, target_atr=target_atr),
        )
        result = run_backtest(frame, config)
        metrics = calculate_metrics(result, config.analytics)
        rows.append(
            {
                "entry_score": entry_score,
                "stop_atr": stop_atr,
                "target_atr": target_atr,
                "total_return": metrics["total_return"],
                "max_drawdown": metrics["max_drawdown"],
                "sharpe": metrics["sharpe"],
                "trades": metrics["trades"],
                "win_rate": metrics["win_rate"],
                "profit_factor": metrics["profit_factor"],
            }
        )
    result = pd.DataFrame(rows)
    result["research_score"] = (
        result["sharpe"].clip(-3, 3)
        + result["total_return"].clip(-1, 2)
        - result["max_drawdown"].abs()
    )
    return result.sort_values(["research_score", "trades"], ascending=[False, False]).reset_index(drop=True)
