# key-maint

Transport-tiered OpenBB widget reporting provider API key state — the
"key status" widget from *Adventures in OpenBB, Ep. 3*. Design:
`../docs/key-maint-design.md`.

The idea: **what you can see depends on how you connected.** Same service,
three postures:

| Tier | How you connect | Sees |
|---|---|---|
| 1 | Public internet via Tailscale Funnel, `https://openbb.<your-tailnet>.ts.net:10000` | key status + demo flag |
| 2 | Same URL from a tailnet device | + `run_tests` live probes |
| 3 | Unix socket on the host, via an SSH exec session | + the key values themselves |

All tiers require the main API's Basic auth (`api-auth.env`). Tier 3 has no
port and no header at all — the admin server binds a **unix socket** in a
host directory you create with mode 0700: being able to reach that file *is*
the authorization.

## Deploy

1. One-time, beside the compose file:
   `mkdir -p key-maint-admin && chmod 700 key-maint-admin`
2. `docker compose up -d --build key-maint`
3. Serve publishes the network server on `:10000` (see `ts-config/serve.json`);
   the funnel variant additionally opens that port to the internet — tier 1
   exists only if you choose that.

## Use

- **Widget:** add `https://openbb.<your-tailnet>.ts.net:10000` as a backend in
  BDOBB or OpenBB Workspace (same Authorization header as the main API) — the
  key status widget appears in the library.
- **Tier 3 (values):** run curl on the host over SSH — works regardless of the
  host's sshd forwarding policy, since it never opens a forwarded channel:

      ssh <host> "curl -s -u openbb:<password> \
        --unix-socket <stack-dir>/key-maint-admin/key-maint-admin.sock \
        http://localhost/keys"

  Alternative, only if your sshd permits streamlocal forwarding (many NAS
  builds deny it):
  `ssh -L 18446:<stack-dir>/key-maint-admin/key-maint-admin.sock <host>` →
  `http://localhost:18446`. `scripts/smoke_live.py` needs this `-L` form.

## Test

    pip install -e . && pytest

The suite mocks all probes; nothing needs a live deployment.
