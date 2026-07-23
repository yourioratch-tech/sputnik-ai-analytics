# TradingView completed-bar feed

## Why a dedicated bar stream is necessary

An alert that fires only on a trading setup supplies sparse event points. It
cannot reconstruct all intervening OHLCV bars, so it cannot support a defensible
backtest. Use the included Pine script on every symbol/timeframe intended for
research and keep setup alerts separate.

## Alert creation

1. Add `tradingview/sputnik_bar_stream.pine` to a chart.
2. Set the payload secret to `SPUTNIK_TV_SHARED_SECRET`.
3. Select the intended symbol and timeframe visibly.
4. Create a dedicated alert for the script's `alert()` calls.
5. Use `Once Per Bar Close`.
6. Set the webhook URL to the HTTPS endpoint containing
   `SPUTNIK_TV_WEBHOOK_ID`.
7. Send a test bar and verify `/v1/context/{symbol}` with the read-only key.

The selected first-pass panel is stored in `configs/tradingview-alerts.yml`.
ASX setup alerts use five-minute completed bars; commodity and DXY driver alerts
use 60-minute bars. `ASX:SXY` is intentionally excluded because visible
TradingView search returned no current ASX match on 2026-07-23.

The use of `Any alert() function call` here is limited to a named bar-telemetry
instrument. It is not a generic trading signal and does not replace
ticker/timeframe-specific setup alerts.

## Completeness checks

- Confirm the chart timezone and the event timestamp are UTC-aware.
- Compare expected exchange sessions with stored timestamps.
- Investigate duplicates, gaps, suspended sessions, and early closes.
- Keep active/incomplete bars out of research.
- Independently reconcile critical data before capital decisions.

## Secret handling

TradingView does not support arbitrary webhook authorization headers. Sputnik
therefore requires both an unguessable URL path ID and a different secret in the
JSON body over HTTPS. The body secret is compared in constant time and removed
before the event is hashed or stored.
