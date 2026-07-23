from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class StrategyConfig:
    ema_fast: int = 20
    ema_slow: int = 50
    atr_period: int = 14
    breakout_period: int = 20
    volume_period: int = 20
    momentum_period: int = 5
    entry_score: float = 6.0
    exit_score: float = 3.0
    oil_momentum_min: float = 0.005
    dxy_momentum_max: float = 0.003
    yield_change_max: float = 0.15
    volume_ratio_min: float = 1.2

    def validate(self) -> None:
        if not 1 <= self.ema_fast < self.ema_slow:
            raise ValueError("ema periods must satisfy 1 <= ema_fast < ema_slow")
        for name in ("atr_period", "breakout_period", "volume_period", "momentum_period"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")
        if not 0 <= self.exit_score < self.entry_score <= 9:
            raise ValueError("scores must satisfy 0 <= exit_score < entry_score <= 9")
        if self.volume_ratio_min <= 0:
            raise ValueError("volume_ratio_min must be positive")


@dataclass(frozen=True)
class RiskConfig:
    initial_capital: float = 50_000.0
    risk_fraction: float = 0.01
    max_allocation: float = 0.90
    stop_atr: float = 2.0
    target_atr: float = 3.5
    trailing_atr: float = 2.0
    max_hold_bars: int = 3

    def validate(self) -> None:
        if self.initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        if not 0 < self.risk_fraction <= 0.1:
            raise ValueError("risk_fraction must be in (0, 0.1]")
        if not 0 < self.max_allocation <= 1:
            raise ValueError("max_allocation must be in (0, 1]")
        if min(self.stop_atr, self.target_atr, self.trailing_atr) <= 0:
            raise ValueError("ATR risk multiples must be positive")
        if self.max_hold_bars < 1:
            raise ValueError("max_hold_bars must be positive")


@dataclass(frozen=True)
class CostConfig:
    commission_flat: float = 29.95
    slippage_bps: float = 5.0

    def validate(self) -> None:
        if self.commission_flat < 0 or self.slippage_bps < 0:
            raise ValueError("cost assumptions cannot be negative")


@dataclass(frozen=True)
class AnalyticsConfig:
    annualization: int = 252
    risk_free_rate: float = 0.035

    def validate(self) -> None:
        if self.annualization < 1:
            raise ValueError("annualization must be positive")
        if self.risk_free_rate <= -1:
            raise ValueError("risk_free_rate must be greater than -100%")


@dataclass(frozen=True)
class AppConfig:
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    costs: CostConfig = field(default_factory=CostConfig)
    analytics: AnalyticsConfig = field(default_factory=AnalyticsConfig)

    def validate(self) -> None:
        self.strategy.validate()
        self.risk.validate()
        self.costs.validate()
        self.analytics.validate()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"config section '{name}' must be a mapping")
    return value


def load_config(path: str | Path | None = None) -> AppConfig:
    data: dict[str, Any] = {}
    if path is not None:
        with Path(path).open("r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle)
        if loaded is not None and not isinstance(loaded, dict):
            raise ValueError("configuration root must be a mapping")
        data = loaded or {}

    config = AppConfig(
        strategy=StrategyConfig(**_section(data, "strategy")),
        risk=RiskConfig(**_section(data, "risk")),
        costs=CostConfig(**_section(data, "costs")),
        analytics=AnalyticsConfig(**_section(data, "analytics")),
    )
    config.validate()
    return config
