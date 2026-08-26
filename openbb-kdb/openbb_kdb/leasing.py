"""Best-effort lease of a live feed from live-grid.

Every failure here is swallowed by design. The quote path degrades to whatever
kdb already holds and then to EODHD's REST snapshot, so a lease that could not
be taken costs freshness, never an error. That invariant is the reason this
module exists separately from the fetcher.
"""

import logging
import os

log = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:6903/subscribe"
TIMEOUT_S = 1.0


async def _post(url: str, json: dict, timeout: float):
    import httpx

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, json=json)
        response.raise_for_status()
        return response.json()


async def lease(symbol: str, url: str | None = None, ttl: float | None = None, post=None) -> bool:
    """Lease `symbol` on the live feed. True if granted; never raises."""
    sym = str(symbol).strip().upper()
    if not sym:
        return False
    target = url or os.getenv("LIVE_GRID_SUBSCRIBE_URL", DEFAULT_URL)
    body: dict = {"symbols": [sym]}
    if ttl is not None:
        body["ttl"] = ttl
    try:
        payload = await (post or _post)(target, json=body, timeout=TIMEOUT_S)
        return sym in (payload or {}).get("leases", {})
    except Exception as exc:  # noqa: BLE001 - a lease failure must not fail a quote
        log.debug("lease for %s failed: %s", sym, exc)
        return False


DEFAULT_SNAPSHOT_URL = "http://127.0.0.1:6903/snapshot"


async def _get(url: str, params: dict, timeout: float):
    import httpx

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()


async def snapshot(symbol: str, url: str | None = None, get=None) -> dict | None:
    """Delayed REST snapshot for a symbol, or None. Never raises."""
    sym = str(symbol).strip().upper()
    if not sym:
        return None
    target = url or os.getenv("LIVE_GRID_SNAPSHOT_URL", DEFAULT_SNAPSHOT_URL)
    try:
        return await (get or _get)(target, params={"symbol": sym}, timeout=TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 - a missing fallback is not an error
        log.debug("snapshot for %s failed: %s", sym, exc)
        return None
