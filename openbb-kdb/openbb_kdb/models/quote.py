"""Equity quotes served from the live tick store.

`last_price` comes from the newest tick live-grid recorded; `prev_close` from
the last complete daily bar. Intraday open/high/low/volume are deliberately
absent during a session: a daily bar has no row for today until the close, and
deriving them from the single latest tick would report open == high == low ==
last_price, which looks like data rather than like the absence of it.
"""

import asyncio
import logging
import os
from typing import Any

from openbb_core.provider.abstract.fetcher import Fetcher
from openbb_core.provider.standard_models.equity_quote import (
    EquityQuoteData,
    EquityQuoteQueryParams,
)

from openbb_kdb.leasing import lease

log = logging.getLogger(__name__)

DEADLINE_S = 3.0
POLL_S = 0.1


def build_quote(symbol: str, tick: dict | None, prev_close: float | None) -> dict | None:
    """Assemble one EquityQuote row. Returns None when there is no tick."""
    if not tick:
        return None
    row: dict[str, Any] = {
        "symbol": symbol,
        "last_price": tick["price"],
        "last_timestamp": tick["time"],
    }
    # EquityQuoteData.last_size is int | None; kdb reports size as a float.
    # A whole-valued float (40.0) coerces cleanly, but a fractional one would
    # raise ValidationError and 500 the route -- omit it instead of guessing.
    size = tick.get("size")
    if size is not None and float(size).is_integer():
        row["last_size"] = int(size)
    if prev_close is not None:
        row["prev_close"] = prev_close
        row["change"] = tick["price"] - prev_close
        if prev_close:
            row["change_percent"] = (tick["price"] - prev_close) / prev_close
    return row


async def _await_tick(store, symbol: str, deadline: float) -> dict | None:
    """Poll for a first tick until the deadline. Returns None if none arrives.

    Each `store.latest_tick` call runs in a worker thread and is bounded by
    `asyncio.wait_for` against the *remaining* deadline: a cold session's own
    connect budget (kdb_store.session._CONNECT_BUDGET_S = 5.0) or a wedged
    call could otherwise block past -- or forever past -- our 3.0s default,
    defeating the reason this deadline exists.
    """
    loop = asyncio.get_running_loop()
    stop = loop.time() + deadline
    while True:
        remaining = stop - loop.time()
        try:
            tick = await asyncio.wait_for(
                asyncio.to_thread(store.latest_tick, symbol), timeout=max(remaining, 0)
            )
        except asyncio.TimeoutError:
            return None
        if tick is not None:
            return tick
        remaining = stop - loop.time()
        if remaining <= 0:
            return None
        await asyncio.sleep(min(POLL_S, remaining))


async def _prev_close(cache, symbol: str, credentials) -> float | None:
    """Close of the most recent complete daily bar, or None.

    Best-effort exactly like the lease: a quote that knows the last price but
    not yesterday's close is still a useful quote, and is what the spec asks
    for when no bar is available.
    """
    from datetime import date, timedelta

    end = date.today()
    try:
        # get() answers (rows, metadata) -- unpack, do not index the tuple.
        rows, _meta = await cache.get(
            symbol=symbol, interval="1d", start=end - timedelta(days=10), end=end,
            model="EquityHistorical", params={}, credentials=credentials,
        )
    except Exception as exc:  # noqa: BLE001 - see docstring
        log.debug("prev_close lookup for %s failed: %s", symbol, exc)
        return None
    if not rows:
        return None
    close = rows[-1].get("close")
    return float(close) if close is not None else None


class KdbEquityQuoteFetcher(Fetcher[EquityQuoteQueryParams, list[EquityQuoteData]]):
    """The newest live tick for a symbol, leasing a feed if one is not running."""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> EquityQuoteQueryParams:
        return EquityQuoteQueryParams(**params)

    @staticmethod
    async def aextract_data(query, credentials, **kwargs) -> dict:
        from openbb_kdb.models.historical import _cache

        symbol = query.symbol.upper()
        # Unconditional and idempotent: renewing keeps a hot symbol hot, and
        # tick recency cannot distinguish "quiet" from "not subscribed".
        await lease(symbol)

        cache = _cache(credentials)
        store = cache.store
        deadline = float(os.getenv("KDB_QUOTE_DEADLINE_S", DEADLINE_S))
        tick = await _await_tick(store, symbol, deadline)
        prev = await _prev_close(cache, symbol, credentials)
        return {"symbol": symbol, "tick": tick, "prev_close": prev}

    @staticmethod
    def transform_data(query, data: dict, **kwargs) -> list[EquityQuoteData]:
        row = build_quote(data["symbol"], data.get("tick"), data.get("prev_close"))
        return [EquityQuoteData.model_validate(row)] if row else []
