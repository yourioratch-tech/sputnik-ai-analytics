# OOO daily macro-confluence strategy

## Objective

Test whether ASX:OOO long trades improve when ETF trend and participation align
with oil momentum while USD and yield pressure remain contained.

## Score

| Component | Points | Pass condition |
|---|---:|---|
| Trend | 2.0 | close > fast EMA > slow EMA |
| Oil momentum | 2.0 | oil return over lookback exceeds threshold |
| DXY contained | 1.0 | DXY return does not exceed threshold |
| Yields contained | 1.0 | 10Y change does not exceed threshold |
| Breakout | 2.0 | close exceeds prior rolling high |
| Volume | 1.0 | volume / average volume exceeds threshold |

Entry requires the configured score and valid ATR. Exit occurs when the score
falls below its threshold, the ATR stop/target/trailing stop is touched, or the
maximum holding period is reached.

## Timing

All component values use the completed daily bar. An entry signal at close is
filled at the next available OOO open with adverse slippage. Exit signals are
also filled at the next open. Intraday stops use daily high/low after the open.

## Interpretation limits

The score is an explainable hypothesis, not a trained probability. Multiple
parameter trials increase overfitting risk. Rank results on out-of-sample data,
review regime stability, and retain a final untouched test period.
