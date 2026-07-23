from __future__ import annotations

from dataclasses import asdict, dataclass
from math import floor

import numpy as np
import pandas as pd

from .config import AppConfig
from .strategy import score_ooo


@dataclass
class Trade:
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    quantity: int
    entry_score: float
    entry_reason: str
    exit_reason: str
    holding_bars: int
    fees: float
    pnl: float
    return_pct: float


@dataclass
class BacktestResult:
    equity: pd.DataFrame
    trades: list[Trade]
    features: pd.DataFrame
    initial_capital: float
    final_equity: float

    def trade_frame(self) -> pd.DataFrame:
        if not self.trades:
            return pd.DataFrame(columns=list(Trade.__annotations__))
        return pd.DataFrame([asdict(trade) for trade in self.trades])


def _buy_fill(price: float, slippage_bps: float) -> float:
    return float(price) * (1 + slippage_bps / 10_000)


def _sell_fill(price: float, slippage_bps: float) -> float:
    return float(price) * (1 - slippage_bps / 10_000)


def run_backtest(frame: pd.DataFrame, config: AppConfig) -> BacktestResult:
    config.validate()
    data = score_ooo(frame, config.strategy).reset_index(drop=True)
    if len(data) < max(config.strategy.ema_slow, config.strategy.breakout_period) + 2:
        raise ValueError("not enough observations for configured lookbacks")

    cash = config.risk.initial_capital
    quantity = 0
    entry_price = 0.0
    entry_fee = 0.0
    entry_index = -1
    entry_score = 0.0
    entry_reason = ""
    initial_stop = np.nan
    trailing_stop = np.nan
    target = np.nan
    trades: list[Trade] = []
    curve: list[dict[str, float | str | int]] = []

    def close_position(index: int, raw_price: float, reason: str) -> None:
        nonlocal cash, quantity, entry_price, entry_fee, entry_index
        nonlocal entry_score, entry_reason, initial_stop, trailing_stop, target
        exit_price = _sell_fill(raw_price, config.costs.slippage_bps)
        exit_fee = config.costs.commission_flat
        cash += quantity * exit_price - exit_fee
        pnl = quantity * (exit_price - entry_price) - entry_fee - exit_fee
        basis = quantity * entry_price + entry_fee
        trades.append(
            Trade(
                entry_date=data.loc[entry_index, "date"].isoformat(),
                exit_date=data.loc[index, "date"].isoformat(),
                entry_price=entry_price,
                exit_price=exit_price,
                quantity=quantity,
                entry_score=entry_score,
                entry_reason=entry_reason,
                exit_reason=reason,
                holding_bars=index - entry_index,
                fees=entry_fee + exit_fee,
                pnl=pnl,
                return_pct=pnl / basis if basis else 0.0,
            )
        )
        quantity = 0
        entry_price = 0.0
        entry_fee = 0.0
        entry_index = -1
        entry_score = 0.0
        entry_reason = ""
        initial_stop = trailing_stop = target = np.nan

    for index, row in data.iterrows():
        exited_today = False
        previous = data.iloc[index - 1] if index > 0 else None

        if quantity and previous is not None:
            bars_held = index - entry_index
            if row["open"] <= max(initial_stop, trailing_stop):
                close_position(index, row["open"], "gap_stop")
                exited_today = True
            elif row["open"] >= target:
                close_position(index, row["open"], "gap_target")
                exited_today = True
            elif bool(previous["exit_signal"]):
                close_position(index, row["open"], "score_exit")
                exited_today = True
            elif bars_held >= config.risk.max_hold_bars:
                close_position(index, row["open"], "max_hold")
                exited_today = True

        if not quantity and not exited_today and previous is not None and bool(previous["entry_signal"]):
            fill = _buy_fill(row["open"], config.costs.slippage_bps)
            atr_value = float(previous["atr"])
            stop_distance = config.risk.stop_atr * atr_value
            risk_budget = cash * config.risk.risk_fraction
            risk_quantity = floor(risk_budget / stop_distance) if stop_distance > 0 else 0
            allocation_quantity = floor(
                max(0.0, cash * config.risk.max_allocation - config.costs.commission_flat) / fill
            )
            quantity = max(0, min(risk_quantity, allocation_quantity))
            if quantity > 0:
                entry_price = fill
                entry_fee = config.costs.commission_flat
                entry_index = index
                entry_score = float(previous["score"])
                entry_reason = str(previous["reason"])
                initial_stop = fill - config.risk.stop_atr * atr_value
                trailing_stop = initial_stop
                target = fill + config.risk.target_atr * atr_value
                cash -= quantity * fill + entry_fee

        if quantity:
            # Daily bars do not reveal whether stop or target occurred first.
            # The conservative convention checks the stop first.
            active_stop = max(initial_stop, trailing_stop)
            if row["low"] <= active_stop:
                close_position(index, active_stop, "atr_stop")
                exited_today = True
            elif row["high"] >= target:
                close_position(index, target, "atr_target")
                exited_today = True
            else:
                row_atr = float(row["atr"]) if pd.notna(row["atr"]) else 0.0
                candidate = float(row["close"]) - config.risk.trailing_atr * row_atr
                trailing_stop = max(trailing_stop, candidate)

        equity = cash + quantity * float(row["close"])
        curve.append(
            {
                "date": row["date"],
                "equity": equity,
                "cash": cash,
                "position": quantity,
                "close": float(row["close"]),
                "score": float(row["score"]),
            }
        )

    if quantity:
        last_index = len(data) - 1
        close_position(last_index, float(data.iloc[-1]["close"]), "end_of_data")
        curve[-1]["equity"] = cash
        curve[-1]["cash"] = cash
        curve[-1]["position"] = 0

    equity_frame = pd.DataFrame(curve)
    return BacktestResult(
        equity=equity_frame,
        trades=trades,
        features=data,
        initial_capital=config.risk.initial_capital,
        final_equity=float(equity_frame.iloc[-1]["equity"]),
    )
