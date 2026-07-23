from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .models import (
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


class BarConflictError(ValueError):
    """Raised when a completed bar attempts to rewrite stored market history."""


def utc_now() -> datetime:
    return datetime.now(UTC)


class MarketStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            yield connection
        finally:
            connection.close()

    def migrate(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;

                CREATE TABLE IF NOT EXISTS webhook_events (
                    id TEXT PRIMARY KEY,
                    received_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    event_time TEXT NOT NULL,
                    event_name TEXT,
                    price REAL,
                    payload_sha256 TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS webhook_events_symbol_time
                    ON webhook_events(symbol, timeframe, event_time DESC);

                CREATE TABLE IF NOT EXISTS market_bars (
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    event_time TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL NOT NULL,
                    received_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    event_id TEXT NOT NULL REFERENCES webhook_events(id),
                    PRIMARY KEY(symbol, timeframe, event_time)
                );

                CREATE INDEX IF NOT EXISTS market_bars_latest
                    ON market_bars(symbol, timeframe, event_time DESC);

                CREATE TABLE IF NOT EXISTS price_points (
                    event_id TEXT PRIMARY KEY REFERENCES webhook_events(id),
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    event_time TEXT NOT NULL,
                    price REAL NOT NULL,
                    received_at TEXT NOT NULL,
                    source TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS price_points_latest
                    ON price_points(symbol, timeframe, event_time DESC);

                CREATE TABLE IF NOT EXISTS news_items (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    category TEXT,
                    summary TEXT,
                    payload_sha256 TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS news_items_published
                    ON news_items(published_at DESC);

                CREATE TABLE IF NOT EXISTS news_symbols (
                    news_id TEXT NOT NULL REFERENCES news_items(id) ON DELETE CASCADE,
                    symbol TEXT NOT NULL,
                    PRIMARY KEY(news_id, symbol)
                );

                CREATE INDEX IF NOT EXISTS news_symbols_symbol
                    ON news_symbols(symbol, news_id);

                CREATE TABLE IF NOT EXISTS research_jobs (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    requested_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    request_json TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT,
                    dataset_sha256 TEXT,
                    engine_version TEXT
                );

                CREATE INDEX IF NOT EXISTS research_jobs_status
                    ON research_jobs(status, requested_at);

                CREATE TABLE IF NOT EXISTS price_amendments (
                    id TEXT PRIMARY KEY,
                    target_kind TEXT NOT NULL CHECK(target_kind IN ('bar', 'price')),
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    event_time TEXT NOT NULL,
                    replacement_json TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    source_url TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS price_amendments_target
                    ON price_amendments(symbol, timeframe, event_time, target_kind, created_at DESC);

                CREATE TABLE IF NOT EXISTS control_configuration_revisions (
                    id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL UNIQUE,
                    configuration_json TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS scheduler_runs (
                    task_key TEXT PRIMARY KEY,
                    run_at TEXT NOT NULL,
                    job_id TEXT
                );

                CREATE TABLE IF NOT EXISTS portfolio_imports (
                    id TEXT PRIMARY KEY,
                    source_kind TEXT NOT NULL,
                    imported_at TEXT NOT NULL,
                    row_count INTEGER NOT NULL,
                    period_start TEXT NOT NULL,
                    period_end TEXT NOT NULL,
                    symbol_count INTEGER NOT NULL,
                    brokerage_total REAL NOT NULL,
                    gross_trade_value REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS portfolio_transactions (
                    id TEXT PRIMARY KEY,
                    import_id TEXT NOT NULL REFERENCES portfolio_imports(id),
                    source_row INTEGER NOT NULL,
                    code TEXT NOT NULL,
                    company TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    side TEXT NOT NULL CHECK(side IN ('buy', 'sell')),
                    quantity REAL NOT NULL,
                    unit_price REAL NOT NULL,
                    trade_value REAL NOT NULL,
                    brokerage REAL NOT NULL,
                    gst REAL NOT NULL,
                    total_value REAL NOT NULL,
                    cash_flow REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS portfolio_transactions_code_date
                    ON portfolio_transactions(code, trade_date, source_row);

                CREATE TABLE IF NOT EXISTS portfolio_summaries (
                    id TEXT PRIMARY KEY,
                    source_kind TEXT NOT NULL,
                    imported_at TEXT NOT NULL,
                    statement_end TEXT NOT NULL,
                    shares_value REAL NOT NULL,
                    cash_value REAL NOT NULL,
                    portfolio_value REAL NOT NULL,
                    franked_dividends REAL NOT NULL,
                    unfranked_dividends REAL NOT NULL,
                    franking_credits REAL NOT NULL,
                    cash_interest REAL NOT NULL,
                    total_income REAL NOT NULL,
                    brokerage REAL NOT NULL,
                    subscriptions REAL NOT NULL,
                    other_fees REAL NOT NULL,
                    total_fees REAL NOT NULL
                );
                """
            )
            connection.commit()

    def record_event(self, event: TradingViewEvent) -> dict[str, Any]:
        payload = event.safe_payload()
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        event_id = digest
        received_at = utc_now().isoformat()
        event_time = event.timestamp.isoformat()
        point = event.price if event.price is not None else event.close

        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute(
                "SELECT id FROM webhook_events WHERE id = ?", (event_id,)
            ).fetchone()
            if prior:
                connection.rollback()
                return {"event_id": event_id, "duplicate": True, "stored": False}

            if event.kind == "bar":
                existing = connection.execute(
                    """
                    SELECT open, high, low, close, volume
                    FROM market_bars
                    WHERE symbol = ? AND timeframe = ? AND event_time = ?
                    """,
                    (event.symbol, event.timeframe, event_time),
                ).fetchone()
                if existing:
                    supplied = (event.open, event.high, event.low, event.close, event.volume)
                    stored = tuple(existing)
                    if supplied != stored:
                        connection.rollback()
                        raise BarConflictError(
                            "completed bar conflicts with immutable stored history"
                        )
                    connection.rollback()
                    return {"event_id": event_id, "duplicate": True, "stored": False}

            connection.execute(
                """
                INSERT INTO webhook_events(
                    id, received_at, source, kind, symbol, timeframe, event_time,
                    event_name, price, payload_sha256, payload_json
                ) VALUES (?, ?, 'tradingview', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    received_at,
                    event.kind,
                    event.symbol,
                    event.timeframe,
                    event_time,
                    event.event,
                    point,
                    digest,
                    canonical,
                ),
            )
            if point is not None:
                connection.execute(
                    """
                    INSERT INTO price_points(
                        event_id, symbol, timeframe, event_time, price, received_at, source
                    ) VALUES (?, ?, ?, ?, ?, ?, 'tradingview')
                    """,
                    (
                        event_id,
                        event.symbol,
                        event.timeframe,
                        event_time,
                        point,
                        received_at,
                    ),
                )
            if event.kind == "bar":
                connection.execute(
                    """
                    INSERT INTO market_bars(
                        symbol, timeframe, event_time, open, high, low, close, volume,
                        received_at, source, event_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'tradingview', ?)
                    """,
                    (
                        event.symbol,
                        event.timeframe,
                        event_time,
                        event.open,
                        event.high,
                        event.low,
                        event.close,
                        event.volume,
                        received_at,
                        event_id,
                    ),
                )
            connection.commit()
        return {"event_id": event_id, "duplicate": False, "stored": True}

    def record_news(self, event: NewsWebhookEvent) -> dict[str, Any]:
        payload = event.safe_payload()
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        received_at = utc_now().isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            exists = connection.execute(
                "SELECT id FROM news_items WHERE id = ?", (digest,)
            ).fetchone()
            if exists:
                connection.rollback()
                return {"news_id": digest, "duplicate": True, "stored": False}
            connection.execute(
                """
                INSERT INTO news_items(
                    id, source, title, url, published_at, received_at, category,
                    summary, payload_sha256, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    digest,
                    event.source,
                    event.title,
                    str(event.url),
                    event.published_at.isoformat(),
                    received_at,
                    event.category,
                    event.summary,
                    digest,
                    canonical,
                ),
            )
            connection.executemany(
                "INSERT INTO news_symbols(news_id, symbol) VALUES (?, ?)",
                [(digest, symbol) for symbol in event.symbols],
            )
            connection.commit()
        return {"news_id": digest, "duplicate": False, "stored": True}

    @staticmethod
    def _rows(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        return [dict(row) for row in rows]

    def latest_prices(
        self, symbols: list[str] | None = None, timeframe: str | None = None
    ) -> list[dict[str, Any]]:
        conditions = []
        parameters: list[Any] = []
        if symbols:
            conditions.append(f"symbol IN ({','.join('?' for _ in symbols)})")
            parameters.extend(symbols)
        if timeframe:
            conditions.append("timeframe = ?")
            parameters.append(timeframe)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"""
            SELECT symbol, timeframe, event_time, price, received_at, source
            FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY symbol, timeframe ORDER BY event_time DESC, received_at DESC
                ) AS row_number
                FROM price_points {where}
            )
            WHERE row_number = 1
            ORDER BY symbol, timeframe
        """
        with self.connect() as connection:
            rows = self._rows(connection.execute(query, parameters).fetchall())
            for row in rows:
                amendment = connection.execute(
                    """
                    SELECT id, replacement_json, reason, actor, created_at
                    FROM price_amendments
                    WHERE symbol=? AND timeframe=? AND event_time=?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (row["symbol"], row["timeframe"], row["event_time"]),
                ).fetchone()
                if amendment:
                    replacement = json.loads(amendment["replacement_json"])
                    row["price"] = replacement.get("price", replacement.get("close", row["price"]))
                    row["amendment"] = {
                        "id": amendment["id"],
                        "reason": amendment["reason"],
                        "actor": amendment["actor"],
                        "created_at": amendment["created_at"],
                    }
        return rows

    def price_history(
        self, symbol: str, timeframe: str | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        parameters: list[Any] = [symbol]
        condition = "symbol = ?"
        if timeframe:
            condition += " AND timeframe = ?"
            parameters.append(timeframe)
        parameters.append(limit)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT symbol, timeframe, event_time, price, received_at, source
                FROM (
                    SELECT symbol, timeframe, event_time, price, received_at, source
                    FROM price_points WHERE {condition}
                    ORDER BY event_time DESC LIMIT ?
                ) ORDER BY event_time ASC
                """,
                parameters,
            ).fetchall()
            result = self._rows(rows)
            for row in result:
                amendment = connection.execute(
                    """
                    SELECT id, replacement_json, reason, actor, created_at
                    FROM price_amendments
                    WHERE symbol=? AND timeframe=? AND event_time=?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (row["symbol"], row["timeframe"], row["event_time"]),
                ).fetchone()
                if amendment:
                    replacement = json.loads(amendment["replacement_json"])
                    row["price"] = replacement.get("price", replacement.get("close", row["price"]))
                    row["amendment_id"] = amendment["id"]
        return result

    def load_bars(self, symbol: str, timeframe: str) -> pd.DataFrame:
        with self.connect() as connection:
            rows = connection.execute(
                """
                WITH latest_amendments AS (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY symbol, timeframe, event_time
                        ORDER BY created_at DESC
                    ) AS row_number
                    FROM price_amendments WHERE target_kind='bar'
                )
                SELECT b.event_time AS date,
                       COALESCE(json_extract(a.replacement_json, '$.open'), b.open) AS open,
                       COALESCE(json_extract(a.replacement_json, '$.high'), b.high) AS high,
                       COALESCE(json_extract(a.replacement_json, '$.low'), b.low) AS low,
                       COALESCE(json_extract(a.replacement_json, '$.close'), b.close) AS close,
                       COALESCE(json_extract(a.replacement_json, '$.volume'), b.volume) AS volume
                FROM market_bars b
                LEFT JOIN latest_amendments a
                  ON a.symbol=b.symbol AND a.timeframe=b.timeframe
                 AND a.event_time=b.event_time AND a.row_number=1
                WHERE b.symbol = ? AND b.timeframe = ?
                ORDER BY b.event_time
                """,
                (symbol, timeframe),
            ).fetchall()
        return pd.DataFrame(self._rows(rows), columns=("date", "open", "high", "low", "close", "volume"))

    def event_counts(self) -> dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT kind, COUNT(*) AS count FROM webhook_events GROUP BY kind"
            ).fetchall()
        return {row["kind"]: row["count"] for row in rows}

    def webhook_analytics(self) -> dict[str, Any]:
        with self.connect() as connection:
            by_kind = self._rows(
                connection.execute(
                    """
                    SELECT kind, COUNT(*) AS event_count,
                           COUNT(DISTINCT symbol) AS symbol_count,
                           MAX(received_at) AS last_received_at
                    FROM webhook_events GROUP BY kind ORDER BY kind
                    """
                ).fetchall()
            )
            by_stream = self._rows(
                connection.execute(
                    """
                    SELECT symbol,timeframe,COUNT(*) AS event_count,
                           MIN(event_time) AS first_event_time,
                           MAX(event_time) AS last_event_time,
                           MAX(received_at) AS last_received_at
                    FROM webhook_events
                    GROUP BY symbol,timeframe
                    ORDER BY symbol,timeframe
                    """
                ).fetchall()
            )
            news = connection.execute(
                """
                SELECT COUNT(*) AS item_count, COUNT(DISTINCT source) AS source_count,
                       MAX(received_at) AS last_received_at FROM news_items
                """
            ).fetchone()
        return {
            "as_of": utc_now().isoformat(),
            "market_by_kind": by_kind,
            "market_streams": by_stream,
            "news": dict(news) if news else {},
        }

    def recent_events(self, symbol: str, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, received_at, source, kind, symbol, timeframe, event_time,
                       event_name, price, payload_sha256, payload_json
                FROM webhook_events
                WHERE symbol = ?
                ORDER BY event_time DESC, received_at DESC
                LIMIT ?
                """,
                (symbol, limit),
            ).fetchall()
        result = self._rows(rows)
        for row in result:
            row["details"] = json.loads(row.pop("payload_json"))
        return result

    def latest_news(
        self, symbols: list[str] | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        parameters: list[Any] = []
        where = ""
        if symbols:
            where = f"WHERE ns.symbol IN ({','.join('?' for _ in symbols)})"
            parameters.extend(symbols)
        parameters.append(limit)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT n.id, n.source, n.title, n.url, n.published_at,
                       n.received_at, n.category, n.summary,
                       GROUP_CONCAT(DISTINCT all_symbols.symbol) AS symbols_csv
                FROM news_items n
                LEFT JOIN news_symbols ns ON ns.news_id = n.id
                LEFT JOIN news_symbols all_symbols ON all_symbols.news_id = n.id
                {where}
                GROUP BY n.id
                ORDER BY n.published_at DESC, n.received_at DESC
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        result = self._rows(rows)
        for row in result:
            value = row.pop("symbols_csv")
            row["symbols"] = sorted(value.split(",")) if value else []
        return result

    def enqueue_backtest(self, request: BacktestJobRequest) -> dict[str, Any]:
        return self._enqueue_job("backtest", request.model_dump(mode="json"))

    def enqueue_weekend_gap(self, request: WeekendGapJobRequest) -> dict[str, Any]:
        return self._enqueue_job("weekend_gap", request.model_dump(mode="json"))

    def enqueue_forecast(self, request: ForecastJobRequest) -> dict[str, Any]:
        return self._enqueue_job("forecast", request.model_dump(mode="json"))

    def enqueue_portfolio_review(self, request: PortfolioReviewJobRequest) -> dict[str, Any]:
        return self._enqueue_job("portfolio_review", request.model_dump(mode="json"))

    def enqueue_asx_hunt(self, request: AsxHuntJobRequest) -> dict[str, Any]:
        return self._enqueue_job("asx_hunt", request.model_dump(mode="json"))

    def record_price_amendment(self, request: PriceAmendmentRequest) -> dict[str, Any]:
        event_time = request.event_time.isoformat()
        replacement = request.replacement()
        canonical = json.dumps(
            {
                "target_kind": request.target_kind,
                "symbol": request.symbol,
                "timeframe": request.timeframe,
                "event_time": event_time,
                "replacement": replacement,
                "reason": request.reason,
                "actor": request.actor,
                "source_url": str(request.source_url) if request.source_url else None,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        amendment_id = hashlib.sha256(canonical.encode()).hexdigest()
        with self.connect() as connection:
            original_table = "market_bars" if request.target_kind == "bar" else "price_points"
            original = connection.execute(
                f"SELECT 1 FROM {original_table} WHERE symbol=? AND timeframe=? AND event_time=? LIMIT 1",
                (request.symbol, request.timeframe, event_time),
            ).fetchone()
            if not original:
                raise ValueError("amendment target does not exist in the immutable ledger")
            connection.execute(
                """
                INSERT OR IGNORE INTO price_amendments(
                    id,target_kind,symbol,timeframe,event_time,replacement_json,
                    reason,actor,source_url,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    amendment_id,
                    request.target_kind,
                    request.symbol,
                    request.timeframe,
                    event_time,
                    json.dumps(replacement, sort_keys=True),
                    request.reason,
                    request.actor,
                    str(request.source_url) if request.source_url else None,
                    utc_now().isoformat(),
                ),
            )
            connection.commit()
        return {
            "id": amendment_id,
            "status": "recorded",
            "original_preserved": True,
            "effective_replacement": replacement,
        }

    def list_price_amendments(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id,target_kind,symbol,timeframe,event_time,replacement_json,
                       reason,actor,source_url,created_at
                FROM price_amendments ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        result = self._rows(rows)
        for row in result:
            row["replacement"] = json.loads(row.pop("replacement_json"))
        return result

    def set_control_configuration(self, request: ControlConfiguration) -> dict[str, Any]:
        payload = request.model_dump(mode="json")
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        revision_id = hashlib.sha256(canonical.encode()).hexdigest()
        with self.connect() as connection:
            current = connection.execute(
                "SELECT COALESCE(MAX(version),0) AS version FROM control_configuration_revisions"
            ).fetchone()
            version = int(current["version"]) + 1
            connection.execute(
                """
                INSERT INTO control_configuration_revisions(
                    id,version,configuration_json,reason,actor,created_at
                ) VALUES (?,?,?,?,?,?)
                """,
                (revision_id, version, canonical, request.reason, request.actor, utc_now().isoformat()),
            )
            connection.commit()
        return {"id": revision_id, "version": version, "configuration": payload}

    def get_control_configuration(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id,version,configuration_json,reason,actor,created_at
                FROM control_configuration_revisions ORDER BY version DESC LIMIT 1
                """
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["configuration"] = json.loads(result.pop("configuration_json"))
        return result

    def claim_schedule(self, task_key: str) -> bool:
        with self.connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO scheduler_runs(task_key,run_at) VALUES (?,?)",
                    (task_key, utc_now().isoformat()),
                )
                connection.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def link_schedule_job(self, task_key: str, job_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE scheduler_runs SET job_id=? WHERE task_key=?", (job_id, task_key)
            )
            connection.commit()

    def import_portfolio(
        self,
        transactions: list[dict[str, Any]],
        manifest: dict[str, Any],
        summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        imported_at = utc_now().isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            exists = connection.execute(
                "SELECT id FROM portfolio_imports WHERE id = ?", (manifest["id"],)
            ).fetchone()
            if exists:
                connection.rollback()
                return {"import_id": manifest["id"], "duplicate": True, "stored": False}
            if summary is not None and abs(manifest["brokerage_total"] - summary["brokerage"]) > 0.01:
                connection.rollback()
                raise ValueError("transaction brokerage does not reconcile to EOFY summary")
            connection.execute(
                """
                INSERT INTO portfolio_imports(
                    id, source_kind, imported_at, row_count, period_start, period_end,
                    symbol_count, brokerage_total, gross_trade_value
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    manifest["id"],
                    manifest["source_kind"],
                    imported_at,
                    manifest["row_count"],
                    manifest["period_start"],
                    manifest["period_end"],
                    manifest["symbol_count"],
                    manifest["brokerage_total"],
                    manifest["gross_trade_value"],
                ),
            )
            connection.executemany(
                """
                INSERT INTO portfolio_transactions(
                    id, import_id, source_row, code, company, trade_date, side,
                    quantity, unit_price, trade_value, brokerage, gst, total_value, cash_flow
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        row["id"],
                        manifest["id"],
                        row["source_row"],
                        row["code"],
                        row["company"],
                        row["trade_date"],
                        row["side"],
                        row["quantity"],
                        row["unit_price"],
                        row["trade_value"],
                        row["brokerage"],
                        row["gst"],
                        row["total_value"],
                        row["cash_flow"],
                    )
                    for row in transactions
                ],
            )
            if summary is not None:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO portfolio_summaries(
                        id, source_kind, imported_at, statement_end, shares_value,
                        cash_value, portfolio_value, franked_dividends, unfranked_dividends,
                        franking_credits, cash_interest, total_income, brokerage,
                        subscriptions, other_fees, total_fees
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        summary["id"],
                        summary["source_kind"],
                        imported_at,
                        summary["statement_end"],
                        summary["shares_value"],
                        summary["cash_value"],
                        summary["portfolio_value"],
                        summary["franked_dividends"],
                        summary["unfranked_dividends"],
                        summary["franking_credits"],
                        summary["cash_interest"],
                        summary["total_income"],
                        summary["brokerage"],
                        summary["subscriptions"],
                        summary["other_fees"],
                        summary["total_fees"],
                    ),
                )
            connection.commit()
        return {
            "import_id": manifest["id"],
            "duplicate": False,
            "stored": True,
            "transactions": len(transactions),
            "summary_reconciled": summary is not None,
        }

    def portfolio_transactions(self) -> pd.DataFrame:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT source_row, code, company, trade_date, side, quantity,
                       unit_price, trade_value, brokerage, gst, total_value, cash_flow
                FROM portfolio_transactions
                ORDER BY trade_date, source_row
                """
            ).fetchall()
        return pd.DataFrame(self._rows(rows))

    def portfolio_summary(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            summary = connection.execute(
                """
                SELECT statement_end, shares_value, cash_value, portfolio_value,
                       franked_dividends, unfranked_dividends, franking_credits,
                       cash_interest, total_income, brokerage, subscriptions,
                       other_fees, total_fees
                FROM portfolio_summaries ORDER BY statement_end DESC LIMIT 1
                """
            ).fetchone()
            imports = connection.execute(
                """
                SELECT COUNT(*) AS import_count, SUM(row_count) AS transaction_count,
                       MIN(period_start) AS period_start, MAX(period_end) AS period_end,
                       SUM(brokerage_total) AS imported_brokerage,
                       SUM(gross_trade_value) AS gross_trade_value
                FROM portfolio_imports
                """
            ).fetchone()
        if not summary and not imports["import_count"]:
            return None
        return {
            "latest_eofy_summary": dict(summary) if summary else None,
            "imports": dict(imports),
            "privacy": "account identifiers and contract-note numbers are not stored",
        }

    def portfolio_symbol_summary(self, code: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT code, COUNT(*) AS transaction_count,
                       SUM(CASE WHEN side = 'buy' THEN 1 ELSE 0 END) AS buys,
                       SUM(CASE WHEN side = 'sell' THEN 1 ELSE 0 END) AS sells,
                       SUM(CASE WHEN side = 'buy' THEN quantity ELSE -quantity END) AS net_quantity,
                       SUM(trade_value) AS gross_trade_value,
                       SUM(brokerage) AS brokerage,
                       MIN(trade_date) AS first_trade_date,
                       MAX(trade_date) AS last_trade_date
                FROM portfolio_transactions WHERE code = ? GROUP BY code
                """,
                (code,),
            ).fetchone()
        return dict(row) if row else None

    def _enqueue_job(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        job_id = str(uuid.uuid4())
        requested_at = utc_now().isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO research_jobs(id, kind, status, requested_at, request_json)
                VALUES (?, ?, 'pending', ?, ?)
                """,
                (job_id, kind, requested_at, json.dumps(payload, sort_keys=True)),
            )
            connection.commit()
        return {"id": job_id, "kind": kind, "status": "pending", "requested_at": requested_at}

    def claim_job(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM research_jobs
                WHERE status = 'pending'
                ORDER BY requested_at LIMIT 1
                """
            ).fetchone()
            if not row:
                connection.rollback()
                return None
            started_at = utc_now().isoformat()
            connection.execute(
                "UPDATE research_jobs SET status = 'running', started_at = ? WHERE id = ?",
                (started_at, row["id"]),
            )
            connection.commit()
        job = dict(row)
        job["status"] = "running"
        job["started_at"] = started_at
        job["request"] = json.loads(job.pop("request_json"))
        return job

    def complete_job(
        self,
        job_id: str,
        result: dict[str, Any],
        dataset_sha256: str,
        engine_version: str,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE research_jobs
                SET status = 'completed', completed_at = ?, result_json = ?,
                    dataset_sha256 = ?, engine_version = ?, error = NULL
                WHERE id = ?
                """,
                (
                    utc_now().isoformat(),
                    json.dumps(result, sort_keys=True),
                    dataset_sha256,
                    engine_version,
                    job_id,
                ),
            )
            connection.commit()

    def fail_job(self, job_id: str, error: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE research_jobs
                SET status = 'failed', completed_at = ?, error = ?
                WHERE id = ?
                """,
                (utc_now().isoformat(), error[:2_000], job_id),
            )
            connection.commit()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM research_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["request"] = json.loads(result.pop("request_json"))
        if result["result_json"]:
            result["result"] = json.loads(result.pop("result_json"))
        else:
            result.pop("result_json")
        return result
