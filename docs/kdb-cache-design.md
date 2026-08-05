# kdb+ read-through cache for the OpenBB Platform

**Date:** 2026-08-04
**Status:** Approved
**Ships as:** v10.0.0 — *Adventures in OpenBB, Ep. 10*

## Purpose

Put an in-memory kdb+ tier underneath OpenBB's historical price path so that
repeated and overlapping requests for the same series are served from RAM
instead of going out to the network. A new provider, `provider="kdb"`, is a
**read-through cache**: it serves what it has, fetches only what it is missing
from an upstream provider (EODHD by default), stores that, and returns the
merged series.

Two things must be true for this to be worth an episode:

1. Zooming a chart from 1 year to 3 years fetches **two years of bars, once** —
   not three years, and not again on the next zoom.
2. The stack still works for a reader with no kdb+ license at all.

## What already exists

- `openbb-docker-old/openbb-kdb` — a PyKX-over-IPC OpenBB extension with a
  write path (`res.kdb.write()`) and a `provider="kdb"` read path with
  tick→OHLCV resampling. It serves **only what is already stored**; there is no
  fetch-on-miss. The new extension is a cache-scoped descendant of it.
- `kdb-x` container images (`QHOME=/root/.kx`, `KX_PORT=5000`), with a kdb-x
  license baked in.
- `openbb-eodhd` (Ep. 8), in this repo — the default upstream.

## Design decisions

### q runs inside the OpenBB container, not as its own service

`q` is started as a **child process of the openbb-api container**, bound to
`127.0.0.1:5000`. PyKX connects to it as an IPC client over loopback.

True in-process embedding (PyKX embedded q) was tested and rejected: PyKX
refuses the kdb-x license with `licence error: kc.lic`, and PyKX's unlicensed
mode is IPC-client-only. Embedded q would require registering for a separate
kdb+/PyKX personal license, and would give every process its *own* heap.

The loopback child process is better here anyway, because **every service in
this stack shares the tailscale container's network namespace**
(`network_mode: service:tailscale`). One q on `127.0.0.1:5000` is therefore on
the same loopback as openbb-mcp, live-grid, key-maint and cache-chart — they
all share one warm cache — while remaining unreachable from every tailnet peer.

### The cache is memory-only

No persistence, no snapshot, no reload on start. A restart means a cold cache
that refills on demand. This is a cache; it is allowed to be empty.

### Configuration

| Var | Default | Meaning |
|---|---|---|
| `KDB_EMBEDDED` | `true` | Spawn q inside the container. `false` skips spawning. |
| `KDB_HOST` | `127.0.0.1` | Point at an **existing kdb+ server** instead of the spawned one. |
| `KDB_PORT` | `5000` | As above. |
| `KDB_MEMORY_MB` | `8192` | Cache budget. q is launched with `-w` = this × 1.25 as containment. |
| `KDB_CACHE_WATERMARK` | `0.75` | Fraction of budget (measured on `.Q.w[]` `heap`) that triggers LRU eviction. |
| `KDB_UPSTREAM` | `eodhd` | Provider used for cache misses. Any registered provider. |

Spawned-local and bring-your-own-server are the **same code path** — an IPC
connection to a host and port. Only the "do we start q ourselves" step differs.

### How q gets into the image, and licensing

This repo's Dockerfile gains a stage that copies the kdb-x runtime (`QHOME`,
the `q` binary and its libs) into the OpenBB image, **excluding `kc.lic`**, and
adds PyKX to the Python environment. The published image therefore carries the
runtime but no license.

The reader supplies their own kdb-x license, mounted from a git-ignored path
(the `ts.env` / `api-auth.env` pattern) and pointed at by `QLIC`. No license
blob enters this repository or any image published from it — the existing
`kdb-x` images have one baked in and must not be republished as-is.

With no license, no q, or an unreachable server, `provider="kdb"` **passes
through to the upstream provider**. The stack runs uncached rather than
failing. This is also what makes the episode's before/after comparison
possible.

## The read-through path

### State in q

| Structure | Contents |
|---|---|
| `.cache.bars` | One OHLCV table per `(symbol, interval)`, timestamp-keyed |
| `.cache.cov` | Coverage ranges: `(sym; iv; start; end)` — the windows actually fetched |
| `.cache.lru` | Last-access time per `(symbol, interval)` |

`.cache.cov` is the load-bearing structure. Without it there is no way to
distinguish "the provider has no data in that window" from "we never asked",
and the cache degenerates into whole-window memoization.

### Serving a request for `(symbol, interval, start, end)`

1. Look up coverage for `(symbol, interval)`. Compute `requested − covered` →
   a gap list, usually empty or a single range.
2. **The tail is never marked covered.** Coverage is recorded only up to the
   last *completed* bar boundary, so the current day (or current intraday bar)
   is refetched on every request. This solves the incomplete-bar problem with
   no TTL.
3. Fetch each gap from the upstream provider, resolved **by name** through
   OpenBB's registry: `RegistryLoader.from_extensions()` →
   `provider.fetcher_dict["EquityHistorical"].fetch_data(params, credentials)`.
   Nothing in this path is EODHD-specific, which is what makes `KDB_UPSTREAM`
   work for any provider.
4. Insert bars, coalesce contiguous coverage ranges, touch LRU.
5. Serve the merged window from q.

### Corporate-action backstop

A split or dividend retroactively rewrites *all* adjusted history, which would
otherwise leave the cache serving silently wrong prices.

Because step 2 refetches the tail regardless, the refetch is overlapped with a
few already-cached bars and the closes are compared. A mismatch means the
adjusted series was rewritten → drop that `(symbol, interval)` entirely and
refetch. The check costs no extra network traffic.

### Eviction and the memory ceiling

**Measured, not assumed** (probes against `kdb-x` `.z.K=5`, `-w 512`):

- Exceeding `-w` **kills the q process outright**. There is no catchable
  `'wsfull` — the IPC handle dies, the process exits, and reconnect fails.
  This happens on ordinary gradual growth, not just on one huge allocation:
  q died on the allocation after `heap` reached `wmax`.
- `heap`, not `used`, is what approaches `wmax`. `heap` grows in large steps
  (67 MB at a 512 MB cap) and ran ~33 MB ahead of `used`.
- `delete` alone frees `used` but **not** `heap`. `.Q.gc[]` returns heap fully
  to baseline (335 MB → 67 MB in the probe).

Therefore `-w` is **containment, not cache policy**. Its job is to protect the
rest of the container: without it, a runaway cache grows until the OOM killer
takes down openbb-api along with q. q is launched with
`-w` = `KDB_MEMORY_MB` × 1.25 so that normal operation never approaches it.

The real budget is enforced by the extension: when `.Q.w[]``heap` exceeds
`KDB_CACHE_WATERMARK` × `KDB_MEMORY_MB`, whole `(symbol, interval)` tables are
dropped oldest-first, each followed by `.Q.gc[]`.

### Surviving a dead q

Because q *can* die, the extension treats a dead connection as a normal state,
not an exception: it detects the closed handle, respawns q if it owns the
process (`KDB_EMBEDDED=true`), reconnects, and serves the request by
pass-through in the meantime. A cold cache after a death is correct behaviour —
the cache was never persisted anyway.

### Concurrency

A per-`(symbol, interval)` asyncio lock, so two widgets loading the same symbol
produce one upstream fetch rather than two.

### Telemetry

`transform_data` returns
`AnnotatedResult(result=…, metadata={cache, rows_from_cache, rows_from_upstream, gaps_fetched, upstream_ms, kdb_ms})`,
which OpenBB surfaces as `OBBject.extra["results_metadata"]` and the REST API
serializes into every response. Cache statistics travel with the data; no side
channel is needed.

`cache` is one of `hit` (no upstream call), `partial` (gaps fetched),
`miss` (nothing cached), or `bypass` (cache unavailable, passed through).

## Components

| Path | What it is |
|---|---|
| `openbb-kdb/` | Provider extension registering `provider="kdb"` for equity / ETF / crypto / currency / index historical. Added to this repo's Dockerfile (PyKX is not currently in the image). |
| `cache-chart/` | FastAPI service, `live-grid/`'s layout: the Workspace widget backend and the standalone demo page. |
| `docker-compose.yml` | q startup in the openbb-api container, `mem_limit`, license mount, Serve route for `:6906`. |
| `scripts/verify-isolation.sh` | Extended to check port 5000. |

Intervals in scope: daily **and** intraday (`1m`, `5m`, `1h`), matching EODHD's
coverage.

## The chart

`cache-chart/` calls the OpenBB API on loopback `127.0.0.1:6900` with
`provider=kdb` and Basic auth, so the demo exercises exactly the path any
client would take, and reads `extra.results_metadata` for the HUD.

| Endpoint | Purpose |
|---|---|
| `GET /widgets.json` | Workspace / BDOBB widget contract |
| `GET /chart` | Plotly figure JSON — the Workspace widget |
| `GET /series` | `{bars, cache}` — incremental loads for the demo page |
| `GET /demo` | the standalone page |
| `GET /health` | upstream and cache reachability |

### The scroll gesture

The page opens on 1 year of daily bars. `plotly.js` — **vendored into the image
at build time, never a CDN** — with `scrollZoom: true`. A `plotly_relayout`
handler fires on wheel-zoom; when the new x-range reaches past what is loaded,
the page requests **only the missing prefix** `[newStart, loadedStart)` and
prepends it. Debounced ~150 ms so one continuous scroll produces one request at
rest rather than one per wheel tick, with in-flight coalescing and a 10-year
clamp.

The HUD reports, per request: window requested, rows from cache, rows from
upstream, `upstream_ms`, `kdb_ms`, bytes over the wire — plus a cumulative
saved-bytes counter. A provider toggle (`kdb` ↔ `eodhd`) runs the identical
gesture with the cache off.

The demonstration is the **second** gesture. Scrolling 1y→3y the first time
reports `partial` with ~2 years fetched. Scrolling back in and out again
reports `hit`: zero rows upstream, no network traffic.

### Why there are two deliverables

A Workspace / BDOBB plotly widget receives figure JSON once and zooms
client-side; it will not refetch on scroll. The widget therefore benefits from
the cache on **parameter** changes — symbol, date range, interval — while the
scroll story requires the standalone page.

## Networking

`q` binds `127.0.0.1:5000` and is never published by Serve, never funneled.
`cache-chart` is published by Serve on `:6906`, tailnet-only, never funneled.

## Testing

The gap arithmetic is the one piece with real logic and no I/O: coverage
subtraction, range coalescing, tail exclusion and corporate-action detection
are unit-tested against a fake q connection. `cache-chart` gets mocked-HTTP
tests in the style of `live-grid/tests`. **CI needs neither a kdb license nor
an EODHD key.**

## Risks

1. **The loopback bind is load-bearing.** The default `KX_PORT=5000` binds all
   interfaces; inside the shared tailscale namespace that exposes an
   unauthenticated q — and q IPC executes arbitrary q — to every tailnet peer.
   Verified directly: a sibling container reaches `KX_PORT=5000` and cannot
   reach `KX_PORT=127.0.0.1:5000`. `verify-isolation.sh` gains a check for it.
2. **A bug in eviction kills q, it does not merely error.** Verified: crossing
   `-w` terminates the process, so the failure mode is "the cache tier
   vanished", not "a query failed". This is why eviction is preventive, why
   `-w` sits above the budget, and why respawn + pass-through are required
   rather than optional. The pass-through path must catch dead-connection
   errors (`RuntimeError`, `PyKXException`), not only connection-refused.
3. **PyKX connection sharing.** Verified that one `SyncQConnection` round-trips
   bars correctly; sharing a single connection across concurrent async tasks is
   still unproven and is settled by a dedicated task before anything depends
   on it.
4. **Adjusted-price drift.** Handled by the tail-overlap check; tested
   explicitly because it is the failure that would otherwise be silent.
5. **Container memory.** `mem_limit` on openbb-api must exceed
   `KDB_MEMORY_MB` plus OpenBB's own footprint (~2 GB), or the OOM killer takes
   the API down rather than just the cache.

## Rejected alternatives

| Approach | Why not |
|---|---|
| Whole-window memoization keyed on `(symbol, interval, start, end)` | A 1y→3y zoom is a total miss and refetches all three years. It defeats the entire point. |
| Eager prefetch of max history on first touch | The first request is slow and pulls far more than asked — the opposite of minimizing network traffic. Intraday max-history is enormous. |
| A caching proxy in front of the OpenBB API | Caches only the routes the proxy knows, and adds a second HTTP hop. The provider tier gets the cache to every consumer — API, MCP, widgets — for free. |
| A separate kdb container | Rejected in favour of q inside the openbb-api container; the shared network namespace already gives every service access to one loopback cache. |
| Persisting the cache to a volume | It is a cache. Cold-start-empty is simpler and honest, and avoids serving stale adjusted history from disk. |
