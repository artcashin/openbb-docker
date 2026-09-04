"""Per-symbol RSI(14) and anchored VWAP for the live grid's studies column.

One `compute()` call over one frame -- one frame built, one pass, one set of
columns out -- so the grid's RSI cannot drift from the chart's: both read the
same registry entry (`app.ta.registry`'s `rsi`, Wilder's smoothing with
alpha=1/n), not the prototype's Cutler SMA. Note this is NOT a `Base` dedup:
both `rsi` and `avwap` declare `deps=lambda p: []`, so neither contributes a
shared `Base` and there is nothing to deduplicate between them -- the win
here is one frame and one round trip, not shared sub-series. The AVWAP
anchor is left None (cumulative from the first bar carrying trade data): a
watchlist mixes US equities, crypto and forex, which do not share one
session open, so there is no single "session open" default that means the
same thing for every row.

Null discipline: a symbol with no trade data has no RSI and no AVWAP. Both
are None, never 0 and never 50 -- a scanner sorted on this column must not
rank a cold symbol as neutral.
"""

from __future__ import annotations

from typing import Any

from app.ta.compute import compute
from app.ta.payload import bars_to_frame
from app.ta.registry import col_suffix, resolve

_RSI_REQ = resolve("rsi", period=14)
_AVWAP_REQ = resolve("avwap", anchor=None)
_RSI_COL = "rsi" + col_suffix(_RSI_REQ)
_AVWAP_COL = "avwap" + col_suffix(_AVWAP_REQ)


def _blank(symbol: str) -> dict[str, Any]:
    return {"symbol": symbol, "rsi": None, "avwap": None, "avwap_dev": None}


def studies_for(symbol: str, bars: list[dict]) -> dict[str, Any]:
    """RSI(14) and anchored-VWAP deviation from one symbol's bars.

    `avwap_dev` is `(price - avwap) / avwap`, the signed fraction the grid's
    deviation bar renders. It is kept separate from `avwap`: the bar wants
    the fraction, a trader reading the column wants the price.
    """
    if not bars:
        return _blank(symbol)
    frame = bars_to_frame(bars)
    if frame.is_empty():
        return _blank(symbol)
    out = compute(frame, [_RSI_REQ, _AVWAP_REQ])
    rsi = out[_RSI_COL][-1]
    avwap = out[_AVWAP_COL][-1]
    price = out["close"][-1]
    dev = None
    if avwap is not None and avwap != 0 and price is not None:
        dev = (price - avwap) / avwap
    return {
        "symbol": symbol,
        "rsi": None if rsi is None else float(rsi),
        "avwap": None if avwap is None else float(avwap),
        "avwap_dev": None if dev is None else float(dev),
    }
