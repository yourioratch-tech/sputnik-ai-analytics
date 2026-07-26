from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pandas as pd

from . import __version__
from .agent_knowledge import build_agent_context
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
    GraniteAgentJobRequest,
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


def _run_granite_agent_job(
    request_data: dict[str, Any], store: MarketStore, settings: Settings
) -> tuple[dict[str, Any], str]:
    request = GraniteAgentJobRequest.model_validate(request_data)
    boundary = (
        "You are a bounded Sputnik research agent. You may analyze, classify, draft, and "
        "recommend next steps. You cannot place or modify orders, access brokers or secrets, "
        "run shell commands, or claim unseen market data is current. Clearly label observed "
        "facts, model-derived conclusions, assumptions, and missing evidence. Never rename, "
        "strengthen, or reinterpret a configured rule. Quote exact rule names when possible."
    )
    role = {
        "main": (
            "Coordinate read-only data collection. Report source, timestamp, freshness, "
            "gaps, contradictions, and the next bounded collection task."
        ),
        "trade_alerts": (
            "Backtest supplied strategies and find candidate setups only from supplied or "
            "stored completed-bar evidence. Every alert must state what changed, why it "
            "matters, price or level, confirmation, and invalidation. Alerts are attention-only; "
            "never place or recommend automatic orders."
        ),
        "portfolio": (
            "Monitor only privacy-filtered positions and trades supplied by the operator. "
            "Compare exposure, thesis, catalysts, risk, and invalidation; never access a broker, "
            "credentials, or place or modify orders."
        ),
        "research": "Synthesize evidence and identify contradictions and unknowns.",
        "market": "Connect markets, but do not invent current prices or trade signals.",
        "webhook": "Triage webhook evidence, freshness, schemas, and delivery failures.",
        "news": "Analyze only supplied stored headlines; distinguish publication facts from inference.",
        "maintenance": "Draft safe, auditable maintenance steps without executing them.",
    }[request.role]
    user = request.task
    user += f"\n\nVersion-controlled Sputnik knowledge:\n{build_agent_context(settings.config_dir)}"
    if request.role == "news":
        news = store.latest_news(limit=30)
        user += "\n\nStored source-linked news (newest first):\n" + json.dumps(
            news, sort_keys=True, default=str
        )[:10_000]
    if request.context:
        user += f"\n\nProvided context:\n{request.context}"
    if request.output_format == "json":
        user += "\n\nReturn one valid JSON object and no surrounding prose."
    payload = json.dumps(
        {
            "model": request.model,
            "messages": [
                {"role": "system", "content": f"{boundary}\n{role}"},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "max_tokens": 2_000,
        }
    ).encode("utf-8")
    http_request = urllib.request.Request(
        f"{settings.lmstudio_base_url}/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            http_request, timeout=settings.granite_timeout_seconds
        ) as response:
            body = json.load(response)
    except urllib.error.URLError as error:
        raise RuntimeError(f"LM Studio request failed: {error.reason}") from error
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise RuntimeError("LM Studio returned an invalid chat-completion response") from error
    fingerprint = hashlib.sha256(
        json.dumps(request_data, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "agent": request.role,
        "model": request.model,
        "output_format": request.output_format,
        "content": content,
        "provenance": "model-derived local LM Studio output",
        "action_boundary": "analysis only; no broker, orders, shell, or secret access",
    }, fingerprint


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
        elif job["kind"] == "granite_agent":
            result, fingerprint = _run_granite_agent_job(job["request"], store, settings)
        else:
            raise ValueError(f"unsupported job kind: {job['kind']}")
        store.complete_job(job["id"], result, fingerprint, __version__)
    except Exception as error:
        store.fail_job(job["id"], f"{type(error).__name__}: {error}")
    return store.get_job(job["id"])


def run_worker(
    settings: Settings,
    poll_seconds: float = 5.0,
    once: bool = False,
    concurrency: int | None = None,
) -> None:
    store = MarketStore(settings.database_path)
    worker_count = concurrency or settings.granite_worker_count

    def process_one() -> dict[str, Any] | None:
        return run_worker_once(MarketStore(settings.database_path), settings)

    while True:
        if settings.scheduler_enabled:
            schedule_due_jobs(store)
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            results = list(pool.map(lambda _: process_one(), range(worker_count)))
        for result in (item for item in results if item):
            print(json.dumps({"id": result["id"], "status": result["status"]}))
        if once:
            return
        if not any(results):
            time.sleep(poll_seconds)
