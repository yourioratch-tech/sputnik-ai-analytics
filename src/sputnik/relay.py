from __future__ import annotations

import json
import socket
import ssl
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import certifi

from .jobs import _run_granite_agent_job
from .settings import Settings
from .storage import MarketStore


def _post(base_url: str, path: str, key: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "SputnikGraniteRelay/1.0",
        },
        method="POST",
    )
    tls = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(request, timeout=30, context=tls) as response:
        return json.load(response)


def _relay_loop(
    settings: Settings, base_url: str, key: str, poll_seconds: float, slot: int
) -> None:
    store = MarketStore(settings.database_path)
    worker_id = f"{socket.gethostname()}-granite-{slot}"
    while True:
        try:
            claimed = _post(base_url, "/v1/internal/agents/claim", key, {"worker_id": worker_id})
            job = claimed.get("job")
            if not job:
                time.sleep(poll_seconds)
                continue
            request_data = {name: job.get(name) for name in ("task", "context", "role", "output_format", "model")}
            try:
                result, _fingerprint = _run_granite_agent_job(request_data, store, settings)
                payload = {"status": "completed", "result": result}
            except Exception as error:
                payload = {"status": "failed", "error": f"{type(error).__name__}: {error}"}
            _post(base_url, f"/v1/internal/agents/{job['id']}/complete", key, payload)
        except Exception as error:
            print(json.dumps({"relay_error": f"{type(error).__name__}: {error}"}), flush=True)
            time.sleep(max(5, poll_seconds))


def run_relay(
    settings: Settings,
    base_url: str,
    key: str,
    poll_seconds: float = 3,
    concurrency: int = 4,
) -> None:
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        list(
            pool.map(
                lambda slot: _relay_loop(settings, base_url, key, poll_seconds, slot),
                range(concurrency),
            )
        )
