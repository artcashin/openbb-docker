# key-maint

Transport-tiered OpenBB widget reporting provider API key state. Spec:
`../docs/superpowers/specs/2026-08-04-key-maint-design.md`.

| Tier | How you connect | Sees |
|---|---|---|
| 1 | Funnel (public), `https://openbb.tailb9874f.ts.net:10000` | status + demo flag |
| 2 | Same URL from a tailnet device | + `run_tests` probes |
| 3 | `ssh -L 8446:127.0.0.1:8446 nas` → `http://localhost:8446` | + key values |

All tiers require the main API's Basic auth (api-auth.env).

## Deploy (NAS)

1. `scp` the repo's compose to the NAS as usual, then from
   `/share/Container/openbb`: `docker compose up -d --build key-maint`.
2. Add to `ts-config/serve.json` (containerboot applies it live):
   - `"TCP"`: `"10000": {"HTTPS": true}`
   - `"Web"`: `"${TS_CERT_DOMAIN}:10000": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8447"}}}`
   - `"AllowFunnel"`: `"${TS_CERT_DOMAIN}:10000": true`
3. Verify: `docker exec openbb-ts tailscale funnel status` shows :10000 funneled,
   443 unchanged, 8443/8444 still tailnet-only.

## Verify tiers

- Public: `dig +short @8.8.8.8 openbb.tailb9874f.ts.net` then curl via that IP —
  rows have no `value`, no `test`.
- Tailnet: same URL on-tailnet with `run_tests=true` — `test` present.
- Admin: `ssh -L 8446:127.0.0.1:8446 nas`, then
  `python scripts/smoke_live.py http://localhost:8446 <user> <pass>` — `value`
  present.

## BDOBB

Add two backends: `https://openbb.tailb9874f.ts.net:10000` (daily use) and
`http://localhost:8446` (admin, only works while the tunnel is up), both with
the standard Authorization header.
