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
        "last_size": tick.get("size"),
        "last_timestamp": tick["time"],
    }
    if prev_close is not None:
        row["prev_close"] = prev_close
        row["change"] = tick["price"] - prev_close
        if prev_close:
            row["change_percent"] = (tick["price"] - prev_close) / prev_close
    return row


async def _await_tick(store, symbol: str, deadline: float) -> dict | None:
    """Poll for a first tick until the deadline. Returns None if none arrives."""
    loop = asyncio.get_running_loop()
    stop = loop.time() + deadline
    while True:
        tick = await asyncio.to_thread(store.latest_tick, symbol)
        if tick is not None:
            return tick
        if loop.time() >= stop:
            return None
        await asyncio.sleep(POLL_S)


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
        return {"symbol": symbol, "tick": tick, "prev_close": None}

    @staticmethod
    def transform_data(query, data: dict, **kwargs) -> list[EquityQuoteData]:
        row = build_quote(data["symbol"], data.get("tick"), data.get("prev_close"))
        return [EquityQuoteData.model_validate(row)] if row else []
