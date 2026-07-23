export interface Env {
  DB: D1Database;
  TV_WEBHOOK_ID: string;
  TV_SHARED_SECRET: string;
  NEWS_WEBHOOK_ID: string;
  NEWS_SHARED_SECRET: string;
  API_KEY: string;
  WORK_KEY: string;
  ORIGIN_URL?: string;
  ORIGIN_API_KEY?: string;
  ORIGIN_ADMIN_KEY?: string;
  MAX_DATA_AGE_SECONDS: string;
  NEWS_ALLOWED_DOMAINS: string;
}

type Json = Record<string, unknown>;

const SYMBOL = /^[A-Z0-9][A-Z0-9:._!/-]{0,39}$/;
const TIMEFRAME = /^(?:[0-9]{1,4}[SMHDW]|[1-9][0-9]{0,3})$/i;
const MAX_BODY_BYTES = 64_000;

function response(payload: unknown, status = 200): Response {
  return Response.json(payload, {
    status,
    headers: {
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
      "X-Frame-Options": "DENY",
      "Referrer-Policy": "no-referrer",
    },
  });
}

export function secureEqual(left: string | null | undefined, right: string | undefined): boolean {
  if (!left || !right || left.length !== right.length) return false;
  let difference = 0;
  for (let index = 0; index < left.length; index += 1) {
    difference |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return difference === 0;
}

async function digest(payload: Json): Promise<string> {
  const canonical = JSON.stringify(
    Object.fromEntries(Object.entries(payload).sort(([a], [b]) => a.localeCompare(b))),
  );
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonical));
  return [...new Uint8Array(bytes)].map((value) => value.toString(16).padStart(2, "0")).join("");
}

function timestamp(value: unknown): string {
  const numeric = typeof value === "number" ? value : Number.NaN;
  const parsed = Number.isFinite(numeric)
    ? new Date(numeric > 10_000_000_000 ? numeric : numeric * 1000)
    : new Date(String(value));
  if (Number.isNaN(parsed.valueOf())) throw new Error("invalid timestamp");
  return parsed.toISOString();
}

function finite(value: unknown, name: string): number {
  const parsed = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(parsed)) throw new Error(`${name} must be finite`);
  return parsed;
}

export function validateMarketEvent(input: Json): Json {
  const event = { ...input };
  event.symbol = String(event.symbol ?? event.ticker ?? "").trim().toUpperCase();
  event.timeframe = String(event.timeframe ?? "").trim().toUpperCase();
  event.timestamp = timestamp(event.timestamp ?? event.time ?? event.bar_time);
  if (event.schema_version !== 1) throw new Error("schema_version must equal 1");
  if (!["bar", "price", "alert"].includes(String(event.kind))) throw new Error("invalid kind");
  if (!SYMBOL.test(String(event.symbol))) throw new Error("invalid symbol");
  if (!TIMEFRAME.test(String(event.timeframe))) throw new Error("invalid timeframe");
  if (event.kind === "bar") {
    if (event.confirmed !== true) throw new Error("only completed bars are accepted");
    const open = finite(event.open, "open");
    const high = finite(event.high, "high");
    const low = finite(event.low, "low");
    const close = finite(event.close, "close");
    const volume = finite(event.volume, "volume");
    if (Math.min(open, high, low, close) <= 0 || volume < 0) throw new Error("invalid OHLCV");
    if (high < Math.max(open, low, close) || low > Math.min(open, high, close)) {
      throw new Error("inconsistent OHLC");
    }
    Object.assign(event, { open, high, low, close, volume });
  }
  const point = event.price ?? event.close;
  if (event.kind === "price" && point === undefined) throw new Error("price is required");
  if (point !== undefined && finite(point, "price") <= 0) throw new Error("price must be positive");
  if (point !== undefined) event.price = finite(point, "price");
  return event;
}

async function body(request: Request): Promise<Json> {
  const declared = Number(request.headers.get("content-length") ?? 0);
  if (declared > MAX_BODY_BYTES) throw new Error("payload too large");
  const text = await request.text();
  if (!text || new TextEncoder().encode(text).byteLength > MAX_BODY_BYTES) {
    throw new Error("payload size is invalid");
  }
  const parsed: unknown = JSON.parse(text);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("JSON object required");
  }
  return parsed as Json;
}

function bearer(request: Request): string | undefined {
  const header = request.headers.get("authorization") ?? "";
  const [scheme, token] = header.split(" ", 2);
  return scheme?.toLowerCase() === "bearer" ? token : undefined;
}

function authorized(request: Request, env: Env): boolean {
  const token = bearer(request);
  return secureEqual(token, env.API_KEY) || secureEqual(token, env.WORK_KEY);
}

function workAuthorized(request: Request, env: Env): boolean {
  return secureEqual(bearer(request), env.WORK_KEY);
}

async function ingestMarket(request: Request, env: Env, pathId: string): Promise<Response> {
  if (!secureEqual(pathId, env.TV_WEBHOOK_ID)) return response({ error: "unauthorized" }, 401);
  try {
    const raw = await body(request);
    if (!secureEqual(String(raw.secret ?? ""), env.TV_SHARED_SECRET)) {
      return response({ error: "unauthorized" }, 401);
    }
    delete raw.secret;
    const event = validateMarketEvent(raw);
    const id = await digest(event);
    const duplicate = await env.DB.prepare("SELECT id FROM webhook_events WHERE id = ?")
      .bind(id).first();
    if (duplicate) return response({ status: "accepted", event_id: id, duplicate: true }, 202);
    if (event.kind === "bar") {
      const prior = await env.DB.prepare(
        "SELECT open, high, low, close, volume FROM market_bars WHERE symbol=? AND timeframe=? AND event_time=?",
      ).bind(event.symbol, event.timeframe, event.timestamp).first<Record<string, number>>();
      if (prior) {
        const keys = ["open", "high", "low", "close", "volume"] as const;
        if (keys.some((key) => prior[key] !== event[key])) {
          return response({ error: "completed bar conflicts with immutable history" }, 409);
        }
        return response({ status: "accepted", event_id: id, duplicate: true }, 202);
      }
    }
    const received = new Date().toISOString();
    const statements = [env.DB.prepare(
      "INSERT INTO webhook_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
    ).bind(id, received, event.kind, event.symbol, event.timeframe, event.timestamp,
      event.event ?? null, event.price ?? null, JSON.stringify(event))];
    if (event.price !== undefined) {
      statements.push(env.DB.prepare("INSERT INTO price_points VALUES (?, ?, ?, ?, ?, ?)")
        .bind(id, event.symbol, event.timeframe, event.timestamp, event.price, received));
    }
    if (event.kind === "bar") {
      statements.push(env.DB.prepare("INSERT INTO market_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
        .bind(event.symbol, event.timeframe, event.timestamp, event.open, event.high,
          event.low, event.close, event.volume, received, id));
    }
    statements.push(env.DB.prepare(`INSERT OR IGNORE INTO origin_sync_outbox
      (id,kind,payload_json,created_at) VALUES (?,'market',?,?)`)
      .bind(id, JSON.stringify(event), received));
    await env.DB.batch(statements);
    return response({ status: "accepted", event_id: id, stored: true }, 202);
  } catch (error) {
    return response({ error: error instanceof Error ? error.message : "invalid payload" }, 422);
  }
}

async function ingestNews(request: Request, env: Env, pathId: string): Promise<Response> {
  if (!secureEqual(pathId, env.NEWS_WEBHOOK_ID)) return response({ error: "unauthorized" }, 401);
  try {
    const raw = await body(request);
    if (!secureEqual(String(raw.secret ?? ""), env.NEWS_SHARED_SECRET)) {
      return response({ error: "unauthorized" }, 401);
    }
    delete raw.secret;
    const url = new URL(String(raw.url));
    if (url.protocol !== "https:") throw new Error("news URL must use HTTPS");
    const allowed = env.NEWS_ALLOWED_DOMAINS.split(",").map((value) => value.trim().toLowerCase()).filter(Boolean);
    if (allowed.length && !allowed.some((domain) => url.hostname === domain || url.hostname.endsWith(`.${domain}`))) {
      throw new Error("news source domain is not approved");
    }
    const published = timestamp(raw.published_at);
    const symbols = Array.isArray(raw.symbols)
      ? [...new Set(raw.symbols.map((value) => String(value).trim().toUpperCase()))]
      : [];
    if (symbols.length > 100 || symbols.some((value) => !SYMBOL.test(value))) throw new Error("invalid symbols");
    const item: Json = {
      schema_version: 1,
      source: String(raw.source ?? "").trim().slice(0, 120),
      title: String(raw.title ?? "").trim().slice(0, 500),
      url: url.toString(), published_at: published, symbols,
      summary: raw.summary ? String(raw.summary).slice(0, 2000) : null,
      category: raw.category ? String(raw.category).slice(0, 80) : null,
    };
    if (!item.source || !item.title) throw new Error("source and title are required");
    const id = await digest(item);
    const received = new Date().toISOString();
    const statements = [env.DB.prepare(
      "INSERT OR IGNORE INTO news_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
    ).bind(id, item.source, item.title, item.url, published, received,
      item.category, item.summary, JSON.stringify(item))];
    for (const symbol of symbols) {
      statements.push(env.DB.prepare("INSERT OR IGNORE INTO news_symbols VALUES (?, ?)").bind(id, symbol));
    }
    statements.push(env.DB.prepare(`INSERT OR IGNORE INTO origin_sync_outbox
      (id,kind,payload_json,created_at) VALUES (?,'news',?,?)`)
      .bind(id, JSON.stringify(item), received));
    const results = await env.DB.batch(statements);
    return response({ status: "accepted", news_id: id, stored: results[0]?.meta.changes === 1 }, 202);
  } catch (error) {
    return response({ error: error instanceof Error ? error.message : "invalid payload" }, 422);
  }
}

async function latestPrices(url: URL, env: Env): Promise<Response> {
  const requested = (url.searchParams.get("symbols") ?? "").split(",").map((v) => v.trim().toUpperCase()).filter(Boolean);
  if (requested.length > 100 || requested.some((value) => !SYMBOL.test(value))) return response({ error: "invalid symbols" }, 422);
  const timeframe = url.searchParams.get("timeframe")?.toUpperCase();
  if (timeframe && !TIMEFRAME.test(timeframe)) return response({ error: "invalid timeframe" }, 422);
  const clauses: string[] = [];
  const values: unknown[] = [];
  if (requested.length) { clauses.push(`symbol IN (${requested.map(() => "?").join(",")})`); values.push(...requested); }
  if (timeframe) { clauses.push("timeframe = ?"); values.push(timeframe); }
  const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";
  const result = await env.DB.prepare(`WITH selected AS (
      SELECT *,ROW_NUMBER() OVER(PARTITION BY symbol,timeframe ORDER BY event_time DESC,received_at DESC) row_number
      FROM price_points ${where}), latest_amendments AS (
      SELECT *,ROW_NUMBER() OVER(PARTITION BY symbol,timeframe,event_time ORDER BY created_at DESC) amendment_number
      FROM price_amendments)
    SELECT p.symbol,p.timeframe,p.event_time,
      COALESCE(json_extract(a.replacement_json,'$.price'),json_extract(a.replacement_json,'$.close'),p.price) price,
      p.received_at,a.id amendment_id,a.reason amendment_reason,a.created_at amendment_created_at
    FROM selected p LEFT JOIN latest_amendments a ON a.symbol=p.symbol AND a.timeframe=p.timeframe
      AND a.event_time=p.event_time AND a.amendment_number=1
    WHERE p.row_number=1 ORDER BY p.symbol,p.timeframe`).bind(...values).all();
  const maxAge = Number(env.MAX_DATA_AGE_SECONDS || 900);
  const prices = result.results.map((row) => {
    const age = Math.max(0, (Date.now() - new Date(String(row.event_time)).valueOf()) / 1000);
    return { ...row, age_seconds: age, freshness: age <= maxAge ? "fresh" : "stale", provenance: "tradingview_webhook_observation" };
  });
  return response({ as_of: new Date().toISOString(), count: prices.length, prices,
    warning: "Webhook observations are not independently verified exchange truth." });
}

async function latestNews(url: URL, env: Env): Promise<Response> {
  const requested = (url.searchParams.get("symbols") ?? "").split(",").map((v) => v.trim().toUpperCase()).filter(Boolean);
  const limit = Math.min(500, Math.max(1, Number(url.searchParams.get("limit") ?? 100)));
  if (requested.length > 100 || requested.some((value) => !SYMBOL.test(value))) return response({ error: "invalid symbols" }, 422);
  const where = requested.length ? `WHERE ns.symbol IN (${requested.map(() => "?").join(",")})` : "";
  const result = await env.DB.prepare(`SELECT n.id,n.source,n.title,n.url,n.published_at,n.received_at,n.category,n.summary,
    GROUP_CONCAT(DISTINCT alls.symbol) symbols FROM news_items n LEFT JOIN news_symbols ns ON ns.news_id=n.id
    LEFT JOIN news_symbols alls ON alls.news_id=n.id ${where} GROUP BY n.id ORDER BY n.published_at DESC LIMIT ?`)
    .bind(...requested, limit).all();
  const items = result.results.map((row) => ({ ...row, symbols: row.symbols ? String(row.symbols).split(",").sort() : [] }));
  return response({ as_of: new Date().toISOString(), count: items.length, items,
    provenance: "approved_news_webhook_metadata" });
}

async function priceHistory(url: URL, env: Env): Promise<Response> {
  const symbol = (url.searchParams.get("symbol") ?? "").trim().toUpperCase();
  const timeframe = url.searchParams.get("timeframe")?.trim().toUpperCase();
  const limit = Math.min(2000, Math.max(1, Number(url.searchParams.get("limit") ?? 500)));
  if (!SYMBOL.test(symbol) || (timeframe && !TIMEFRAME.test(timeframe))) {
    return response({ error: "invalid symbol or timeframe" }, 422);
  }
  const condition = timeframe ? "symbol=? AND timeframe=?" : "symbol=?";
  const values = timeframe ? [symbol, timeframe, limit] : [symbol, limit];
  const result = await env.DB.prepare(`WITH selected AS (
      SELECT symbol,timeframe,event_time,price,received_at FROM price_points WHERE ${condition}
      ORDER BY event_time DESC LIMIT ?), latest_amendments AS (
      SELECT *,ROW_NUMBER() OVER(PARTITION BY symbol,timeframe,event_time ORDER BY created_at DESC) amendment_number
      FROM price_amendments)
    SELECT p.symbol,p.timeframe,p.event_time,
      COALESCE(json_extract(a.replacement_json,'$.price'),json_extract(a.replacement_json,'$.close'),p.price) price,
      p.received_at,a.id amendment_id
    FROM selected p LEFT JOIN latest_amendments a ON a.symbol=p.symbol AND a.timeframe=p.timeframe
      AND a.event_time=p.event_time AND a.amendment_number=1
    ORDER BY p.event_time ASC`).bind(...values).all();
  return response({ symbol, timeframe: timeframe ?? null, count: result.results.length,
    points: result.results, provenance: "tradingview_webhook_observation" });
}

async function marketContext(symbolValue: string, env: Env): Promise<Response> {
  const symbol = decodeURIComponent(symbolValue).trim().toUpperCase();
  if (!SYMBOL.test(symbol)) return response({ error: "invalid symbol" }, 422);
  const prices = await env.DB.prepare(`WITH selected AS (
      SELECT *,ROW_NUMBER() OVER(PARTITION BY timeframe ORDER BY event_time DESC,received_at DESC) row_number
      FROM price_points WHERE symbol=?), latest_amendments AS (
      SELECT *,ROW_NUMBER() OVER(PARTITION BY symbol,timeframe,event_time ORDER BY created_at DESC) amendment_number
      FROM price_amendments)
    SELECT p.symbol,p.timeframe,p.event_time,
      COALESCE(json_extract(a.replacement_json,'$.price'),json_extract(a.replacement_json,'$.close'),p.price) price,
      p.received_at,a.id amendment_id,a.reason amendment_reason
    FROM selected p LEFT JOIN latest_amendments a ON a.symbol=p.symbol AND a.timeframe=p.timeframe
      AND a.event_time=p.event_time AND a.amendment_number=1
    WHERE p.row_number=1 ORDER BY p.timeframe`).bind(symbol).all();
  const events = await env.DB.prepare(`SELECT id,received_at,kind,symbol,timeframe,event_time,event_name,price,payload_json
    FROM webhook_events WHERE symbol=? ORDER BY event_time DESC,received_at DESC LIMIT 20`).bind(symbol).all();
  const news = await env.DB.prepare(`SELECT n.id,n.source,n.title,n.url,n.published_at,n.received_at,n.category,n.summary
    FROM news_items n JOIN news_symbols ns ON ns.news_id=n.id WHERE ns.symbol=?
    ORDER BY n.published_at DESC LIMIT 20`).bind(symbol).all();
  const maxAge = Number(env.MAX_DATA_AGE_SECONDS || 900);
  const latest = prices.results.map((row) => {
    const age = Math.max(0, (Date.now() - new Date(String(row.event_time)).valueOf()) / 1000);
    return { ...row, age_seconds: age, freshness: age <= maxAge ? "fresh" : "stale" };
  });
  return response({ symbol, as_of: new Date().toISOString(), latest_by_timeframe: latest,
    recent_events: events.results.map((row) => {
      const { payload_json, ...event } = row;
      return { ...event, details: JSON.parse(String(payload_json)) };
    }), recent_news: news.results,
    evidence_class: "observed_webhook",
    unknowns: ["No independent exchange reconciliation is implied.",
      "A price-point stream is not complete OHLCV unless every completed bar is delivered."],
    execution_boundary: "NO BROKER - NO ORDERS" });
}

async function webhookAnalytics(env: Env): Promise<Response> {
  const kinds = await env.DB.prepare(`SELECT kind,COUNT(*) event_count,COUNT(DISTINCT symbol) symbol_count,
    MAX(received_at) last_received_at FROM webhook_events GROUP BY kind ORDER BY kind`).all();
  const streams = await env.DB.prepare(`SELECT symbol,timeframe,COUNT(*) event_count,
    MIN(event_time) first_event_time,MAX(event_time) last_event_time,MAX(received_at) last_received_at
    FROM webhook_events GROUP BY symbol,timeframe ORDER BY symbol,timeframe`).all();
  const news = await env.DB.prepare(`SELECT COUNT(*) item_count,COUNT(DISTINCT source) source_count,
    MAX(received_at) last_received_at FROM news_items`).first();
  return response({ as_of: new Date().toISOString(), market_by_kind: kinds.results,
    market_streams: streams.results, news,
    meaning: "Counts and last-received times show what is stored; they do not prove complete exchange coverage." });
}

function cleanConfiguration(raw: Json): Json {
  const timezone = String(raw.timezone ?? "Australia/Sydney");
  const start = String(raw.session_start ?? "10:00");
  const end = String(raw.session_end ?? "16:15");
  const interval = Number(raw.scan_interval_minutes ?? 30);
  const minimumBars = Number(raw.minimum_bars ?? 60);
  const topN = Number(raw.top_n ?? 10);
  const maxAge = Number(raw.max_daily_bar_age_hours ?? 72);
  const universe = Array.isArray(raw.asx_universe)
    ? [...new Set(raw.asx_universe.map((value) => String(value).trim().toUpperCase()))]
    : [];
  if (!/^\d{2}:\d{2}$/.test(start) || !/^\d{2}:\d{2}$/.test(end)) throw new Error("invalid session time");
  if (!Number.isInteger(interval) || interval < 5 || interval > 240) throw new Error("invalid scan interval");
  if (!Number.isInteger(minimumBars) || minimumBars < 20 || minimumBars > 5000) throw new Error("invalid minimum bars");
  if (!Number.isInteger(topN) || topN < 1 || topN > 100) throw new Error("invalid top count");
  if (!Number.isInteger(maxAge) || maxAge < 1 || maxAge > 168) throw new Error("invalid maximum age");
  if (universe.length > 500 || universe.some((value) => !value.startsWith("ASX:") || !SYMBOL.test(value))) {
    throw new Error("ASX universe contains an invalid symbol");
  }
  const reason = String(raw.reason ?? "configuration update").trim().slice(0, 500);
  const actor = String(raw.actor ?? "chatgpt_work").trim().slice(0, 80);
  if (reason.length < 3 || !/^[a-zA-Z0-9_.:@/-]{1,80}$/.test(actor)) throw new Error("invalid reason or actor");
  return { enabled: raw.enabled !== false, timezone, session_start: start, session_end: end,
    scan_interval_minutes: interval, max_daily_bar_age_hours: maxAge, minimum_bars: minimumBars,
    top_n: topN, asx_universe: universe,
    strategy_backtests: Array.isArray(raw.strategy_backtests) ? raw.strategy_backtests.slice(0, 50) : [],
    reason, actor };
}

async function getConfiguration(env: Env): Promise<Response> {
  const row = await env.DB.prepare(`SELECT id,version,configuration_json,reason,actor,created_at
    FROM control_configuration_revisions ORDER BY version DESC LIMIT 1`).first<Record<string, unknown>>();
  if (!row) return response({ error: "configuration has not been set" }, 404);
  const { configuration_json, ...metadata } = row;
  return response({ ...metadata, configuration: JSON.parse(String(configuration_json)) });
}

async function putConfiguration(request: Request, env: Env): Promise<Response> {
  try {
    const configuration = cleanConfiguration(await body(request));
    const id = await digest(configuration);
    const current = await env.DB.prepare("SELECT COALESCE(MAX(version),0) version FROM control_configuration_revisions").first<{version: number}>();
    const version = Number(current?.version ?? 0) + 1;
    const created = new Date().toISOString();
    await env.DB.prepare(`INSERT INTO control_configuration_revisions
      (id,version,configuration_json,reason,actor,created_at) VALUES (?,?,?,?,?,?)`)
      .bind(id, version, JSON.stringify(configuration), configuration.reason, configuration.actor, created).run();
    return response({ id, version, created_at: created, configuration });
  } catch (error) {
    return response({ error: error instanceof Error ? error.message : "invalid configuration" }, 422);
  }
}

async function listAmendments(url: URL, env: Env): Promise<Response> {
  const limit = Math.min(500, Math.max(1, Number(url.searchParams.get("limit") ?? 100)));
  const result = await env.DB.prepare(`SELECT id,target_kind,symbol,timeframe,event_time,replacement_json,
    reason,actor,source_url,created_at FROM price_amendments ORDER BY created_at DESC LIMIT ?`).bind(limit).all();
  return response({ count: result.results.length, original_history_preserved: true,
    items: result.results.map((row) => {
      const { replacement_json, ...item } = row;
      return { ...item, replacement: JSON.parse(String(replacement_json)) };
    }) });
}

async function createAmendment(request: Request, env: Env): Promise<Response> {
  try {
    const raw = await body(request);
    const target = String(raw.target_kind ?? "");
    const symbol = String(raw.symbol ?? "").trim().toUpperCase();
    const timeframe = String(raw.timeframe ?? "").trim().toUpperCase();
    const eventTime = timestamp(raw.event_time);
    if (!["bar", "price"].includes(target) || !SYMBOL.test(symbol) || !TIMEFRAME.test(timeframe)) {
      throw new Error("invalid amendment target");
    }
    const replacement: Json = {};
    if (target === "price") {
      replacement.price = finite(raw.price, "price");
      if (Number(replacement.price) <= 0) throw new Error("price must be positive");
    } else {
      const open = finite(raw.open, "open"); const high = finite(raw.high, "high");
      const low = finite(raw.low, "low"); const close = finite(raw.close, "close");
      const volume = finite(raw.volume, "volume");
      if (Math.min(open, high, low, close) <= 0 || volume < 0 || high < Math.max(open, low, close)
        || low > Math.min(open, high, close)) throw new Error("invalid OHLCV");
      Object.assign(replacement, { open, high, low, close, volume });
    }
    const reason = String(raw.reason ?? "").trim().slice(0, 500);
    const actor = String(raw.actor ?? "chatgpt_work").trim().slice(0, 80);
    const sourceUrl = raw.source_url ? new URL(String(raw.source_url)) : null;
    if (reason.length < 3 || !/^[a-zA-Z0-9_.:@/-]{1,80}$/.test(actor)) throw new Error("invalid reason or actor");
    if (sourceUrl && sourceUrl.protocol !== "https:") throw new Error("source URL must use HTTPS");
    const table = target === "bar" ? "market_bars" : "price_points";
    const original = await env.DB.prepare(`SELECT 1 found FROM ${table} WHERE symbol=? AND timeframe=? AND event_time=? LIMIT 1`)
      .bind(symbol, timeframe, eventTime).first();
    if (!original) return response({ error: "amendment target does not exist in the immutable ledger" }, 404);
    const canonical: Json = { target_kind: target, symbol, timeframe, event_time: eventTime,
      replacement, reason, actor, source_url: sourceUrl?.toString() ?? null };
    const id = await digest(canonical); const created = new Date().toISOString();
    await env.DB.prepare(`INSERT OR IGNORE INTO price_amendments
      (id,target_kind,symbol,timeframe,event_time,replacement_json,reason,actor,source_url,created_at)
      VALUES (?,?,?,?,?,?,?,?,?,?)`).bind(id,target,symbol,timeframe,eventTime,JSON.stringify(replacement),
        reason,actor,sourceUrl?.toString() ?? null,created).run();
    return response({ id, status: "recorded", original_preserved: true, effective_replacement: replacement }, 201);
  } catch (error) {
    return response({ error: error instanceof Error ? error.message : "invalid amendment" }, 422);
  }
}

async function proxyOrigin(request: Request, env: Env): Promise<Response> {
  if (!env.ORIGIN_URL || !env.ORIGIN_API_KEY) return response({ error: "Python origin is not configured" }, 503);
  const incoming = new URL(request.url);
  const target = new URL(`${incoming.pathname}${incoming.search}`, env.ORIGIN_URL);
  const headers = new Headers(request.headers);
  headers.set("Authorization", `Bearer ${env.ORIGIN_ADMIN_KEY ?? env.ORIGIN_API_KEY}`);
  headers.delete("host");
  return fetch(target, { method: request.method, headers, body: request.method === "GET" ? undefined : request.body });
}

async function syncOriginOutbox(env: Env, limit = 25): Promise<void> {
  if (!env.ORIGIN_URL || !env.ORIGIN_ADMIN_KEY) return;
  const pending = await env.DB.prepare(`SELECT id,kind,payload_json FROM origin_sync_outbox
    WHERE synced_at IS NULL ORDER BY created_at LIMIT ?`).bind(limit).all();
  for (const row of pending.results) {
    try {
      const path = row.kind === "news" ? "/v1/internal/edge-sync/news" : "/v1/internal/edge-sync/market";
      const target = new URL(path, env.ORIGIN_URL);
      const result = await fetch(target, {
        method: "POST",
        headers: { "Authorization": `Bearer ${env.ORIGIN_ADMIN_KEY}`, "Content-Type": "application/json" },
        body: String(row.payload_json),
      });
      if (!result.ok) throw new Error(`origin returned ${result.status}`);
      await env.DB.prepare("UPDATE origin_sync_outbox SET synced_at=?,attempts=attempts+1,last_error=NULL WHERE id=?")
        .bind(new Date().toISOString(), row.id).run();
    } catch (error) {
      await env.DB.prepare("UPDATE origin_sync_outbox SET attempts=attempts+1,last_error=? WHERE id=?")
        .bind(String(error instanceof Error ? error.message : error).slice(0, 500), row.id).run();
    }
  }
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/health") return response({ status: "ok", system: "sputnik-market-edge", broker_connected: false, orders_enabled: false });
    const market = url.pathname.match(/^\/v1\/webhooks\/tradingview\/([^/]+)$/);
    if (request.method === "POST" && market?.[1]) {
      const result = await ingestMarket(request, env, market[1]);
      ctx.waitUntil(syncOriginOutbox(env));
      return result;
    }
    const news = url.pathname.match(/^\/v1\/webhooks\/news\/([^/]+)$/);
    if (request.method === "POST" && news?.[1]) {
      const result = await ingestNews(request, env, news[1]);
      ctx.waitUntil(syncOriginOutbox(env));
      return result;
    }
    if (!authorized(request, env)) return response({ error: "valid bearer token required" }, 401);
    if (request.method === "GET" && url.pathname === "/v1/prices/latest") return latestPrices(url, env);
    if (request.method === "GET" && url.pathname === "/v1/prices/history") return priceHistory(url, env);
    if (request.method === "GET" && url.pathname === "/v1/news/latest") return latestNews(url, env);
    if (request.method === "GET" && url.pathname === "/v1/analytics/webhooks") return webhookAnalytics(env);
    if (request.method === "GET" && url.pathname === "/v1/work/configuration") return getConfiguration(env);
    if (request.method === "GET" && url.pathname === "/v1/work/price-amendments") return listAmendments(url, env);
    if (request.method === "PUT" && url.pathname === "/v1/work/configuration") {
      if (!workAuthorized(request, env)) return response({ error: "valid work key required" }, 401);
      return putConfiguration(request, env);
    }
    if (request.method === "POST" && url.pathname === "/v1/work/price-amendments") {
      if (!workAuthorized(request, env)) return response({ error: "valid work key required" }, 401);
      return createAmendment(request, env);
    }
    const context = url.pathname.match(/^\/v1\/context\/(.+)$/);
    if (request.method === "GET" && context?.[1]) return marketContext(context[1], env);
    if (/^\/v1\/(work|research\/jobs|portfolio)\//.test(url.pathname)) return proxyOrigin(request, env);
    return response({ error: "not found" }, 404);
  },
  async scheduled(_controller: ScheduledController, env: Env): Promise<void> {
    await syncOriginOutbox(env, 100);
  },
} satisfies ExportedHandler<Env>;
