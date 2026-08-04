# OpenBB Workspace ↔ NAS backend: setup & usage

Working as of 2026-08-04. The NAS widget backend is public via Tailscale Funnel
with HTTP Basic auth; the Workspace connection is named **Personal OpenBB**.

## Connection settings (pro.openbb.co → Connections → Personal OpenBB)

| Field | Value |
|---|---|
| Name | Personal OpenBB |
| Endpoint URL | `https://openbb.tailb9874f.ts.net/` |
| Validate widgets | **No** (Yes live-fires all 442 widgets and fails on keyless providers — EIA/BLS) |
| Auth Key | `Authorization` |
| Auth Value | `Basic <base64(openbb:password)>` — password lives in `nas:/share/Container/openbb/api-auth.env` (chmod 600, gitignored) |
| Auth Location | Header |

Works from any device, on or off the tailnet (Funnel publishes port 443 only;
the MCP ports 8443/8444 stay tailnet-only).

## Adding a widget from this backend

1. Open a dashboard (sidebar → My Dashboards, or `+` → New Dashboard).
2. Click the **grid + icon** (bottom-right) or the **Add widgets** card to open
   the Widget Menu.
3. Search, e.g. **"Historical"**. Every row tagged **Personal OpenBB** comes from
   the NAS backend; the grey label before the tag is the data provider
   (Eodhd, FMP, YFinance, Kdb, Arcticdb, ...). Use the left column to filter by
   category (Equity, Crypto, Currency, ETF, ...).
4. Tick the widget — e.g. **Historical (Chart) · Eodhd** under Equity → Price —
   and click **Add widgets**.
5. Set the widget's parameters in its title bar: Symbol `AAPL`, Interval `1d`,
   Exchange `US` → live OHLCV candlestick chart served by the NAS.

EODHD-backed widgets available: equity/crypto/currency/ETF Historical
(+ Chart variants), and equity fundamentals: Balance, Cash, Income, Dividends,
Historical Splits.

## Troubleshooting

- **Connection shows Inactive**: happens if the Workspace polls while the
  `openbb-api` container is restarting (~60 s window). Connections page →
  Inactive → refresh icon on the row.
- **Test fails with generic 500**: Validate widgets is set to Yes. Set it to No.
  Permanent fix: install free EIA/BLS keys in
  `nas:/share/Container/openbb/credentials.env` (values only — no inline
  comments on key lines) then `docker compose up -d openbb-api` from
  `/share/Container/openbb`.
- **Everything 401s**: the Authorization header was removed or the password
  rotated — re-derive `base64(openbb:<password>)` from api-auth.env and update
  the connection.
- **"blocked by CORS policy: Permission was denied for this request to access
  the `local` address space"** (and the connection flips Inactive on every
  login): only happens on machines running Tailscale — MagicDNS resolves the
  ts.net name to the tailnet 100.x address, which Chromium browsers treat as
  the local network and gate behind a per-site permission. The server's CORS
  is fine. Chosen fix: per-site grant — padlock → Permissions → **Local
  network access** → Allow for pro.openbb.co
  (`edge://settings/content/localNetworkAccess`), with browser Secure DNS
  left OFF so MagicDNS keeps the fast direct tailnet path. (Enabling DoH also
  works — public Funnel IPs never trigger the gate — but adds ~0.7 s per
  widgets.json fetch; rejected.) Devices without Tailscale are unaffected.
  Note the misleading symptom: `widgets.json` loads fine as a direct tab
  navigation (exempt) while the app's cross-origin fetch is blocked.
