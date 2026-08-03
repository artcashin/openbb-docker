# Live Grid: EODHD websockets → OpenBB Workspace

**Date:** 2026-08-03
**Status:** Approved

## Purpose

A new `live-grid` service that streams real-time EODHD prices into an OpenBB
Workspace `live_grid` widget: a mixed watchlist (US equities, crypto, forex)
whose symbols are typed directly into the widget in Workspace. All EODHD
access — websocket and REST — goes through the official `eodhd` SDK
(https://github.com/EodHistoricalData/EODHD-APIs-Python-Financial-Library),
pinned to the same GitHub commit tarball as the openbb-eodhd extension
(`f98e69d797140ca03f16fa7451046d84aab9643e`).

## Protocol contracts

### OpenBB Workspace (docs.openbb.co/workspace/developers/widget-types/live-grid)

- `GET /widgets.json` — widget registry. The live grid entry uses
  `"type": "live_grid"`, `"endpoint": "live_grid"`, `"wsEndpoint": "live_grid_ws"`,
  `"data.wsRowIdColumn": "symbol"`, and a `symbol` text param with
  `multiSelect: true`.
- `GET /live_grid?symbol=A,B,C` — returns the initial rows (array of dicts).
- `WS /live_grid_ws` — Workspace sends `{"params": {"symbol": "A,B,C"}}` as a
  JSON message on connect and again whenever the user changes params. The
  backend streams one JSON row dict per update; rows are matched to the table
  by `symbol`. Columns with `enableCellChangeWs: false` never update over ws.
- CORS must allow the Workspace origin (`https://pro.openbb.co`).

### EODHD (official SDK)

- `WebSocketClient(api_key, endpoint, symbols, store_data=True)` connects to
  `wss://ws.eodhistoricaldata.com/ws/{endpoint}?api_token=...` where endpoint ∈
  `us | us-quote | forex | crypto`, max 50 symbols per connection. Symbols are
  fixed at construction; changing the set means stop + rebuild.
- Tick fields: US trades and crypto send `s` (symbol), `p` (price), `q` (size),
  `t` (ms timestamp). Forex sends `s`, `a` (ask), `b` (bid), `t` — no `p`.
- `APIClient.get_live_stock_prices(ticker)` (REST `/api/real-time`) returns a
  delayed snapshot with `close`, `previousClose`, `change`, `change_p`,
  `volume` — used to seed initial rows and change baselines.

### SDK warts and required workarounds

1. `WebSocketClient.__init__` registers SIGINT/SIGTERM handlers. Snapshot the
   current handlers (`signal.getsignal`) before construction and restore them
   immediately after, so uvicorn/docker shutdown keeps working. Construct only
   on the main thread (uvicorn's event-loop thread).
2. The only consumption API is `store_data=True` + a grow-forever `data_list`
   of raw JSON strings. A drain task reads new entries and trims the consumed
   prefix (`del data_list[:n]` — safe under the GIL: producer appends at the
   tail, only the drain task deletes from the head).

## Architecture

New top-level directory in openbb-docker:

```
live-grid/
  app/
    main.py        FastAPI app: /widgets.json, GET /live_grid, WS /live_grid_ws, /health
    feeds.py       FeedManager: one SDK WebSocketClient per asset class
    quotes.py      QuoteTable: symbol → row dict; REST snapshot seeding
    classify.py    Symbol → feed routing
  widgets.json     Served verbatim by main.py
  tests/
  Dockerfile       python:3.12-slim + eodhd (pinned tarball) + fastapi + uvicorn
  pyproject.toml
```

### classify.py

`classify(symbol) -> "crypto" | "forex" | "us"`:

- contains `-` → crypto (e.g. `BTC-USD`)
- 6 chars and both 3-char halves are ISO-4217 currency codes → forex
  (e.g. `EURUSD`)
- otherwise → US equity

### quotes.py — QuoteTable

- `rows`: dict symbol → `{symbol, price, change, change_percent, bid, ask,
  last_size, volume, updated_at}`.
- `seed(symbols, client)`: REST snapshot per symbol via the SDK; stores
  `previousClose` per symbol for change math; returns initial rows.
- `apply_tick(feed, msg)`: updates price/bid/ask/last_size/updated_at and
  recomputes change/change_percent from the stored prev close (forex price =
  bid/ask midpoint). Returns the symbol so callers can mark it dirty.

### feeds.py — FeedManager

- Holds per-feed state: SDK client, subscribed symbol set, drain cursor.
- `register(conn_id, symbols)` / `unregister(conn_id)`: track each grid
  connection's classified symbol sets; recompute the per-feed union; if a
  feed's union changed, stop its client and rebuild with the new set
  (with the signal-handler save/restore dance). Empty union → no client.
- A single asyncio task drains every feed's `data_list` every ~200 ms,
  parses ticks, applies them to the QuoteTable, and adds the symbol to a
  global dirty set observed by all grid connections.
- Rebuilds are debounced (~1 s) so rapid param edits don't thrash EODHD
  connections.

### main.py

- `GET /widgets.json`: serves the file, CORS-enabled.
- `GET /live_grid?symbol=...`: classify, seed via REST snapshot, return rows.
  Missing/invalid `EODHD_API_KEY` → HTTP 500 with a clear message.
- `WS /live_grid_ws`: consumer task reads params messages and (re)registers
  the connection's symbols; producer task flushes the latest row for each
  dirty subscribed symbol every ~250 ms (coalescing — EODHD US trades can
  tick hundreds of times per second). Disconnect → unregister.
- `GET /health`: `{"status": "ok", "feeds": {...}}` with per-feed connection
  state.

## Widget columns

| field | notes |
| --- | --- |
| symbol | row id |
| price | `renderFn: showCellChange`, `colorValueKey: change` |
| change | |
| change_percent | `renderFn: greenRed` |
| bid / ask | populated for forex (and left null for trades feeds) |
| last_size | last trade size |
| volume | snapshot day volume, `enableCellChangeWs: false` |
| updated_at | HH:MM:SS of last tick |

Default param value: `AAPL,MSFT,TSLA,BTC-USD,ETH-USD,EURUSD`.

## Error handling

- No/invalid API key: GET returns 500 with remediation text; ws closes 1011.
- SDK reconnects on its own (exponential backoff, 5 attempts). If a feed gives
  up, its rows go stale; the app stays up and logs a warning. `/health`
  exposes the dead feed.
- Unknown symbols never tick (EODHD silently ignores them); the row keeps its
  snapshot values. Snapshot failures for individual symbols degrade to a row
  with nulls rather than failing the whole GET.
- Workspace disconnect mid-send: producer catches and unregisters.

## Deployment

- Compose service `openbb-live-grid` in `docker-compose.nas.yml`, built from
  `live-grid/Dockerfile`, listening on `127.0.0.1:6903` behind the existing
  tailscale sidecar (same pattern as `stores-mcp` on 6902).
- `EODHD_API_KEY` injected from `credentials.env`.
- Workspace connects to the tailnet HTTPS URL as a custom backend
  (tailscale serve publishes 6903, as with the other services).

## Testing

- Unit tests (pytest, mocked SDK — same MagicMock pattern as openbb-eodhd):
  classify routing; QuoteTable tick math including forex mid and missing prev
  close; FeedManager union/refcount/rebuild logic; ws params handling and
  producer coalescing via FastAPI TestClient.
- `scripts/smoke.py`: with a real key, connect to the crypto feed (24/7) for
  ~10 s and assert ticks arrive and rows update; optionally exercise us/forex
  during market hours.

## Out of scope

- Historical/candle columns, us-quote (equity bid/ask) feed, more than 50
  symbols per feed, authentication on the backend (tailnet-only), and
  persistence of ticks (ArcticDB/kdb ingestion stays separate).
