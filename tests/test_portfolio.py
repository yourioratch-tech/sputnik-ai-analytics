from pathlib import Path

import pandas as pd
import pytest

from sputnik.portfolio import fifo_analytics, parse_westpac_transactions
from sputnik.storage import MarketStore


def test_westpac_preamble_parser_and_private_storage(tmp_path: Path):
    source = tmp_path / "transactions.csv"
    source.write_text(
        "Transaction Summary\n"
        "01 Jul 2025 - 30 Jun 2026\n"
        "SHARES - 12345678 - HIN X1234567890\n"
        "PRIVATE NAME\n\n"
        "Code,Company,Date,Type,Quantity,Unit Price ($),Trade Value ($),"
        "Brokerage+GST ($),GST ($),Contract Note,Total Value ($)\n"
        '"OOO","BETASHARES OIL","01/06/2026","Buy","100","8.0000",'
        '"800.0000","10.0000","0.9100","999999999","810.0000"\n'
        '"OOO","BETASHARES OIL","05/06/2026","Sell","-100","8.5000",'
        '"-850.0000","10.0000","0.9100","999999998","-840.0000"\n'
        "GLOSSARY,,,,,,,,,,\n",
        encoding="utf-8",
    )
    transactions, manifest = parse_westpac_transactions(source)
    assert len(transactions) == 2
    assert manifest["brokerage_total"] == 20
    assert all("contract" not in key for key in transactions[0])

    store = MarketStore(tmp_path / "portfolio.sqlite3")
    summary = {
        "id": "summary-hash",
        "source_kind": "westpac_eofy_summary",
        "statement_end": "2026-06-30",
        "shares_value": 0,
        "cash_value": 0,
        "portfolio_value": 0,
        "franked_dividends": 0,
        "unfranked_dividends": 0,
        "franking_credits": 0,
        "cash_interest": 0,
        "total_income": 0,
        "brokerage": 20,
        "subscriptions": 0,
        "other_fees": 0,
        "total_fees": 20,
    }
    result = store.import_portfolio(transactions, manifest, summary)
    assert result["summary_reconciled"] is True
    assert store.portfolio_summary()["imports"]["transaction_count"] == 2

    fifo = fifo_analytics(pd.DataFrame(transactions))
    assert fifo["round_trip_count"] == 1
    assert fifo["realized_pnl_after_brokerage"] == pytest.approx(30)
