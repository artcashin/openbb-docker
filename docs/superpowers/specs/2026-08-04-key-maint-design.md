# Key-Maint: API key maintenance widget service

**Date:** 2026-08-04
**Status:** Approved

## Purpose

A small FastAPI service (`key-maint/`, mirroring `live-grid/`'s layout) that
serves one OpenBB table widget reporting the state of every provider API key in
`credentials.env`: set / empty / missing, whether the value is the provider's
public demo key, and an on-demand live test of each key against its provider.
Rendered by BDOBB as an additional backend; usable from OpenBB Workspace too.

What a caller may see is decided by **how the request arrived**, not by
configuration:

| Tier | Transport | Sees |
|---|---|---|
| 1 Public | Tailscale Funnel (port 10000) | provider, status, demo highlight |
| 2 Tailnet | Tailscale Serve, on-tailnet client | tier 1 + `run_tests` live probes |
| 3 Admin | SSH tunnel to NAS host loopback | tier 2 + actual key values; phase-2 editing |

Key values are visible only to someone who can SSH into the NAS. There is no
password, token, or mode flag that reveals them over the network paths —
the admin bind is simply unreachable except through the host's loopback.

## Access-tier mechanics

One FastAPI app, two uvicorn binds inside the tailscale sidecar's netns:

- **`127.0.0.1:8447` — network-facing bind.** `serve.json` proxies
  `${TS_CERT_DOMAIN}:10000 → http://127.0.0.1:8447`, and `AllowFunnel` gains
  `"${TS_CERT_DOMAIN}:10000": true`. (Funnel supports only 443/8443/10000;
  443 and 8443 are taken.) Requests on this bind are at most tier 2.
- **`127.0.0.1:8446` — admin bind.** The tailscale service adds
  `ports: ["127.0.0.1:8446:8446"]`, publishing it to the **NAS host's
  loopback only** (never the LAN). Reaching it requires
  `ssh -L 8446:127.0.0.1:8446 nas`, then `http://localhost:8446` in BDOBB or
  a browser. Arrival on this bind IS tier 3 — the port is the proof.

Within the 8447 bind, tier 1 vs 2 is decided by the client address tailscaled
reports in `X-Forwarded-For`: `100.64.0.0/10` → tailnet (tier 2), anything
else → funnel (tier 1). Implementation must verify tailscaled replaces (not
appends to) a client-supplied `X-Forwarded-For`; if that cannot be
demonstrated, default to tier 1 on ambiguity. The stakes are low by design:
header spoofing could at worst trigger quota-burning probes (tier 2), never
reveal values (tier 3 is port-gated).

`app.main` starts two uvicorn servers in one process, each wrapping its own
app instance constructed with an explicit role (`network` / `admin`); the
role is fixed at construction, so there is no per-request detection to get
wrong. The `/keys` response includes `"tier"` so clients can label the view.

## Endpoints

- `GET /widgets.json` — one widget: type `table`, category "Admin", name
  "Provider API Keys". Params: `run_tests` (boolean, default false). No
  mode param — tier decides everything.
- `GET /keys?run_tests=<bool>` — array of rows:

```json
{
  "provider": "EODHD",
  "env_var": "EODHD_API_KEY",
  "status": "set",            // set | empty | missing
  "demo": true,               // value equals a known public demo key
  "value": "demo",            // tier 3 only; omitted otherwise (not masked, absent)
  "test": {"result": "ok", "detail": "200 in 412ms"}  // run_tests=true, tier >= 2
}
```

- `PUT /keys/{env_var}` — tier 3 only; returns **501** in phase 1. Reserved
  for phase 2 (editing + apply mechanism, deliberately undesigned here).
- CORS: allow origin `https://pro.openbb.co` (live-grid precedent). BDOBB
  bypasses CORS via Tauri.
- All endpoints on both binds require the same HTTP Basic auth as the main
  API: credentials read from `api-auth.env` (mounted read-only). Even tier 1's
  status view is an infrastructure inventory; it is not anonymous.

## Reading key state

- `credentials.env` is bind-mounted **read-only** at `/config/credentials.env`
  and parsed per compose dotenv semantics (`KEY=value` lines, values taken
  verbatim; the repo convention keeps comments on their own lines — see
  docs on the 2026-08-04 inline-comment incident). The file is re-read on
  every request (it is tiny); what the widget shows is exactly what the next
  container restart will load — file truth, not process-env truth.
- A static **registry** (`app/registry.py`) maps each known env var to:
  display name, provider URL, known public demo values (e.g. EODHD `demo`,
  Alpha Vantage `demo`), and a probe spec. Non-credential vars in the file
  (`KDB_HOST`, `ARCTICDB_*`, ...) are listed in an explicit ignore set.
- Coverage rules: registry entry with no line in the file → `status:
  "missing"`. `*_API_KEY`/`*_TOKEN`/`*_SECRET` line with no registry entry →
  generic row (no demo detection, no probe). A pytest asserts every
  credential var in `credentials.env.example` is either in the registry or
  the ignore set, so they cannot drift.

## Live testing (tier ≥ 2, `run_tests=true`)

- Each registry probe spec is the cheapest authenticated call for that
  provider (EODHD: 1-bar EOD AAPL; FRED: 1 observation; BLS: 1 series; EIA:
  minimal STEO query; ...). Probes run concurrently via httpx, 5 s timeout
  each.
- Classification: `ok` (2xx), `auth_failed` (401/403 or the provider's
  known invalid/missing-key body shape), `error` (timeout, network, 5xx),
  `skipped` (key not set, or provider has no probe spec).
- Key values never appear in logs, error details, or probe URLs echoed back;
  probe `detail` strings are built from status/latency only.
- Probes never fire on widget load — only when the user flips `run_tests`.
  Tier 1 requests ignore `run_tests` entirely (`test` field absent).

## Deployment

New service in `docker-compose.nas.yml`:

```yaml
  key-maint:
    build: ./key-maint            # python:3.12-slim + fastapi/uvicorn/httpx
    container_name: openbb-key-maint
    restart: unless-stopped
    network_mode: service:tailscale
    depends_on: [tailscale]
    command: ["python", "-m", "app.main"]   # starts both binds (8446 admin, 8447 network)
    volumes:
      - ./credentials.env:/config/credentials.env:ro
      - ./api-auth.env:/config/api-auth.env:ro
```

Plus, on the **tailscale** service: `ports: ["127.0.0.1:8446:8446"]`.

`serve.json` additions: `"TCP": {"10000": {"HTTPS": true}}`,
`"Web": {"${TS_CERT_DOMAIN}:10000": {"Handlers": {"/": {"Proxy":
"http://127.0.0.1:8447"}}}}`, `"AllowFunnel": {"${TS_CERT_DOMAIN}:10000":
true}`. containerboot re-applies the file live (no restart needed).

Client setup:
- BDOBB / Workspace (any device): backend `https://openbb.<tailnet>.ts.net:10000`
  with the standard Authorization header. On-tailnet browsers get tier 2
  automatically; off-tailnet get tier 1.
- Admin session: `ssh -L 8446:127.0.0.1:8446 nas`, then backend
  `http://localhost:8446` (same Authorization header).

## Error handling

- `credentials.env` missing/unreadable → every registry row `status:
  "unknown"` plus a synthetic first row explaining the mount problem; HTTP
  200 always. The widget must degrade, never blank.
- Malformed lines are skipped and reported as a synthetic warning row.
- Probe failures are per-row results, never request failures.
- Unknown query params ignored; `PUT` on phase 1 → 501 with a one-line body.

## Testing

- **pytest (CI):** dotenv parsing (incl. comment-line convention and the
  empty-value+inline-comment hazard), registry/example sync, ignore-set
  completeness, tier resolution (role flag → tier; XFF CGNAT → 2, public →
  1, absent/ambiguous → 1), value presence/absence per tier, demo detection,
  probe classification against mocked httpx, Basic-auth enforcement on both
  binds, 501 on PUT.
- **`scripts/smoke_live.py` (manual):** real probes against live providers
  using the operator's own credentials.env; prints the table. Not run in CI.
- Follow live-grid's test layout and naming.

## Phase 2 seam (explicitly out of scope)

Editing: `PUT /keys/{env_var}` on the admin bind writes a staged
`credentials.env` change; the apply mechanism (docker socket vs host agent vs
manual `docker compose up -d`) is a separate brainstorm/spec. Nothing in
phase 1 may assume a particular choice; the 501 stub and the read-only mount
are the whole phase-1 footprint.

## Non-goals

- No Workspace "app" packaging, no charts — one table widget.
- No secrets management beyond display (no encryption-at-rest changes;
  credentials.env stays the source of truth).
- No per-user identity: tiers are transport-based, not user-based.
