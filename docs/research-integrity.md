# Research integrity contract

## Evidence classes

| Class | Meaning |
|---|---|
| `tradingview_webhook_observation` | Payload received from the configured TradingView alert over HTTPS |
| `historical` | Stored completed bars used after the outcome existed |
| `model_derived` | Feature, score, frequency estimate, interval, or ranking computed by code |
| `unconfirmed` | Missing, stale, incomplete, conflicting, or not independently reconciled |

Webhook provenance is auditable but not independent exchange verification.

## Before a backtest is interpreted

Record the universe, bar timezone, exchange calendar, completed-bar rule,
features available at each cutoff, target, horizon, overlap, costs, spread,
slippage, benchmark, train/validation/test boundaries, parameters, and rejection
criteria. Preserve the dataset fingerprint and exact code version.

## Leakage controls

- Rolling breakouts exclude the current bar.
- Signals execute no earlier than the next bar open.
- Macro observations join backward only.
- Fitted transforms belong inside each chronological training fold.
- Overlapping targets require purging/embargo or dependence-aware inference.
- The final test period remains untouched until the full pipeline is frozen.

## Weekend-gap interpretation

`historical_positive_rate` is an empirical frequency in the stored sample. It
is not a calibrated forecast for the coming Monday. The Wilson interval shows
sampling uncertainty only; it does not capture regime misclassification,
missing bars, geopolitical uniqueness, execution spread, or structural change.

The `research_gate` is `insufficient_sample` below the predeclared minimum.
Passing the sample gate still does not prove economic value.

## Full-ASX claims

A complete scan needs a point-in-time universe, delisted securities, suspended
names, corporate actions, liquidity/spread screens, survivorship policy, and
licensed histories. A current constituent list backfilled into history is not a
valid all-ASX study.

## Acceptance ladder

1. Data integrity and completeness pass.
2. Cash/naive/buy-and-hold benchmark is explicit.
3. Net value survives conservative costs.
4. Results are stable across nearby parameters and chronological folds.
5. No single regime or handful of observations dominates.
6. Final test and paper-observation results remain consistent.

Backtests never authorize execution.
