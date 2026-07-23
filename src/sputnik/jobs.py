from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import pandas as pd

from . import __version__
from .asx_hunt import hunt_asx_candidates
from .backtest import run_backtest
from .config import load_config
from .data import merge_point_in_time
from .forecast import empirical_forecast
from .gap_study import run_weekend_gap_study
from .models import (
    AsxHuntJobRequest,
    BacktestJobRequest,
    ForecastJobRequest,
    PortfolioReviewJobRequest,
    WeekendGapJobRequest,
)
from .portfolio import review_transactions_against_bars
from .report import write_report
from .scheduler import schedule_due_jobs
from .settings import Settings
from .storage import MarketStore


def _fingerprint(frames: dict[str, pd.DataFrame], request: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(request, sort_keys=True).encode("utf-8"))
    for name in sorted(frames):
        digest.update(name.encode("utf-8"))
        digest.update(pd.util.hash_pandas_object(frames[name], index=True).values.tobytes())
    return digest.hexdigest()


def _safe_config_path(settings: Settings, name: str) -> Path:
    parent = settings.config_dir.resolve()
    candidate = (parent / f"{name}.yml").resolve()
    if candidate.parent != parent or not candidate.is_file():
        raise ValueError(f"unknown strategy config: {name}")
    return candidate


def _run_backtest_job(
    job_id: str, request_data: dict[str, Any], store: MarketStore, settings: Settings
) -> tuple[dict[str, Any], str]:
    request = BacktestJobRequest.model_validate(request_data)
    frames = {request.symbol: store.load_bars(request.symbol, request.timeframe)}
    if frames[request.symbol].empty:
        raise ValueError("no completed bars stored for target symbol/timeframe")
    macro: pd.DataFrame | None = None
    for field, symbol in request.macro_symbols.items():
        factor = store.load_bars(symbol, request.timeframe)
        frames[symbol] = factor
        if factor.empty:
            raise ValueError(f"no completed bars stored for macro symbol {symbol}")
        series = factor.loc[:, ["date", "close"]].rename(columns={"close": field})
        macro = series if macro is None else pd.merge_asof(
            macro.sort_values("date"), series.sort_values("date"), on="date", direction="backward"
        )
    dataset = merge_point_in_time(frames[request.symbol], macro)
    config = load_config(_safe_config_path(settings, request.config_name))
    result = run_backtest(dataset, config)
    output = settings.reports_dir / job_id
    report = write_report(result, config, output)
    fingerprint = _fingerprint(frames, request_data)
    report["research_record"] = {
        "dataset_sha256": fingerprint,
        "engine_version": __version__,
        "symbol": request.symbol,
        "timeframe": request.timeframe,
        "signal_timing": "completed bar; next-bar open execution",
        "provenance": "historical TradingView webhook bars",
        "limitations": [
            "Single-sample simulation; no walk-forward claim is implied.",
            "Results require independent source-completeness and corporate-action checks.",
            "Backtests never authorize execution.",
        ],
    }
    (output / "research_record.json").write_text(
        json.dumps(report["research_record"], indent=2, sort_keys=True), encoding="utf-8"
    )
    return report, fingerprint


def _run_gap_job(
    request_data: dict[str, Any], store: MarketStore
) -> tuple[dict[str, Any], str]:
    request = WeekendGapJobRequest.model_validate(request_data)
    targets = {
        symbol: store.load_bars(symbol, request.timeframe) for symbol in request.symbols
    }
    factors = {
        name: store.load_bars(symbol, request.timeframe)
        for name, symbol in request.factor_symbols.items()
    }
    frames = {**targets, **{f"factor:{name}": frame for name, frame in factors.items()}}
    fingerprint = _fingerprint(frames, request_data)
    report = run_weekend_gap_study(
        targets, factors, request.rules, request.cost_bps, request.minimum_samples
    )
    report["research_record"] = {
        "dataset_sha256": fingerprint,
        "engine_version": __version__,
        "timeframe": request.timeframe,
        "provenance": "historical TradingView webhook bars",
    }
    return report, fingerprint


def _run_forecast_job(
    request_data: dict[str, Any], store: MarketStore
) -> tuple[dict[str, Any], str]:
    request = ForecastJobRequest.model_validate(request_data)
    prices = store.load_bars(request.symbol, request.timeframe)
    fingerprint = _fingerprint({request.symbol: prices}, request_data)
    report = empirical_forecast(
        prices,
        request.horizon_bars,
        request.lookback_bars,
        request.minimum_samples,
    )
    report["symbol"] = request.symbol
    report["timeframe"] = request.timeframe
    report["research_record"] = {
        "dataset_sha256": fingerprint,
        "engine_version": __version__,
        "provenance": "historical TradingView webhook bars",
    }
    return report, fingerprint


def _run_portfolio_review_job(
    request_data: dict[str, Any], store: MarketStore
) -> tuple[dict[str, Any], str]:
    request = PortfolioReviewJobRequest.model_validate(request_data)
    transactions = store.portfolio_transactions()
    if transactions.empty:
        raise ValueError("no private portfolio transactions have been imported")
    codes = sorted(transactions["code"].unique())
    bars = {
        code: store.load_bars(f"ASX:{code}", request.timeframe) for code in codes
    }
    fingerprint_frames = {"private_transactions": transactions}
    fingerprint_frames.update({f"ASX:{code}": frame for code, frame in bars.items()})
    fingerprint = _fingerprint(fingerprint_frames, request_data)
    report = review_transactions_against_bars(transactions, bars, request.horizons)
    report["timeframe"] = request.timeframe
    report["research_record"] = {
        "dataset_sha256": fingerprint,
        "engine_version": __version__,
        "transaction_provenance": "private imported broker ledger",
        "market_provenance": "historical TradingView webhook bars",
    }
    return report, fingerprint


def _run_asx_hunt_job(
    request_data: dict[str, Any], store: MarketStore
) -> tuple[dict[str, Any], str]:
    request = AsxHuntJobRequest.model_validate(request_data)
    frames = {
        symbol: store.load_bars(symbol, request.timeframe) for symbol in request.symbols
    }
    fingerprint = _fingerprint(frames, request_data)
    report = hunt_asx_candidates(
        frames,
        minimum_bars=request.minimum_bars,
        top_n=request.top_n,
        max_age_hours=request.max_age_hours,
    )
    report.update(
        {
            "timeframe": request.timeframe,
            "dataset_sha256": fingerprint,
            "engine_version": __version__,
            "provenance": "stored completed TradingView webhook bars",
        }
    )
    return report, fingerprint


def run_worker_once(store: MarketStore, settings: Settings) -> dict[str, Any] | None:
    job = store.claim_job()
    if not job:
        return None
    try:
        if job["kind"] == "backtest":
            result, fingerprint = _run_backtest_job(
                job["id"], job["request"], store, settings
            )
        elif job["kind"] == "weekend_gap":
            result, fingerprint = _run_gap_job(job["request"], store)
        elif job["kind"] == "forecast":
            result, fingerprint = _run_forecast_job(job["request"], store)
        elif job["kind"] == "portfolio_review":
            result, fingerprint = _run_portfolio_review_job(job["request"], store)
        elif job["kind"] == "asx_hunt":
            result, fingerprint = _run_asx_hunt_job(job["request"], store)
        else:
            raise ValueError(f"unsupported job kind: {job['kind']}")
        store.complete_job(job["id"], result, fingerprint, __version__)
    except Exception as error:
        store.fail_job(job["id"], f"{type(error).__name__}: {error}")
    return store.get_job(job["id"])


def run_worker(settings: Settings, poll_seconds: float = 5.0, once: bool = False) -> None:
    store = MarketStore(settings.database_path)
    while True:
        if settings.scheduler_enabled:
            schedule_due_jobs(store)
        result = run_worker_once(store, settings)
        if result:
            print(json.dumps({"id": result["id"], "status": result["status"]}))
        if once:
            return
        if not result:
            time.sleep(poll_seconds)
