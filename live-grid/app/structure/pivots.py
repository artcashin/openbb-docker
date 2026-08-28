"""ATR-normalised ZigZag pivot detection.

A pivot confirms when price retraces from the running extreme by k * ATR, with
the threshold read at the bar where the extreme formed -- so the move is judged
against the volatility that produced it, not today's.

Kernel-regression smoothing (Lo/Mamaysky/Wang) was rejected as the engine: its
extremum lands on a different bar than the real high, and the snap-back step
that fixes that reintroduces the arbitrariness the smoothing removed. Both
consumers need exact coordinates.
"""
from __future__ import annotations

import polars as pl

from app.structure.atr import adjusted_atr
from app.structure.types import Pivot, price_tag
from app.ta.exprs import adj


def _series(df: pl.DataFrame, atr_period: int):
    """Adjusted high/low/close plus Wilder ATR, as plain Python lists.

    The threshold below is `k * ATR`, tested against moves in the adjusted
    high/low/close -- so the ATR has to be measured in that same adjusted
    price space (see app/structure/atr.py) or the threshold is scaled wrong,
    worst right at a split.
    """
    frame = df.with_columns([
        adj("high").alias("_h"), adj("low").alias("_l"), adj("close").alias("_c"),
    ])
    atrs = adjusted_atr(df, atr_period)
    return (frame["_h"].to_list(), frame["_l"].to_list(), frame["_c"].to_list(),
            atrs, frame["date"].cast(pl.Utf8).to_list())


def find_pivots(df: pl.DataFrame, k: float, scale: str,
                atr_period: int = 14) -> list[Pivot]:
    if df.height < 2:
        return []
    highs, lows, closes, atrs, dates = _series(df, atr_period)

    def emit(bar: int, kind: str, prev_price: float | None,
             confirmed: bool) -> Pivot:
        price = highs[bar] if kind == "high" else lows[bar]
        atr = atrs[bar] or 0.0
        move = 0.0 if prev_price is None else abs(price - prev_price)
        return Pivot(
            id=f"p:{scale}:{kind}:{dates[bar]}:{price_tag(price)}",
            date=dates[bar], bar=bar, price=price, kind=kind,
            swing_atr=round(move / atr, 4) if atr else 0.0,
            swing_pct=round(100 * move / prev_price, 4) if prev_price else 0.0,
            confirmed=confirmed,
        )

    pivots: list[Pivot] = []
    direction: str | None = None      # "up" while tracking a high
    ext_bar = 0
    prev_price: float | None = None

    for i in range(1, len(closes)):
        threshold = k * (atrs[ext_bar] or 0.0)
        if threshold <= 0:
            continue
        if direction is None:
            if highs[i] - lows[ext_bar] >= threshold:
                direction, ext_bar = "up", i
            elif highs[ext_bar] - lows[i] >= threshold:
                direction, ext_bar = "down", i
            continue
        if direction == "up":
            if highs[i] >= highs[ext_bar]:
                ext_bar = i
            elif highs[ext_bar] - closes[i] >= threshold:
                pivots.append(emit(ext_bar, "high", prev_price, True))
                prev_price, direction, ext_bar = highs[ext_bar], "down", i
        else:
            if lows[i] <= lows[ext_bar]:
                ext_bar = i
            elif closes[i] - lows[ext_bar] >= threshold:
                pivots.append(emit(ext_bar, "low", prev_price, True))
                prev_price, direction, ext_bar = lows[ext_bar], "up", i

    # The running extreme has not retraced far enough to be a pivot yet. It is
    # reported so a caller can see the developing swing, and flagged so nobody
    # treats it as settled.
    if direction is not None:
        pivots.append(emit(ext_bar, "high" if direction == "up" else "low",
                           prev_price, False))
    return pivots
