# Product specification distilled from the supplied session

The supplied conversation is requirements evidence, not a verified market-data
source. Its old prices, probabilities, news claims, and position suggestions
must not seed live state or be treated as confirmed facts.

"Learning" in this system means durable private evidence, reproducible feature
and backtest computation, forecast/outcome reconciliation, and versioned model
changes. Reading files or a GitHub repository does not alter ChatGPT model
weights. ChatGPT recalls current facts by querying the API at request time.

## Research question the system must support

For a documented ASX universe, estimate what historically happened from Friday
close to the next exchange-session open when a declared cross-market regime was
present. Example factors include oil, DXY, Treasury yields, gold, silver,
copper, volatility, equity futures, and explicit geopolitical-event labels.

The system should support horizons of 1, 3, 5, and 20 sessions later, but the
initial implemented engine is Friday close to next-session open. Adding other
horizons is an extension, not something the current result should imply.

## Required engines

1. **Evidence ledger** — immutable completed bars, alerts, source, timeframe,
   receive time, market time, hashes, freshness, and missing-data status.
2. **Macro/regime engine** — explicit rules over time-aligned cross-market
   features; no prose-only regime labels.
3. **Statistical engine** — baselines, conditional samples, costs, tails,
   uncertainty intervals, fingerprints, and sample-size gates.
4. **Strategy engine** — completed-bar signals, next-bar execution convention,
   conservative intrabar assumptions, benchmark, and drawdown reporting.
5. **Reasoning surface** — read-only ChatGPT context containing observed facts,
   historical results, model-derived conclusions, limitations, and unknowns.

## Non-negotiable boundaries

- No broker connection, credential inspection, orders, or account controls.
- No entire-ASX claim without an explicit point-in-time universe and complete
  data coverage report.
- No probability without a denominator, date range, horizon, method, and
  uncertainty.
- No backtest on isolated price alerts masquerading as complete bar history.
- No rewriting a stored completed bar without a separately audited correction
  process.
- No GitHub repository, language model, or dashboard is itself a live data feed.

## Future milestones

- Point-in-time ASX universe and delisting/corporate-action adapter.
- Calendar-aware ASX session labelling and opening-auction spread estimates.
- Purged walk-forward model evaluation and calibration ledger.
- Multi-horizon event studies and factor-neutral benchmarks.
- Licensed-source reconciliation against TradingView webhook observations.
- Human-reviewed regime/event taxonomy with precise start/end timestamps.
