# Security policy

## Supported version

Only the latest `main` branch and latest tagged release receive security fixes.

## Report a vulnerability

Use a private GitHub Security Advisory after the private remote is created. Do
not open a public issue containing secrets, live URLs, payloads, or exploit
details.

## Secret boundaries

Use six independent random values:

- `SPUTNIK_TV_WEBHOOK_ID`: unguessable URL path component.
- `SPUTNIK_TV_SHARED_SECRET`: TradingView JSON payload secret.
- `SPUTNIK_NEWS_WEBHOOK_ID`: unguessable news-ingestion path component.
- `SPUTNIK_NEWS_SHARED_SECRET`: approved collector JSON payload secret.
- `SPUTNIK_API_KEY`: read-only ChatGPT Action bearer token.
- `SPUTNIK_ADMIN_KEY`: research-job creation bearer token.

Never commit `.env`, database files, WAL/SHM files, tunnel tokens, alert exports,
private CSV data, or sensitive reports. The service strips the TradingView body
secret before storage.

## Deployment requirements

- HTTPS is mandatory outside localhost.
- Bind the origin to localhost/private Docker networking.
- Put edge rate limiting and access logging in front of the webhook.
- Keep the database volume private and encrypted at rest where available.
- Back up through SQLite's online backup mechanism or a coordinated shutdown.
- Keep host, container base, Python, and dependencies patched.
- Review GitHub secret scanning and Dependabot alerts.

## If a secret is exposed

1. Disable the affected Action, alert route, or tunnel.
2. Rotate the exposed value; do not merely delete it from the latest commit.
3. If committed, purge it from history and rotate it again.
4. Review tunnel/API logs and stored event hashes for abuse.
5. Restore service with distinct credentials and verify invalid old values
   return 401.

## Explicit non-capabilities

This repository must not gain broker authentication, account mutation, order
placement, order cancellation, or credential/session extraction surfaces.
