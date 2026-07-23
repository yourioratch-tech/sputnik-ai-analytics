# Deployment

## Live Cloudflare evidence edge

The production ingress and read-only evidence API are deployed at:

```text
https://sputnik-market-edge.youri-oratch.workers.dev
```

TradingView and approved news collectors write to Cloudflare D1 through the
Worker in `edge/`. The Worker validates two independent webhook secrets,
enforces completed bars, strips payload secrets, and exposes authenticated
latest/history/context reads. It never accepts broker or order operations.

From `edge/`, an authenticated Cloudflare operator can reproduce the deployment:

```bash
npm ci
npm run typecheck
npm test
npx wrangler d1 migrations apply sputnik-market-edge --remote
npx wrangler secret put TV_WEBHOOK_ID
npx wrangler secret put TV_SHARED_SECRET
npx wrangler secret put NEWS_WEBHOOK_ID
npx wrangler secret put NEWS_SHARED_SECRET
npx wrangler secret put API_KEY
npx wrangler secret put WORK_KEY
npx wrangler deploy
```

Do not place secret values in `wrangler.jsonc`, GitHub, TradingView alert names,
logs, or screenshots. The currently generated values are stored in macOS
Keychain under the account `sputnik-ai-analytics` and service names documented
in `docs/tradingview.md`.

The GitHub deployment workflow is disabled on ordinary pushes until repository
variable `CLOUDFLARE_DEPLOY_ENABLED=true` and repository secrets
`CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` are configured. Manual runs
also require those two repository secrets. Webhook secrets remain managed with
`wrangler secret put`; they are not GitHub secrets.

The edge can proxy bounded compute routes to a private Python origin when
`ORIGIN_URL`, `ORIGIN_API_KEY`, and `ORIGIN_ADMIN_KEY` are configured as
Worker settings. Until that origin is online, live price/news reads work and
research-job or private-portfolio routes deliberately return HTTP 503.

Validated market/news events are also placed in a D1 outbox. When the private
origin is configured, the Worker retries delivery immediately and every five
minutes so the Python bar ledger can run backtests on the same completed-bar feed.

## Recommended local-first topology

Run the API and worker on the Mac with Docker Compose. Bind port 8765 to
`127.0.0.1`, not the LAN or public internet. Put a named Cloudflare Tunnel in
front of the API and restrict the hostname to this service.

GitHub stores code only. The named Docker volume stores the live SQLite ledger;
back it up separately and never commit it.

## Start

```bash
cp .env.example .env
# Fill all seven independent secrets and the stable public URL.
docker compose up --build -d api worker
docker compose ps
curl http://127.0.0.1:8765/health
```

For an existing named tunnel, set `CLOUDFLARE_TUNNEL_TOKEN` and route the tunnel
hostname to `http://api:8765`, then:

```bash
docker compose --profile tunnel up -d tunnel
```

Do not use a temporary tunnel URL for a long-lived TradingView alert or Custom
GPT Action. Do not expose the Docker socket or database file to the tunnel.

## Operational checks

- `/health` is reachable locally and reports `ready: true`.
- Invalid path IDs and body secrets return 401.
- Reader and admin keys cannot substitute for one another.
- The latest known symbol shows the expected market time and freshness.
- Worker jobs move `pending → running → completed/failed`.
- Disk capacity, backups, clock synchronization, and certificate/tunnel health
  are monitored.

## Backups

Use a SQLite online backup or stop both services before copying the database.
Keep encrypted backups outside the Git repository. Test restoration on a
separate volume.

## Secret rotation

1. Rotate the read-only key and update the Custom GPT Action.
2. Rotate webhook ID and payload secret together, update TradingView alerts,
   then remove the old route.
3. Rotate the admin key and restart the API/worker.
4. Review access logs and invalidate the tunnel token if exposure is suspected.
