# ChatGPT Work Action setup

## Prerequisites

- The API is running continuously behind a stable HTTPS hostname.
- `/health` returns `ready: true`.
- TradingView test events appear in the local database.
- `SPUTNIK_OPERATOR_KEY` is different from the price/news webhook, reader, and admin secrets.

## Configure the Action

1. Open the Custom GPT editor and add an Action.
2. Import `docs/openapi-chatgpt.yaml`.
3. Keep the deployed Cloudflare Worker server URL, or replace it with your stable hostname.
4. Choose API-key authentication, auth type `Bearer`.
5. Enter `SPUTNIK_OPERATOR_KEY` only in the Action secret field.
6. Test `getLatestPrices`, `getWebhookCoverage`, `getSputnikConfiguration`,
   and `wakeGraniteAgent`.

Do not paste the key into GPT instructions, a chat, source control, or the
OpenAPI document.

## Recommended GPT instructions

```text
Treat Sputnik API values as source-labelled observations, not independently
verified exchange truth. Always state symbol, timeframe, event time, freshness,
and missing evidence. Separate observed data from historical/model-derived
results. Never claim a sparse alert stream is complete OHLCV. Never place,
modify, or cancel orders; no broker tools exist. If data is stale or absent, say
exactly what is missing.
```

## Compute permission

The Action may enqueue bounded Python jobs, save versioned ASX-session settings,
and append sourced price corrections. Raw observations are never overwritten.
It cannot ingest webhook payloads, import private files, write GitHub, run shell
commands, or operate a broker. `wakeGraniteAgent` queues a bounded task for one
of four local LM Studio worker slots. Poll the returned job ID with
`getResearchJob`. This is how ChatGPT Work requests local compute without Codex
being part of the production path.
