from pathlib import Path

import pandas as pd

from sputnik.jobs import run_worker_once
from sputnik.models import TradingViewEvent, WeekendGapJobRequest
from sputnik.settings import Settings
from sputnik.storage import MarketStore


def test_worker_completes_durable_weekend_gap_job(tmp_path: Path):
    runtime = Settings(
        database_path=tmp_path / "jobs.sqlite3",
        reports_dir=tmp_path / "reports",
        config_dir=Path("configs"),
    )
    store = MarketStore(runtime.database_path)
    dates = pd.bdate_range("2025-01-01", periods=80, tz="UTC")
    for date in dates:
        monday = date.weekday() == 0
        event = TradingViewEvent(
            schema_version=1,
            kind="bar",
            secret="not-persisted-secret-value",
            symbol="ASX:OOO",
            timeframe="1D",
            timestamp=date.to_pydatetime(),
            open=101 if monday else 100,
            high=102,
            low=99,
            close=100,
            volume=1000,
            confirmed=True,
        )
        store.record_event(event)

    queued = store.enqueue_weekend_gap(
        WeekendGapJobRequest(
            symbols=["ASX:OOO"], timeframe="1D", cost_bps=10, minimum_samples=2
        )
    )
    completed = run_worker_once(store, runtime)
    assert completed is not None
    assert completed["id"] == queued["id"]
    assert completed["status"] == "completed"
    assert completed["dataset_sha256"]
    assert completed["result"]["symbols"]["ASX:OOO"]["baseline"]["samples"] > 5
