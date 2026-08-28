"""Shared adjusted-series ATR for the structure-detection package.

`app.ta.exprs.true_range()` is raw-OHLC by documented contract -- RSI, ADX,
and the `atr` registry entry all depend on that, so it cannot be reused here.
Detection in this package runs on the ADJUSTED series throughout (see
app/structure/pivots.py), and the volatility threshold has to be measured in
that same price space or it is comparing a raw-space number to an
adjusted-space move -- wrong scale always, and wrong by roughly 2x right at a
split. This module re-derives Wilder's true range on adjusted OHLC instead.

Pivots, trendlines, and levels (tasks 1-3) all need this same series, so it
lives here rather than inline in any one of them.
"""
from __future__ import annotations

import polars as pl

from app.ta.exprs import adj


def adjusted_atr(df: pl.DataFrame, period: int = 14) -> list[float]:
    """Wilder's ATR, computed from the ADJUSTED high/low/close.

    Same formula as `app.ta.exprs.true_range()` -- max of (high-low),
    |high-prev_close|, |low-prev_close| -- and the same Wilder smoothing
    (`ewm_mean(alpha=1/period, adjust=False, ignore_nulls=True)`), but every
    input is `adj(...)` first so a split does not jump the series.
    """
    hi, lo, c = adj("high"), adj("low"), adj("close")
    prev_c = c.shift(1)
    true_range = pl.max_horizontal((hi - lo), (hi - prev_c).abs(), (lo - prev_c).abs())
    atr = true_range.ewm_mean(alpha=1 / period, adjust=False, ignore_nulls=True)
    return df.select(atr.alias("_atr"))["_atr"].to_list()
