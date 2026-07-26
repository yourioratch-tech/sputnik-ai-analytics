from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import uvicorn

from .backtest import run_backtest
from .config import load_config
from .data import make_demo_market, merge_point_in_time, read_macro, read_prices
from .jobs import run_worker
from .news_collector import collect_news, load_news_config
from .portfolio import parse_westpac_summary, parse_westpac_transactions
from .report import write_report
from .settings import Settings
from .storage import MarketStore
from .sweep import parameter_sweep


def _dataset(prices: str, macro: str | None):
    return merge_point_in_time(read_prices(prices), read_macro(macro) if macro else None)


def _backtest(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    result = run_backtest(_dataset(args.prices, args.macro), config)
    payload = write_report(result, config, args.output)
    print(json.dumps(payload["metrics"], indent=2))
    print(f"Report: {Path(args.output).resolve() / 'index.html'}")


def _demo(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    prices, macro = make_demo_market(args.days, args.seed)
    frame = merge_point_in_time(prices, macro)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    prices.to_csv(output / "demo_ooo.csv", index=False)
    macro.to_csv(output / "demo_macro.csv", index=False)
    result = run_backtest(frame, config)
    payload = write_report(result, config, output)
    print("Synthetic data is a software demonstration, not market evidence.")
    print(json.dumps(payload["metrics"], indent=2))
    print(f"Dashboard: {output.resolve() / 'index.html'}")


def _sweep(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    result = parameter_sweep(_dataset(args.prices, args.macro), config)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(target, index=False)
    print(result.head(10).to_string(index=False))
    print(f"Sweep: {target.resolve()}")


def _serve(args: argparse.Namespace) -> None:
    uvicorn.run(
        "sputnik.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        proxy_headers=True,
    )


def _worker(args: argparse.Namespace) -> None:
    run_worker(
        Settings.from_env(),
        poll_seconds=args.poll_seconds,
        once=args.once,
        concurrency=args.concurrency,
    )


def _import_portfolio(args: argparse.Namespace) -> None:
    runtime = Settings.from_env()
    transactions, manifest = parse_westpac_transactions(args.transactions)
    summary = parse_westpac_summary(args.summary) if args.summary else None
    result = MarketStore(runtime.database_path).import_portfolio(
        transactions, manifest, summary
    )
    safe_result = {
        **result,
        "database": str(runtime.database_path),
        "privacy": "account identifiers and contract-note numbers were not stored",
    }
    print(json.dumps(safe_result, indent=2))


def _collect_news(args: argparse.Namespace) -> None:
    runtime = Settings.from_env()
    if not runtime.news_shared_secret:
        raise SystemExit("SPUTNIK_NEWS_SHARED_SECRET is required")
    config = load_news_config(args.config)
    store = MarketStore(runtime.database_path)
    while True:
        result = collect_news(config, store, runtime.news_shared_secret)
        print(json.dumps(result, sort_keys=True))
        if not args.watch:
            return
        time.sleep(args.poll_seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sputnik", description="СПУТНИК macro intelligence and OOO research"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    backtest = subparsers.add_parser("backtest", help="backtest OOO from CSV data")
    backtest.add_argument("--prices", required=True)
    backtest.add_argument("--macro")
    backtest.add_argument("--config", default="configs/ooo_daily.yml")
    backtest.add_argument("--output", default="reports/ooo")
    backtest.set_defaults(handler=_backtest)

    demo = subparsers.add_parser("demo", help="run a deterministic synthetic demonstration")
    demo.add_argument("--days", type=int, default=900)
    demo.add_argument("--seed", type=int, default=42)
    demo.add_argument("--config", default="configs/ooo_daily.yml")
    demo.add_argument("--output", default="reports/demo")
    demo.set_defaults(handler=_demo)

    sweep = subparsers.add_parser("sweep", help="run a bounded parameter sweep")
    sweep.add_argument("--prices", required=True)
    sweep.add_argument("--macro")
    sweep.add_argument("--config", default="configs/ooo_daily.yml")
    sweep.add_argument("--output", default="reports/sweep.csv")
    sweep.set_defaults(handler=_sweep)

    serve = subparsers.add_parser("serve", help="serve the authenticated market evidence API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(handler=_serve)

    worker = subparsers.add_parser("worker", help="process durable research jobs")
    worker.add_argument("--poll-seconds", type=float, default=5.0)
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--concurrency", type=int, choices=range(1, 5), default=4)
    worker.set_defaults(handler=_worker)

    news = subparsers.add_parser(
        "collect-news", help="collect allowlisted RSS/Atom news with source provenance"
    )
    news.add_argument("--config", default="configs/news-feeds.yml")
    news.add_argument("--watch", action="store_true")
    news.add_argument("--poll-seconds", type=int, default=900)
    news.set_defaults(handler=_collect_news)

    portfolio = subparsers.add_parser(
        "import-portfolio", help="privately import a Westpac EOFY transaction export"
    )
    portfolio.add_argument("--transactions", required=True)
    portfolio.add_argument("--summary")
    portfolio.set_defaults(handler=_import_portfolio)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.handler(args)
