from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from sputnik.asx_hunt import hunt_asx_candidates
from sputnik.models import ControlConfiguration
from sputnik.scheduler import schedule_due_jobs
from sputnik.storage import MarketStore


def frame(last: str, slope: float, volume: float) -> pd.DataFrame:
    dates = pd.date_range(end=last, periods=80, freq="D", tz="UTC")
    close = 10 + slope * np.arange(80)
    return pd.DataFrame(
        {
            "date": dates,
            "open": close - 0.02,
            "high": close + 0.05,
            "low": close - 0.05,
            "close": close,
            "volume": volume,
        }
    )


def test_hunter_ranks_fresh_liquid_completed_bar_setups():
    now = datetime(2026, 7, 23, 7, tzinfo=UTC)
    result = hunt_asx_candidates(
        {
            "ASX:BHP": frame("2026-07-23T06:00:00Z", 0.08, 2_000_000),
            "ASX:LTR": frame("2026-07-23T06:00:00Z", -0.01, 100_000),
            "ASX:OLD": frame("2026-07-01T06:00:00Z", 0.20, 5_000_000),
        },
        minimum_bars=60,
        top_n=2,
        max_age_hours=72,
        now=now,
    )
    assert result["status"] == "ranked"
    assert result["candidates"][0]["symbol"] == "ASX:BHP"
    assert result["candidates"][0]["classification"] == "setup_to_watch"
    assert any(item["symbol"] == "ASX:OLD" for item in result["excluded"])


def test_scheduler_claims_each_asx_session_bucket_once(tmp_path: Path):
    store = MarketStore(tmp_path / "scheduler.sqlite3")
    store.set_control_configuration(
        ControlConfiguration(
            asx_universe=["ASX:EVN", "ASX:BHP"],
            scan_interval_minutes=30,
            reason="test ASX session panel",
            actor="test",
        )
    )
    now = datetime(2026, 7, 23, 1, 15, tzinfo=UTC)  # 11:15 Sydney
    first = schedule_due_jobs(store, now)
    second = schedule_due_jobs(store, now)
    assert len(first) == 1
    assert first[0]["kind"] == "asx_hunt"
    assert second == []
