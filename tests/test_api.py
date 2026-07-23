import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from sputnik.api import create_app
from sputnik.settings import Settings


def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_path=tmp_path / "test.sqlite3",
        reports_dir=tmp_path / "reports",
        config_dir=Path("configs"),
        tv_webhook_id="webhook-path-id-that-is-long-enough",
        tv_shared_secret="payload-secret-that-is-long-enough",
        news_webhook_id="news-path-id-that-is-long-enough",
        news_shared_secret="news-secret-that-is-long-enough",
        news_allowed_domains=("example.com",),
        api_key="reader-key-that-is-long-enough",
        operator_key="operator-key-that-is-long-enough",
        admin_key="admin-key-that-is-long-enough",
        max_data_age_seconds=900,
    )


def bar_payload(**overrides):
    payload = {
        "schema_version": 1,
        "kind": "bar",
        "secret": "payload-secret-that-is-long-enough",
        "symbol": "ASX:OOO",
        "timeframe": "1D",
        "timestamp": "2026-07-23T06:00:00Z",
        "open": 8.42,
        "high": 8.55,
        "low": 8.38,
        "close": 8.48,
        "volume": 206000,
        "confirmed": True,
    }
    payload.update(overrides)
    return payload


def test_webhook_auth_ingestion_and_reader_api(tmp_path):
    runtime = settings(tmp_path)
    client = TestClient(create_app(runtime))
    url = "/v1/webhooks/tradingview/webhook-path-id-that-is-long-enough"

    assert client.post(url, json=bar_payload(secret="wrong-but-still-long-enough")).status_code == 401
    response = client.post(url, json=bar_payload())
    assert response.status_code == 202
    assert response.json()["stored"] is True

    replay = client.post(url, json=bar_payload())
    assert replay.status_code == 202
    assert replay.json()["duplicate"] is True

    assert client.get("/v1/prices/latest").status_code == 401
    headers = {"Authorization": "Bearer reader-key-that-is-long-enough"}
    latest = client.get(
        "/v1/prices/latest?symbols=ASX:OOO&timeframe=1D", headers=headers
    )
    assert latest.status_code == 200
    assert latest.json()["prices"][0]["price"] == 8.48
    assert latest.json()["prices"][0]["provenance"] == "tradingview_webhook_observation"

    context = client.get("/v1/context/ASX:OOO", headers=headers)
    assert context.status_code == 200
    assert context.json()["execution_boundary"] == "NO BROKER - NO ORDERS"

    with sqlite3.connect(runtime.database_path) as connection:
        stored = connection.execute("SELECT payload_json FROM webhook_events").fetchone()[0]
    assert "payload-secret" not in stored
    assert "secret" not in json.loads(stored)


def test_incomplete_bar_and_conflicting_history_are_rejected(tmp_path):
    client = TestClient(create_app(settings(tmp_path)))
    url = "/v1/webhooks/tradingview/webhook-path-id-that-is-long-enough"

    assert client.post(url, json=bar_payload(confirmed=False)).status_code == 422
    assert client.post(url, json=bar_payload()).status_code == 202
    conflict = client.post(url, json=bar_payload(close=8.49))
    assert conflict.status_code == 409


def test_research_admin_key_is_separate(tmp_path):
    client = TestClient(create_app(settings(tmp_path)))
    request = {
        "symbols": ["ASX:OOO"],
        "timeframe": "1D",
        "factor_symbols": {},
        "rules": [],
        "cost_bps": 10,
        "minimum_samples": 2,
    }
    reader = {"Authorization": "Bearer reader-key-that-is-long-enough"}
    admin = {"Authorization": "Bearer admin-key-that-is-long-enough"}
    assert client.post("/v1/research/weekend-gaps", json=request, headers=reader).status_code == 401
    queued = client.post("/v1/research/weekend-gaps", json=request, headers=admin)
    assert queued.status_code == 202
    job_id = queued.json()["id"]
    assert client.get(f"/v1/research/jobs/{job_id}", headers=reader).status_code == 200


def test_news_webhook_and_chatgpt_compute_surface(tmp_path):
    runtime = settings(tmp_path)
    client = TestClient(create_app(runtime))
    news = {
        "schema_version": 1,
        "secret": "news-secret-that-is-long-enough",
        "source": "Example source",
        "title": "Timestamped headline",
        "url": "https://news.example.com/item/1",
        "published_at": "2026-07-23T08:00:00Z",
        "symbols": ["ASX:OOO"],
        "summary": "Short source-linked synopsis.",
    }
    response = client.post(
        "/v1/webhooks/news/news-path-id-that-is-long-enough", json=news
    )
    assert response.status_code == 202
    reader = {"Authorization": "Bearer reader-key-that-is-long-enough"}
    latest = client.get("/v1/news/latest?symbols=ASX:OOO", headers=reader)
    assert latest.status_code == 200
    assert latest.json()["items"][0]["title"] == "Timestamped headline"

    forecast = client.post(
        "/v1/work/forecasts",
        headers=reader,
        json={"symbol": "ASX:OOO", "timeframe": "1D"},
    )
    assert forecast.status_code == 202
    assert forecast.json()["kind"] == "forecast"

    with sqlite3.connect(runtime.database_path) as connection:
        stored = connection.execute("SELECT payload_json FROM news_items").fetchone()[0]
    assert "news-secret" not in stored


def test_operator_configuration_amendment_and_webhook_analytics(tmp_path):
    runtime = settings(tmp_path)
    client = TestClient(create_app(runtime))
    assert client.post(
        "/v1/webhooks/tradingview/webhook-path-id-that-is-long-enough",
        json=bar_payload(),
    ).status_code == 202
    reader = {"Authorization": "Bearer reader-key-that-is-long-enough"}
    operator = {"Authorization": "Bearer operator-key-that-is-long-enough"}
    configuration = {
        "asx_universe": ["ASX:EVN", "ASX:BHP", "ASX:NST", "ASX:WDS", "ASX:OOO"],
        "top_n": 5,
        "reason": "initial liquid ASX panel",
        "actor": "chatgpt_work",
    }
    assert client.put("/v1/work/configuration", json=configuration, headers=reader).status_code == 401
    saved = client.put("/v1/work/configuration", json=configuration, headers=operator)
    assert saved.status_code == 200
    assert saved.json()["version"] == 1
    assert client.get("/v1/work/configuration", headers=reader).json()["configuration"]["top_n"] == 5

    amendment = {
        "target_kind": "bar",
        "symbol": "ASX:OOO",
        "timeframe": "1D",
        "event_time": "2026-07-23T06:00:00Z",
        "open": 8.42,
        "high": 8.55,
        "low": 8.38,
        "close": 8.50,
        "volume": 206000,
        "reason": "checked against the visible completed bar",
        "actor": "chatgpt_work",
    }
    created = client.post("/v1/work/price-amendments", json=amendment, headers=operator)
    assert created.status_code == 201
    assert created.json()["original_preserved"] is True
    latest = client.get("/v1/prices/latest?symbols=ASX:OOO", headers=reader)
    assert latest.json()["prices"][0]["price"] == 8.50
    analytics = client.get("/v1/analytics/webhooks", headers=reader)
    assert analytics.status_code == 200
    assert analytics.json()["market_streams"][0]["event_count"] == 1


def test_edge_sync_requires_admin_and_strips_injected_secret(tmp_path):
    runtime = settings(tmp_path)
    client = TestClient(create_app(runtime))
    payload = bar_payload()
    payload.pop("secret")
    assert client.post("/v1/internal/edge-sync/market", json=payload).status_code == 401
    response = client.post(
        "/v1/internal/edge-sync/market",
        json=payload,
        headers={"Authorization": "Bearer admin-key-that-is-long-enough"},
    )
    assert response.status_code == 202
    with sqlite3.connect(runtime.database_path) as connection:
        stored = connection.execute("SELECT payload_json FROM webhook_events").fetchone()[0]
    assert "edge-validated-payload" not in stored
