# openbb-kdb

In-memory **kdb+ read-through cache** for the OpenBB Platform. Companion code
for *Adventures in OpenBB, Ep. 10*.

`provider="kdb"` serves bars it already holds, fetches only the date ranges it
is missing from an upstream provider, stores those, and returns the merged
series:

```python
obb.equity.price.historical("AAPL", provider="kdb",
                            start_date="2022-01-01", end_date="2025-01-01")
```

Every response carries cache telemetry in `extra["results_metadata"]`:

```python
{"cache": "partial", "rows_from_cache": 252, "rows_from_upstream": 504,
 "gaps_fetched": 1, "upstream_ms": 412.7, "kdb_ms": 3.1}
```

`cache` is `hit` (no upstream call), `partial` (gaps fetched), `miss` (nothing
cached) or `bypass` (kdb unavailable — served straight from upstream).

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `KDB_EMBEDDED` | `true` | Spawn q inside this container |
| `KDB_HOST` | `127.0.0.1` | Point at your own kdb+ server (disables spawning) |
| `KDB_PORT` | `5000` | |
| `KDB_MEMORY_MB` | `8192` | Cache budget; q gets `-w` 25% above it |
| `KDB_CACHE_WATERMARK` | `0.75` | Heap fraction that triggers LRU eviction |
| `KDB_UPSTREAM` | `eodhd` | Provider used for cache misses — any installed provider |

## Requirements

- A kdb+/kdb-x **license** (`kc.lic`) for the q server. PyKX itself runs as an
  unlicensed IPC client and needs none.
- Without a license or a reachable q, the provider passes through to the
  upstream and reports `cache: "bypass"`. Nothing breaks.

## Notes

- **The cache is memory-only.** A restart means a cold cache. It is a cache.
- **q binds `127.0.0.1`.** Every service in this stack shares one network
  namespace, so a `0.0.0.0` bind would publish an unauthenticated q — which
  executes arbitrary q — to every peer on the tailnet.
- **Crossing q's `-w` kills the process**; there is no catchable `'wsfull`.
  Eviction is preventive and `-w` is containment for the rest of the container.

## Test

    pip install -e .[dev] && pytest    # no kdb license or provider key needed
