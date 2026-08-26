# Security

## Reporting

Open an issue, or contact the maintainer directly for anything you would
rather not file in public.

## API auth covered only `/api/v1` (fixed 2026-08-26)

**Affected: every release from v2.0.0 through v11.1.1, and `main` before the
fix.**

OpenBB wires its `authenticate_user` dependency into the `/api/v1` command
router only. Everything else served by the same app answered without
credentials, even with `OPENBB_API_AUTH=true`:

    /                /widgets.json    /apps.json    /agents.json
    /openapi.json    /docs            /redoc        /docs/oauth2-redirect

`/api/v1` itself was never affected — it returned 401 correctly throughout.

This mattered for two reasons. `docker-compose.yml` has instructed readers
since v2.0.0 to verify that `/widgets.json` returns 401 without credentials,
so the documentation asserted a posture the stack did not have. And
`/openapi.json` enumerates every endpoint, parameter and schema the API
exposes. Tailscale Funnel can publish this port to the public internet; on a
funneled deployment those paths were internet-reachable.

### Am I affected?

Run this against a deployment, from inside the container's network namespace:

    docker exec -i \
      -e OPENBB_URL=http://127.0.0.1:6900 \
      -e OPENBB_USER=<username> -e OPENBB_PASS=<password> \
      openbb-api bash -s < scripts/verify-auth.sh

`Auth posture FAILED` means the deployment is affected. `Auth posture
verified` means it is not. The script is shipped in every fixed release.

### The fix

A middleware requiring HTTP Basic auth on every path when
`OPENBB_API_AUTH` is set — not a route dependency, because `/docs` and
`/openapi.json` are registered by FastAPI itself and have no router to
attach one to. It no-ops when auth is disabled, which keeps the in-process
`openbb-mcp` wrapper working; that service is bound to loopback and is
never funneled.

`main` applies it in `api_app.py`'s factory. Tagged releases apply it as a
Dockerfile patch of `openbb_core/api/rest_api.py`, because `api_app.py`
postdates every tag.

The guard is registered **inside** the CORS layer, deliberately. A browser
preflight carries no credentials by definition, so an auth layer outside
CORS would answer it with a bare 401 and no `Access-Control-Allow-*`
headers — locking OpenBB Workspace out entirely, while every `curl` test
still passed. `verify-auth.sh` checks the preflight for exactly this reason.

### What was done

All eight affected tags were re-cut onto commits carrying the fix, the same
practice used for the CFTC startup-crash fix on 2026-08-21. The git tags
therefore point at fixed source. **Published container images are rebuilt
separately**, so if you pulled an image before its rebuild, re-pull and
re-run `verify-auth.sh`.

### Credit

Found on 2026-08-05 while auditing the metadata endpoints. The fix sat
unmerged on a branch for three weeks before being rediscovered and shipped.
