from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import urlparse

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .auth import require_admin, require_operator, require_reader, secrets_match
from .models import (
    SYMBOL_PATTERN,
    TIMEFRAME_PATTERN,
    AsxHuntJobRequest,
    BacktestJobRequest,
    ControlConfiguration,
    ForecastJobRequest,
    NewsWebhookEvent,
    PortfolioReviewJobRequest,
    PriceAmendmentRequest,
    TradingViewEvent,
    WeekendGapJobRequest,
)
from .portfolio import fifo_analytics
from .settings import Settings
from .storage import BarConflictError, MarketStore

MAX_WEBHOOK_BYTES = 64_000


def _normalise_webhook_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    if "symbol" not in result:
        result["symbol"] = result.pop("ticker", None)
    if "timestamp" not in result:
        result["timestamp"] = result.pop("time", result.pop("bar_time", None))
    return result


def _normalise_symbol(symbol: str) -> str:
    value = symbol.strip().upper()
    if not SYMBOL_PATTERN.fullmatch(value):
        raise HTTPException(status_code=422, detail="invalid symbol")
    return value


def _normalise_timeframe(timeframe: str | None) -> str | None:
    if timeframe is None:
        return None
    value = timeframe.strip().upper()
    if not TIMEFRAME_PATTERN.fullmatch(value):
        raise HTTPException(status_code=422, detail="invalid timeframe")
    return value


def _age_seconds(timestamp: str) -> float:
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return max(0.0, (datetime.now(UTC) - parsed).total_seconds())


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime = settings or Settings.from_env()
    store = MarketStore(runtime.database_path)
    app = FastAPI(
        title="Sputnik Market Evidence API",
        summary="Read-only market telemetry and auditable research jobs.",
        version="0.3.0",
        contact={"name": "Sputnik"},
        license_info={"name": "MIT"},
    )
    app.state.settings = runtime
    app.state.store = store

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/health", tags=["operations"])
    def health() -> dict[str, Any]:
        counts = store.event_counts()
        return {
            "status": "ok",
            "ready": not runtime.missing_runtime_secrets(),
            "system": "sputnik-ai-analytics",
            "version": app.version,
            "event_counts": counts,
            "broker_connected": False,
            "orders_enabled": False,
        }

    @app.post(
        "/v1/webhooks/tradingview/{webhook_id}",
        status_code=status.HTTP_202_ACCEPTED,
        tags=["ingestion"],
        include_in_schema=False,
    )
    async def tradingview_webhook(webhook_id: str, request: Request) -> JSONResponse:
        if not runtime.tv_webhook_id or not runtime.tv_shared_secret:
            raise HTTPException(status_code=503, detail="webhook ingestion is not configured")
        if not secrets_match(webhook_id, runtime.tv_webhook_id):
            raise HTTPException(status_code=401, detail="invalid webhook identity")
        length = request.headers.get("content-length")
        if length:
            try:
                if int(length) > MAX_WEBHOOK_BYTES:
                    raise HTTPException(status_code=413, detail="payload too large")
            except ValueError as error:
                raise HTTPException(status_code=400, detail="invalid content length") from error
        body = await request.body()
        if not body or len(body) > MAX_WEBHOOK_BYTES:
            raise HTTPException(status_code=413, detail="payload size is invalid")
        try:
            raw = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise HTTPException(status_code=400, detail="valid JSON object required") from error
        if not isinstance(raw, dict):
            raise HTTPException(status_code=400, detail="valid JSON object required")
        try:
            event = TradingViewEvent.model_validate(_normalise_webhook_payload(raw))
        except ValidationError as error:
            return JSONResponse(
                status_code=422,
                content={
                    "detail": error.errors(include_url=False, include_context=False)
                },
            )
        if not secrets_match(event.secret, runtime.tv_shared_secret):
            raise HTTPException(status_code=401, detail="invalid webhook secret")
        try:
            recorded = store.record_event(event)
        except BarConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return JSONResponse(
            status_code=202,
            content={
                "status": "accepted",
                **recorded,
                "symbol": event.symbol,
                "timeframe": event.timeframe,
                "kind": event.kind,
            },
        )

    @app.post(
        "/v1/webhooks/news/{webhook_id}",
        status_code=status.HTTP_202_ACCEPTED,
        tags=["ingestion"],
        include_in_schema=False,
    )
    async def news_webhook(webhook_id: str, request: Request) -> JSONResponse:
        if not runtime.news_webhook_id or not runtime.news_shared_secret:
            raise HTTPException(status_code=503, detail="news ingestion is not configured")
        if not secrets_match(webhook_id, runtime.news_webhook_id):
            raise HTTPException(status_code=401, detail="invalid webhook identity")
        body = await request.body()
        if not body or len(body) > MAX_WEBHOOK_BYTES:
            raise HTTPException(status_code=413, detail="payload size is invalid")
        try:
            raw = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise HTTPException(status_code=400, detail="valid JSON object required") from error
        if not isinstance(raw, dict):
            raise HTTPException(status_code=400, detail="valid JSON object required")
        try:
            event = NewsWebhookEvent.model_validate(raw)
        except ValidationError as error:
            return JSONResponse(
                status_code=422,
                content={
                    "detail": error.errors(include_url=False, include_context=False)
                },
            )
        if not secrets_match(event.secret, runtime.news_shared_secret):
            raise HTTPException(status_code=401, detail="invalid news webhook secret")
        hostname = (urlparse(str(event.url)).hostname or "").lower()
        if runtime.news_allowed_domains and not any(
            hostname == domain or hostname.endswith(f".{domain}")
            for domain in runtime.news_allowed_domains
        ):
            raise HTTPException(status_code=422, detail="news source domain is not approved")
        recorded = store.record_news(event)
        return JSONResponse(
            status_code=202,
            content={
                "status": "accepted",
                **recorded,
                "source": event.source,
                "published_at": event.published_at.isoformat(),
            },
        )

    @app.post(
        "/v1/internal/edge-sync/market",
        status_code=status.HTTP_202_ACCEPTED,
        tags=["operations"],
        include_in_schema=False,
    )
    def sync_market_from_edge(
        payload: Annotated[dict[str, Any], Body()],
        _: Annotated[None, Depends(require_admin)],
    ) -> dict[str, Any]:
        try:
            event = TradingViewEvent.model_validate(
                {**payload, "secret": "edge-validated-payload"}
            )
            return {"status": "accepted", **store.record_event(event)}
        except (ValidationError, BarConflictError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post(
        "/v1/internal/edge-sync/news",
        status_code=status.HTTP_202_ACCEPTED,
        tags=["operations"],
        include_in_schema=False,
    )
    def sync_news_from_edge(
        payload: Annotated[dict[str, Any], Body()],
        _: Annotated[None, Depends(require_admin)],
    ) -> dict[str, Any]:
        try:
            event = NewsWebhookEvent.model_validate(
                {**payload, "secret": "edge-validated-payload"}
            )
            return {"status": "accepted", **store.record_news(event)}
        except ValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/v1/prices/latest", tags=["market evidence"])
    def latest_prices(
        _: Annotated[None, Depends(require_reader)],
        symbols: Annotated[str | None, Query(description="Comma-separated TradingView symbols")] = None,
        timeframe: str | None = None,
    ) -> dict[str, Any]:
        selected = None
        if symbols:
            selected = [_normalise_symbol(item) for item in symbols.split(",") if item.strip()]
            if not selected or len(selected) > 100:
                raise HTTPException(status_code=422, detail="select between 1 and 100 symbols")
        rows = store.latest_prices(selected, _normalise_timeframe(timeframe))
        for row in rows:
            row["age_seconds"] = round(_age_seconds(row["event_time"]), 3)
            row["freshness"] = (
                "fresh" if row["age_seconds"] <= runtime.max_data_age_seconds else "stale"
            )
            row["provenance"] = "tradingview_webhook_observation"
        return {
            "as_of": datetime.now(UTC).isoformat(),
            "count": len(rows),
            "prices": rows,
            "warning": "Webhook observations are not independently verified exchange truth.",
        }

    @app.get("/v1/prices/history", tags=["market evidence"])
    def price_history(
        _: Annotated[None, Depends(require_reader)],
        symbol: str,
        timeframe: str | None = None,
        limit: Annotated[int, Query(ge=1, le=2_000)] = 500,
    ) -> dict[str, Any]:
        clean_symbol = _normalise_symbol(symbol)
        clean_timeframe = _normalise_timeframe(timeframe)
        rows = store.price_history(clean_symbol, clean_timeframe, limit)
        return {
            "symbol": clean_symbol,
            "timeframe": clean_timeframe,
            "count": len(rows),
            "points": rows,
            "provenance": "tradingview_webhook_observation",
        }

    @app.get("/v1/context/{symbol}", tags=["market evidence"])
    def market_context(
        symbol: str,
        _: Annotated[None, Depends(require_reader)],
        event_limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> dict[str, Any]:
        clean_symbol = _normalise_symbol(symbol)
        prices = store.latest_prices([clean_symbol])
        events = store.recent_events(clean_symbol, event_limit)
        news = store.latest_news([clean_symbol], event_limit)
        portfolio = store.portfolio_symbol_summary(clean_symbol.split(":")[-1])
        for price in prices:
            price["age_seconds"] = round(_age_seconds(price["event_time"]), 3)
            price["freshness"] = (
                "fresh" if price["age_seconds"] <= runtime.max_data_age_seconds else "stale"
            )
        return {
            "symbol": clean_symbol,
            "as_of": datetime.now(UTC).isoformat(),
            "latest_by_timeframe": prices,
            "recent_events": events,
            "recent_news": news,
            "private_portfolio_activity": portfolio,
            "evidence_class": "observed_webhook",
            "unknowns": [
                "No independent exchange reconciliation is implied.",
                "A price-point stream is not a complete OHLCV history unless every completed bar is sent.",
            ],
            "execution_boundary": "NO BROKER - NO ORDERS",
        }

    @app.get("/v1/news/latest", tags=["news evidence"])
    def latest_news(
        _: Annotated[None, Depends(require_reader)],
        symbols: Annotated[str | None, Query(description="Comma-separated symbols")] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> dict[str, Any]:
        selected = None
        if symbols:
            selected = [_normalise_symbol(item) for item in symbols.split(",") if item.strip()]
            if not selected or len(selected) > 100:
                raise HTTPException(status_code=422, detail="select between 1 and 100 symbols")
        items = store.latest_news(selected, limit)
        return {
            "as_of": datetime.now(UTC).isoformat(),
            "count": len(items),
            "items": items,
            "provenance": "approved_news_webhook_metadata",
            "warning": "Headlines and summaries require source review; no direction is implied.",
        }

    @app.get("/v1/analytics/webhooks", tags=["market evidence"])
    def webhook_analytics(
        _: Annotated[None, Depends(require_reader)],
    ) -> dict[str, Any]:
        result = store.webhook_analytics()
        result["meaning"] = (
            "Counts and last-received times show what the system actually has; "
            "they do not prove complete exchange coverage."
        )
        return result

    @app.get("/v1/work/configuration", tags=["ChatGPT work"])
    def get_work_configuration(
        _: Annotated[None, Depends(require_reader)],
    ) -> dict[str, Any]:
        revision = store.get_control_configuration()
        if not revision:
            raise HTTPException(status_code=404, detail="configuration has not been set")
        return revision

    @app.put("/v1/work/configuration", tags=["ChatGPT work"])
    def put_work_configuration(
        request_body: Annotated[ControlConfiguration, Body()],
        _: Annotated[None, Depends(require_operator)],
    ) -> dict[str, Any]:
        return store.set_control_configuration(request_body)

    @app.get("/v1/work/price-amendments", tags=["ChatGPT work"])
    def get_price_amendments(
        _: Annotated[None, Depends(require_reader)],
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> dict[str, Any]:
        items = store.list_price_amendments(limit)
        return {"count": len(items), "items": items, "original_history_preserved": True}

    @app.post(
        "/v1/work/price-amendments",
        status_code=status.HTTP_201_CREATED,
        tags=["ChatGPT work"],
    )
    def create_price_amendment(
        request_body: Annotated[PriceAmendmentRequest, Body()],
        _: Annotated[None, Depends(require_operator)],
    ) -> dict[str, Any]:
        try:
            return store.record_price_amendment(request_body)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/v1/portfolio/summary", tags=["private portfolio evidence"])
    def private_portfolio_summary(
        _: Annotated[None, Depends(require_reader)],
    ) -> dict[str, Any]:
        summary = store.portfolio_summary()
        if not summary:
            raise HTTPException(status_code=404, detail="no private portfolio data imported")
        transactions = store.portfolio_transactions()
        fifo = fifo_analytics(transactions)
        fifo.pop("round_trips", None)
        summary["fifo_behavior"] = fifo
        summary["provenance"] = "private broker exports supplied by the user"
        summary["warning"] = "Provisional analytics only; not a tax-lot or broker reconciliation."
        return summary

    @app.post(
        "/v1/research/backtests",
        status_code=status.HTTP_202_ACCEPTED,
        tags=["research"],
    )
    def enqueue_backtest(
        request_body: Annotated[BacktestJobRequest, Body()],
        _: Annotated[None, Depends(require_admin)],
    ) -> dict[str, Any]:
        return store.enqueue_backtest(request_body)

    @app.get("/v1/research/backtests/{job_id}", tags=["research"])
    def get_backtest(
        job_id: str,
        _: Annotated[None, Depends(require_reader)],
    ) -> dict[str, Any]:
        job = store.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="research job not found")
        return job

    @app.post(
        "/v1/research/weekend-gaps",
        status_code=status.HTTP_202_ACCEPTED,
        tags=["research"],
    )
    def enqueue_weekend_gap(
        request_body: Annotated[WeekendGapJobRequest, Body()],
        _: Annotated[None, Depends(require_admin)],
    ) -> dict[str, Any]:
        return store.enqueue_weekend_gap(request_body)

    @app.post(
        "/v1/work/forecasts",
        status_code=status.HTTP_202_ACCEPTED,
        tags=["ChatGPT work"],
    )
    def work_forecast(
        request_body: Annotated[ForecastJobRequest, Body()],
        _: Annotated[None, Depends(require_reader)],
    ) -> dict[str, Any]:
        return store.enqueue_forecast(request_body)

    @app.post(
        "/v1/work/backtests",
        status_code=status.HTTP_202_ACCEPTED,
        tags=["ChatGPT work"],
    )
    def work_backtest(
        request_body: Annotated[BacktestJobRequest, Body()],
        _: Annotated[None, Depends(require_reader)],
    ) -> dict[str, Any]:
        return store.enqueue_backtest(request_body)

    @app.post(
        "/v1/work/weekend-gaps",
        status_code=status.HTTP_202_ACCEPTED,
        tags=["ChatGPT work"],
    )
    def work_weekend_gap(
        request_body: Annotated[WeekendGapJobRequest, Body()],
        _: Annotated[None, Depends(require_reader)],
    ) -> dict[str, Any]:
        return store.enqueue_weekend_gap(request_body)

    @app.post(
        "/v1/work/portfolio-reviews",
        status_code=status.HTTP_202_ACCEPTED,
        tags=["ChatGPT work"],
    )
    def work_portfolio_review(
        request_body: Annotated[PortfolioReviewJobRequest, Body()],
        _: Annotated[None, Depends(require_reader)],
    ) -> dict[str, Any]:
        return store.enqueue_portfolio_review(request_body)

    @app.post(
        "/v1/work/asx-hunts",
        status_code=status.HTTP_202_ACCEPTED,
        tags=["ChatGPT work"],
    )
    def work_asx_hunt(
        request_body: Annotated[AsxHuntJobRequest, Body()],
        _: Annotated[None, Depends(require_operator)],
    ) -> dict[str, Any]:
        return store.enqueue_asx_hunt(request_body)

    @app.get("/v1/research/jobs/{job_id}", tags=["research"])
    def get_research_job(
        job_id: str,
        _: Annotated[None, Depends(require_reader)],
    ) -> dict[str, Any]:
        job = store.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="research job not found")
        return job

    return app


app = create_app()
