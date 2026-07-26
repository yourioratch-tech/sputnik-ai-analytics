from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator

SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9:._!/-]{0,39}$")
TIMEFRAME_PATTERN = re.compile(r"^[0-9]{1,4}[SMHDW]$|^[1-9][0-9]{0,3}$", re.IGNORECASE)


def parse_timestamp(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(seconds, tz=UTC)
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(UTC)


class TradingViewEvent(BaseModel):
    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    schema_version: Literal[1] = 1
    kind: Literal["bar", "price", "alert"]
    secret: str = Field(min_length=16, max_length=512)
    symbol: str = Field(min_length=1, max_length=40)
    timeframe: str = Field(min_length=1, max_length=12)
    timestamp: datetime
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None
    price: float | None = None
    event: str | None = Field(default=None, max_length=80)
    message: str | None = Field(default=None, max_length=2_000)
    confirmed: bool = True

    @field_validator("symbol", mode="before")
    @classmethod
    def validate_symbol(cls, value: Any) -> str:
        symbol = str(value).strip().upper()
        if not SYMBOL_PATTERN.fullmatch(symbol):
            raise ValueError("invalid symbol")
        return symbol

    @field_validator("timeframe", mode="before")
    @classmethod
    def validate_timeframe(cls, value: Any) -> str:
        timeframe = str(value).strip().upper()
        if not TIMEFRAME_PATTERN.fullmatch(timeframe):
            raise ValueError("invalid timeframe")
        return timeframe

    @field_validator("timestamp", mode="before")
    @classmethod
    def validate_timestamp(cls, value: Any) -> datetime:
        return parse_timestamp(value)

    @model_validator(mode="after")
    def validate_market_values(self) -> TradingViewEvent:
        if self.kind == "bar":
            if not self.confirmed:
                raise ValueError("only completed bars are accepted")
            values = (self.open, self.high, self.low, self.close, self.volume)
            if any(value is None for value in values):
                raise ValueError("bar events require open, high, low, close, and volume")
            assert self.open is not None and self.high is not None
            assert self.low is not None and self.close is not None and self.volume is not None
            if min(self.open, self.high, self.low, self.close) <= 0 or self.volume < 0:
                raise ValueError("OHLC must be positive and volume non-negative")
            if self.high < max(self.open, self.low, self.close):
                raise ValueError("high is inconsistent with OHLC")
            if self.low > min(self.open, self.high, self.close):
                raise ValueError("low is inconsistent with OHLC")
        point = self.price if self.price is not None else self.close
        if self.kind == "price" and point is None:
            raise ValueError("price events require price")
        if point is not None and point <= 0:
            raise ValueError("price must be positive")
        return self

    def safe_payload(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json", exclude={"secret"}, exclude_none=True)
        return payload


class BacktestJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    timeframe: str
    config_name: str = "ooo_daily"
    macro_symbols: dict[Literal["oil", "dxy", "us10y", "us30y", "gold", "silver", "copper"], str] = Field(
        default_factory=dict
    )

    @field_validator("symbol")
    @classmethod
    def normalise_symbol(cls, value: str) -> str:
        return TradingViewEvent.validate_symbol(value)

    @field_validator("timeframe")
    @classmethod
    def normalise_timeframe(cls, value: str) -> str:
        return TradingViewEvent.validate_timeframe(value)

    @field_validator("config_name")
    @classmethod
    def validate_config_name(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", value):
            raise ValueError("config_name must be a safe slug")
        return value

    @field_validator("macro_symbols")
    @classmethod
    def normalise_macro_symbols(cls, value: dict[str, str]) -> dict[str, str]:
        return {key: TradingViewEvent.validate_symbol(symbol) for key, symbol in value.items()}


class RegimeRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    operator: Literal["gt", "ge", "lt", "le", "eq"]
    value: float


class WeekendGapJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbols: list[str] = Field(min_length=1, max_length=500)
    timeframe: str = "1D"
    factor_symbols: dict[str, str] = Field(default_factory=dict)
    rules: list[RegimeRule] = Field(default_factory=list, max_length=20)
    cost_bps: float = Field(default=10.0, ge=0, le=500)
    minimum_samples: int = Field(default=20, ge=2, le=10_000)

    @field_validator("symbols")
    @classmethod
    def normalise_symbols(cls, values: list[str]) -> list[str]:
        clean = [TradingViewEvent.validate_symbol(value) for value in values]
        if len(set(clean)) != len(clean):
            raise ValueError("symbols must be unique")
        return clean

    @field_validator("timeframe")
    @classmethod
    def normalise_gap_timeframe(cls, value: str) -> str:
        return TradingViewEvent.validate_timeframe(value)

    @field_validator("factor_symbols")
    @classmethod
    def normalise_factor_symbols(cls, value: dict[str, str]) -> dict[str, str]:
        result: dict[str, str] = {}
        for name, symbol in value.items():
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", name):
                raise ValueError("factor names must be safe lowercase slugs")
            result[name] = TradingViewEvent.validate_symbol(symbol)
        return result

    @model_validator(mode="after")
    def validate_rule_fields(self) -> WeekendGapJobRequest:
        allowed = {f"{name}_return" for name in self.factor_symbols}
        unknown = sorted({rule.field for rule in self.rules} - allowed)
        if unknown:
            raise ValueError(f"rules reference unknown factor returns: {', '.join(unknown)}")
        return self


class NewsWebhookEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1] = 1
    secret: str = Field(min_length=16, max_length=512)
    source: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=500)
    url: AnyHttpUrl
    published_at: datetime
    symbols: list[str] = Field(default_factory=list, max_length=100)
    summary: str | None = Field(default=None, max_length=2_000)
    category: str | None = Field(default=None, max_length=80)

    @field_validator("url")
    @classmethod
    def require_https(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.scheme != "https":
            raise ValueError("news URL must use HTTPS")
        return value

    @field_validator("published_at", mode="before")
    @classmethod
    def validate_published_at(cls, value: Any) -> datetime:
        return parse_timestamp(value)

    @field_validator("symbols")
    @classmethod
    def normalise_news_symbols(cls, values: list[str]) -> list[str]:
        clean = [TradingViewEvent.validate_symbol(value) for value in values]
        if len(set(clean)) != len(clean):
            raise ValueError("symbols must be unique")
        return clean

    def safe_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"secret"}, exclude_none=True)


class ForecastJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    timeframe: str
    horizon_bars: int = Field(default=1, ge=1, le=20)
    lookback_bars: int = Field(default=1_000, ge=60, le=20_000)
    minimum_samples: int = Field(default=60, ge=20, le=10_000)
    method: Literal["empirical_distribution"] = "empirical_distribution"

    @field_validator("symbol")
    @classmethod
    def normalise_forecast_symbol(cls, value: str) -> str:
        return TradingViewEvent.validate_symbol(value)

    @field_validator("timeframe")
    @classmethod
    def normalise_forecast_timeframe(cls, value: str) -> str:
        return TradingViewEvent.validate_timeframe(value)


class GraniteAgentJobRequest(BaseModel):
    """Bounded reasoning work for a local LM Studio model; never an execution request."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    task: str = Field(min_length=3, max_length=4_000)
    context: str | None = Field(default=None, max_length=16_000)
    role: Literal[
        "main", "trade_alerts", "portfolio", "news",
        "research", "market", "webhook", "maintenance",
    ] = "main"
    output_format: Literal["markdown", "json"] = "markdown"
    model: str = Field(default="granite-4-micro", pattern=r"^[a-zA-Z0-9._/-]{1,120}$")


class PortfolioReviewJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeframe: str = "1D"
    horizons: list[int] = Field(default_factory=lambda: [1, 3, 5, 20], min_length=1, max_length=10)

    @field_validator("timeframe")
    @classmethod
    def normalise_review_timeframe(cls, value: str) -> str:
        return TradingViewEvent.validate_timeframe(value)

    @field_validator("horizons")
    @classmethod
    def validate_horizons(cls, values: list[int]) -> list[int]:
        if any(value < 1 or value > 252 for value in values):
            raise ValueError("horizons must be between 1 and 252 bars")
        if len(set(values)) != len(values):
            raise ValueError("horizons must be unique")
        return sorted(values)


class PriceAmendmentRequest(BaseModel):
    """Append-only correction; the original observation is never overwritten."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    target_kind: Literal["bar", "price"]
    symbol: str
    timeframe: str
    event_time: datetime
    price: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None
    reason: str = Field(min_length=10, max_length=500)
    actor: str = Field(default="chatgpt_work", pattern=r"^[a-zA-Z0-9_.:@/-]{1,80}$")
    source_url: AnyHttpUrl | None = None

    @field_validator("symbol")
    @classmethod
    def normalise_amendment_symbol(cls, value: str) -> str:
        return TradingViewEvent.validate_symbol(value)

    @field_validator("timeframe")
    @classmethod
    def normalise_amendment_timeframe(cls, value: str) -> str:
        return TradingViewEvent.validate_timeframe(value)

    @field_validator("event_time", mode="before")
    @classmethod
    def validate_event_time(cls, value: Any) -> datetime:
        return parse_timestamp(value)

    @field_validator("source_url")
    @classmethod
    def require_amendment_https(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:
        if value is not None and value.scheme != "https":
            raise ValueError("source URL must use HTTPS")
        return value

    @model_validator(mode="after")
    def validate_replacement(self) -> PriceAmendmentRequest:
        if self.target_kind == "price":
            if self.price is None or self.price <= 0:
                raise ValueError("price amendments require a positive price")
            if any(value is not None for value in (self.open, self.high, self.low, self.close, self.volume)):
                raise ValueError("price amendments cannot include OHLCV")
            return self
        values = (self.open, self.high, self.low, self.close, self.volume)
        if any(value is None for value in values):
            raise ValueError("bar amendments require complete OHLCV")
        assert self.open is not None and self.high is not None
        assert self.low is not None and self.close is not None and self.volume is not None
        if min(self.open, self.high, self.low, self.close) <= 0 or self.volume < 0:
            raise ValueError("OHLC must be positive and volume non-negative")
        if self.high < max(self.open, self.low, self.close):
            raise ValueError("high is inconsistent with OHLC")
        if self.low > min(self.open, self.high, self.close):
            raise ValueError("low is inconsistent with OHLC")
        if self.price is not None:
            raise ValueError("bar amendments use close rather than price")
        return self

    def replacement(self) -> dict[str, float]:
        fields = ("price",) if self.target_kind == "price" else ("open", "high", "low", "close", "volume")
        return {field: float(getattr(self, field)) for field in fields}


class StrategySchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    timeframe: str = "1D"
    config_name: str = "ooo_daily"
    macro_symbols: dict[str, str] = Field(default_factory=dict)

    @field_validator("symbol")
    @classmethod
    def normalise_schedule_symbol(cls, value: str) -> str:
        return TradingViewEvent.validate_symbol(value)

    @field_validator("timeframe")
    @classmethod
    def normalise_schedule_timeframe(cls, value: str) -> str:
        return TradingViewEvent.validate_timeframe(value)

    @field_validator("config_name")
    @classmethod
    def validate_schedule_config(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", value):
            raise ValueError("config_name must be a safe slug")
        return value

    @field_validator("macro_symbols")
    @classmethod
    def normalise_schedule_macro(cls, value: dict[str, str]) -> dict[str, str]:
        return {name: TradingViewEvent.validate_symbol(symbol) for name, symbol in value.items()}


class ControlConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    timezone: str = "Australia/Sydney"
    session_start: str = Field(default="10:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    session_end: str = Field(default="16:15", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    scan_interval_minutes: int = Field(default=30, ge=5, le=240)
    max_daily_bar_age_hours: int = Field(default=72, ge=1, le=168)
    minimum_bars: int = Field(default=60, ge=20, le=5_000)
    top_n: int = Field(default=10, ge=1, le=100)
    asx_universe: list[str] = Field(default_factory=list, max_length=500)
    strategy_backtests: list[StrategySchedule] = Field(default_factory=list, max_length=50)
    reason: str = Field(default="initial configuration", min_length=3, max_length=500)
    actor: str = Field(default="chatgpt_work", pattern=r"^[a-zA-Z0-9_.:@/-]{1,80}$")

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("unknown IANA timezone") from error
        return value

    @field_validator("asx_universe")
    @classmethod
    def validate_asx_universe(cls, values: list[str]) -> list[str]:
        clean = [TradingViewEvent.validate_symbol(value) for value in values]
        if any(not value.startswith("ASX:") for value in clean):
            raise ValueError("ASX universe symbols must start with ASX:")
        if len(clean) != len(set(clean)):
            raise ValueError("ASX universe symbols must be unique")
        return clean


class AsxHuntJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbols: list[str] = Field(min_length=1, max_length=500)
    timeframe: str = "1D"
    minimum_bars: int = Field(default=60, ge=20, le=5_000)
    top_n: int = Field(default=10, ge=1, le=100)
    max_age_hours: int = Field(default=72, ge=1, le=168)

    @field_validator("symbols")
    @classmethod
    def normalise_hunt_symbols(cls, values: list[str]) -> list[str]:
        clean = [TradingViewEvent.validate_symbol(value) for value in values]
        if any(not value.startswith("ASX:") for value in clean):
            raise ValueError("hunter accepts ASX symbols only")
        if len(clean) != len(set(clean)):
            raise ValueError("symbols must be unique")
        return clean

    @field_validator("timeframe")
    @classmethod
    def normalise_hunt_timeframe(cls, value: str) -> str:
        return TradingViewEvent.validate_timeframe(value)
