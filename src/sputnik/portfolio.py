from __future__ import annotations

import csv
import hashlib
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from pypdf import PdfReader

TRANSACTION_HEADER = (
    "Code",
    "Company",
    "Date",
    "Type",
    "Quantity",
    "Unit Price ($)",
    "Trade Value ($)",
    "Brokerage+GST ($)",
    "GST ($)",
    "Contract Note",
    "Total Value ($)",
)


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_westpac_transactions(path: str | Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source = Path(path)
    source_hash = file_sha256(source)
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    header_index = next(
        (
            index
            for index, row in enumerate(rows)
            if tuple(row[: len(TRANSACTION_HEADER)]) == TRANSACTION_HEADER
        ),
        None,
    )
    if header_index is None:
        raise ValueError("Westpac transaction header was not found")

    transactions: list[dict[str, Any]] = []
    for source_row, row in enumerate(rows[header_index + 1 :], start=header_index + 2):
        if len(row) < len(TRANSACTION_HEADER):
            continue
        record = dict(zip(TRANSACTION_HEADER, row, strict=False))
        side = record["Type"].strip().lower()
        if side not in {"buy", "sell"}:
            continue
        code = record["Code"].strip().upper()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9.]{0,15}", code):
            raise ValueError(f"invalid ASX code on source row {source_row}")
        trade_date = datetime.strptime(record["Date"].strip(), "%d/%m/%Y").date()
        quantity_raw = float(record["Quantity"])
        trade_value_raw = float(record["Trade Value ($)"])
        total_value_raw = float(record["Total Value ($)"])
        if (side == "buy" and quantity_raw <= 0) or (side == "sell" and quantity_raw >= 0):
            raise ValueError(f"quantity sign contradicts side on source row {source_row}")
        transaction_id = hashlib.sha256(
            f"{source_hash}:{source_row}".encode()
        ).hexdigest()
        transactions.append(
            {
                "id": transaction_id,
                "source_row": source_row,
                "code": code,
                "company": record["Company"].strip(),
                "trade_date": trade_date.isoformat(),
                "side": side,
                "quantity": abs(quantity_raw),
                "unit_price": float(record["Unit Price ($)"]),
                "trade_value": abs(trade_value_raw),
                "brokerage": float(record["Brokerage+GST ($)"]),
                "gst": float(record["GST ($)"]),
                "total_value": abs(total_value_raw),
                "cash_flow": abs(total_value_raw) * (1 if side == "sell" else -1),
            }
        )
    if not transactions:
        raise ValueError("no Buy/Sell transactions found")
    frame = pd.DataFrame(transactions)
    manifest = {
        "id": source_hash,
        "source_kind": "westpac_eofy_transactions",
        "row_count": len(frame),
        "period_start": frame["trade_date"].min(),
        "period_end": frame["trade_date"].max(),
        "symbol_count": int(frame["code"].nunique()),
        "brokerage_total": float(frame["brokerage"].sum()),
        "gross_trade_value": float(frame["trade_value"].sum()),
    }
    return transactions, manifest


def _money_after(text: str, label: str) -> float:
    flexible_label = r"\s+".join(re.escape(part) for part in label.split())
    pattern = flexible_label + r"\s*\$(-?[0-9,]+(?:\.[0-9]{2})?)"
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        raise ValueError(f"summary value not found: {label}")
    return float(match.group(1).replace(",", ""))


def parse_westpac_summary(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    text = "\n".join(page.extract_text() or "" for page in PdfReader(source).pages)
    date_match = re.search(r"Value as at (\d{1,2} [A-Za-z]+ \d{4})", text)
    if not date_match:
        raise ValueError("summary valuation date was not found")
    statement_end = datetime.strptime(date_match.group(1), "%d %B %Y").date().isoformat()
    shares_match = re.search(
        r"Portfolio Valuation.*?Shares\s+\d+\s+[^\n]+\s+\$(-?[0-9,]+(?:\.[0-9]{2})?)",
        text,
        flags=re.DOTALL,
    )
    cash_match = re.search(
        r"CASH INVESTMENT\s+ACCOUNT\s+\d+\s+[^\n]+\s+\$(-?[0-9,]+(?:\.[0-9]{2})?)",
        text,
    )
    if not shares_match or not cash_match:
        raise ValueError("portfolio valuation rows were not found")
    summary = {
        "id": file_sha256(source),
        "source_kind": "westpac_eofy_summary",
        "statement_end": statement_end,
        "shares_value": float(shares_match.group(1).replace(",", "")),
        "cash_value": float(cash_match.group(1).replace(",", "")),
        "portfolio_value": _money_after(text, "TOTAL PORTFOLIO VALUE"),
        "franked_dividends": _money_after(text, "Est. Franked Dividends"),
        "unfranked_dividends": _money_after(text, "Est. Unfranked Dividends"),
        "franking_credits": _money_after(text, "Est. Franking Credits"),
        "cash_interest": _money_after(text, "CASH INVESTMENT ACCOUNT \nInterest"),
        "total_income": _money_after(text, "TOTAL INCOME"),
        "brokerage": _money_after(text, "Total Brokerage (inc. GST)"),
        "subscriptions": _money_after(text, "Total Subscriptions (inc. GST)"),
        "other_fees": _money_after(text, "Other Fees (inc. GST)"),
        "total_fees": _money_after(text, "TOTAL FEES & CHARGES"),
    }
    return summary


@dataclass
class Lot:
    date: str
    quantity: float
    unit_cost: float


def fifo_analytics(transactions: pd.DataFrame) -> dict[str, Any]:
    if transactions.empty:
        return {"matched_quantity": 0, "unmatched_sell_quantity": 0, "round_trips": []}
    data = transactions.sort_values(["trade_date", "source_row"]).copy()
    lots: dict[str, deque[Lot]] = {}
    round_trips: list[dict[str, Any]] = []
    unmatched = 0.0
    for row in data.itertuples(index=False):
        queue = lots.setdefault(row.code, deque())
        fee_per_unit = row.brokerage / row.quantity if row.quantity else 0.0
        if row.side == "buy":
            queue.append(Lot(row.trade_date, row.quantity, row.unit_price + fee_per_unit))
            continue
        remaining = row.quantity
        sell_unit_net = row.unit_price - fee_per_unit
        while remaining > 1e-9 and queue:
            lot = queue[0]
            matched = min(remaining, lot.quantity)
            pnl = matched * (sell_unit_net - lot.unit_cost)
            days = (
                datetime.fromisoformat(row.trade_date) - datetime.fromisoformat(lot.date)
            ).days
            round_trips.append(
                {
                    "code": row.code,
                    "entry_date": lot.date,
                    "exit_date": row.trade_date,
                    "quantity": matched,
                    "holding_days": days,
                    "realized_pnl_after_brokerage": pnl,
                    "return_after_brokerage": pnl / (matched * lot.unit_cost),
                }
            )
            lot.quantity -= matched
            remaining -= matched
            if lot.quantity <= 1e-9:
                queue.popleft()
        unmatched += remaining
    trips = pd.DataFrame(round_trips)
    return {
        "matched_quantity": float(trips["quantity"].sum()) if not trips.empty else 0.0,
        "unmatched_sell_quantity": unmatched,
        "round_trip_count": len(trips),
        "win_rate": float((trips["realized_pnl_after_brokerage"] > 0).mean())
        if not trips.empty
        else None,
        "realized_pnl_after_brokerage": float(trips["realized_pnl_after_brokerage"].sum())
        if not trips.empty
        else 0.0,
        "median_holding_days": float(trips["holding_days"].median())
        if not trips.empty
        else None,
        "round_trips": round_trips,
        "limitations": [
            "FIFO uses only transactions in the supplied export; opening lots may be absent.",
            "Trade dates contain no execution timestamp, so same-day ordering follows source rows.",
            "Corporate actions, transfers, dividends, tax parcels, and wash-sale rules are not inferred.",
        ],
    }


def review_transactions_against_bars(
    transactions: pd.DataFrame,
    bars_by_code: dict[str, pd.DataFrame],
    horizons: list[int],
) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    unmatched = 0
    for row in transactions.itertuples(index=False):
        bars = bars_by_code.get(row.code)
        if bars is None or bars.empty:
            unmatched += 1
            continue
        market = bars.copy()
        market["date"] = pd.to_datetime(market["date"], utc=True)
        market = market.sort_values("date").reset_index(drop=True)
        trade_date = pd.Timestamp(row.trade_date, tz="UTC")
        base_index = int(market["date"].searchsorted(trade_date, side="right") - 1)
        if base_index < 0:
            unmatched += 1
            continue
        matched_any = False
        direction = 1 if row.side == "buy" else -1
        for horizon in horizons:
            future_index = base_index + horizon
            if future_index >= len(market):
                continue
            future_close = float(market.iloc[future_index]["close"])
            raw_return = future_close / row.unit_price - 1
            observations.append(
                {
                    "code": row.code,
                    "trade_date": row.trade_date,
                    "side": row.side,
                    "horizon_bars": horizon,
                    "signed_forward_return": direction * raw_return,
                    "market_date": market.iloc[future_index]["date"].isoformat(),
                }
            )
            matched_any = True
        if not matched_any:
            unmatched += 1
    frame = pd.DataFrame(observations)
    metrics: list[dict[str, Any]] = []
    if not frame.empty:
        for (horizon, side), group in frame.groupby(["horizon_bars", "side"]):
            returns = group["signed_forward_return"]
            metrics.append(
                {
                    "horizon_bars": int(horizon),
                    "side": side,
                    "samples": len(group),
                    "directional_success_rate": float((returns > 0).mean()),
                    "mean_signed_forward_return": float(returns.mean()),
                    "median_signed_forward_return": float(returns.median()),
                }
            )
    return {
        "transactions": int(len(transactions)),
        "unmatched_transactions": unmatched,
        "matched_observations": len(frame),
        "metrics": metrics,
        "provenance": "private transaction ledger joined to historical webhook bars",
        "limitations": [
            "The broker export provides trade dates but not execution timestamps.",
            "Daily-bar matching cannot determine whether intraday signals existed before each fill.",
            "Signed forward return measures timing alignment, not causal strategy skill.",
            "Missing or stale market-bar history reduces coverage and gates interpretation.",
        ],
        "execution_boundary": "RETROSPECTIVE RESEARCH ONLY - NO BROKER - NO ORDERS",
    }
