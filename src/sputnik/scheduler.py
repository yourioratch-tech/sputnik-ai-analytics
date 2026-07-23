from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from .models import AsxHuntJobRequest, BacktestJobRequest, ControlConfiguration
from .storage import MarketStore


def schedule_due_jobs(store: MarketStore, now: datetime | None = None) -> list[dict[str, object]]:
    revision = store.get_control_configuration()
    if not revision:
        return []
    config = ControlConfiguration.model_validate(revision["configuration"])
    if not config.enabled or not config.asx_universe:
        return []
    current = (now or datetime.now(UTC)).astimezone(ZoneInfo(config.timezone))
    if current.weekday() >= 5:
        return []
    start = time.fromisoformat(config.session_start)
    end = time.fromisoformat(config.session_end)
    results: list[dict[str, object]] = []
    if start <= current.time().replace(tzinfo=None) <= end:
        bucket = (current.hour * 60 + current.minute) // config.scan_interval_minutes
        task_key = f"asx-hunt:{current.date().isoformat()}:{bucket}"
        if store.claim_schedule(task_key):
            job = store.enqueue_asx_hunt(
                AsxHuntJobRequest(
                    symbols=config.asx_universe,
                    minimum_bars=config.minimum_bars,
                    top_n=config.top_n,
                    max_age_hours=config.max_daily_bar_age_hours,
                )
            )
            store.link_schedule_job(task_key, job["id"])
            results.append(job)
    if current.time().replace(tzinfo=None) >= time(17, 0):
        for strategy in config.strategy_backtests:
            task_key = f"backtest:{current.date().isoformat()}:{strategy.symbol}:{strategy.config_name}"
            if not store.claim_schedule(task_key):
                continue
            job = store.enqueue_backtest(
                BacktestJobRequest(
                    symbol=strategy.symbol,
                    timeframe=strategy.timeframe,
                    config_name=strategy.config_name,
                    macro_symbols=strategy.macro_symbols,
                )
            )
            store.link_schedule_job(task_key, job["id"])
            results.append(job)
    return results
