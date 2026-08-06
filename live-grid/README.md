# live-grid

OpenBB Workspace `live_grid` backend streaming real-time **EODHD** prices —
US equities, crypto and forex in one watchlist. Companion code for
*Adventures in OpenBB, Ep. 9*.

- `GET /widgets.json` — the widget contract (`type: live_grid`,
  `wsEndpoint`, `wsRowIdColumn: symbol`).
- `GET /live_grid?symbol=A,B,C` — initial rows, seeded from REST snapshots
  (previous close is cached as the change baseline).
- `WS /live_grid_ws` — Workspace sends `{"params":{"symbol":"A,B"}}` on
  connect and on every param change; the backend streams one row dict per
  update, coalesced to ~4 flushes/second per connection.
- `GET /health` — per-feed connection state.

Symbols route by shape: a dash means crypto (`BTC-USD`), two ISO currency
codes mean forex (`EURUSD`), everything else is a US equity. Feed clients are
rebuilt (debounced ~1s) when the subscribed union changes; the SDK's
signal-handler and grow-forever-buffer quirks are handled in `app/feeds.py`.

## Keys

`EODHD_API_KEY` — the public demo websocket key in `.env.example` covers
exactly the default watchlist for testing. Real watchlists need a paid EODHD
key with websocket access (free-tier keys 403 on connect; the REST-only
`demo` token fails the SDK's key-format validation).

## Test

    pip install -e .[dev] && pytest          # all mocked, no key needed
    python scripts/smoke_live.py             # real key: crypto feed, 24/7
