# key-maint

Transport-tiered OpenBB widget reporting provider API key state. Spec:
`../docs/superpowers/specs/2026-08-04-key-maint-design.md`.

| Tier | How you connect | Sees |
|---|---|---|
| 1 | Funnel (public), `https://openbb.tailb9874f.ts.net:10000` | status + demo flag |
| 2 | Same URL from a tailnet device | + `run_tests` probes |
| 3 | `ssh nas "curl -s -u openbb:<password> --unix-socket /share/Container/openbb/key-maint-admin/key-maint-admin.sock http://localhost/keys"` | + key values |

All tiers require the main API's Basic auth (api-auth.env).

Tier 3, primary method: run curl directly on the NAS over an SSH exec session
— `ssh nas "curl -s -u openbb:<password> --unix-socket /share/Container/openbb/key-maint-admin/key-maint-admin.sock http://localhost/keys"`.
This works regardless of the NAS's `sshd` local-forwarding policy, since it
never opens a forwarded channel.

Alternative, only if your NAS sshd permits local forwarding (QNAP default
denies it — `AllowTcpForwarding`/`streamlocal` — this one reproducibly gets
"administratively prohibited" on this NAS):
`ssh -L 18446:/share/Container/openbb/key-maint-admin/key-maint-admin.sock nas`
→ `http://localhost:18446`. `scripts/smoke_live.py` needs this `-L` form (it
makes an HTTP request against a `base` URL, not an SSH exec call), so it only
works when local forwarding is available.

## Deploy (NAS)

1. One-time: `mkdir -p /share/Container/openbb/key-maint-admin && chmod 700
   /share/Container/openbb/key-maint-admin` — the admin unix socket is
   created inside this bind-mounted host directory, so its permissions are
   the entire tier-3 access control.
2. `scp` the repo's compose to the NAS as usual, then from
   `/share/Container/openbb`: `docker compose up -d --build key-maint`.
3. Add to `ts-config/serve.json` (containerboot applies it live):
   - `"TCP"`: `"10000": {"HTTPS": true}`
   - `"Web"`: `"${TS_CERT_DOMAIN}:10000": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8447"}}}`
   - `"AllowFunnel"`: `"${TS_CERT_DOMAIN}:10000": true`
4. Verify: `docker exec openbb-ts tailscale funnel status` shows :10000 funneled,
   443 unchanged, 8443/8444 still tailnet-only.

## Verify tiers

- Public: `dig +short @8.8.8.8 openbb.tailb9874f.ts.net` then curl via that IP —
  rows have no `value`, no `test`.
- Tailnet: same URL on-tailnet with `run_tests=true` — `test` present.
- Admin: `ssh nas "curl -s -u openbb:<password> --unix-socket /share/Container/openbb/key-maint-admin/key-maint-admin.sock http://localhost/keys"` —
  `value` present in the returned JSON. `scripts/smoke_live.py` requires the
  `-L` alternative above (`ssh -L 18446:/share/Container/openbb/key-maint-admin/key-maint-admin.sock nas`,
  then `python scripts/smoke_live.py http://localhost:18446 <user> <pass>`) —
  only usable if your NAS sshd permits local forwarding.

The admin bind is a unix socket, not a published port: the compose file no
longer publishes anything on the tailscale service (that would only forward
to the container bridge IP, not its loopback, and can't work). Reaching the
socket requires an SSH session as the NAS admin user with access to
`/share/Container/openbb/key-maint-admin/` — a host directory that must be
mode 0700 and owned by that user. That filesystem access, not a port or
header, is tier 3's authorization; sibling containers sharing the netns
cannot reach the admin API at all.

## BDOBB

Add two backends: `https://openbb.tailb9874f.ts.net:10000` (daily use) and,
if your NAS sshd permits local forwarding, `http://localhost:18446` (admin,
only works while the `-L` SSH tunnel above is up) — both with the standard
Authorization header. BDOBB needs an HTTP backend URL, so it can only reach
tier 3 via the `-L` alternative, not the primary `ssh nas curl` form.
