"""Roll ticks into OHLCV bars, and roll those bars up further.

The session filter is the single largest lever on any comparison against a
consolidated reference feed: FirstRate ticks include extended hours, and most
reference bars do not. It is a parameter, never a hidden default.
"""

from __future__ import annotations

from datetime import time

import pandas as pd

EASTERN = "America/New_York"
BAR_COLUMNS = ["open", "high", "low", "close", "volume"]

REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)

SESSIONS = ("regular", "all")

# pandas resample rules for the intervals a reference source may hand back.
_RULES = {
    "1m": "1min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "1h": "1h",
    "1d": "1D",
}

_AGG = {
    "open": ("open", "first"),
    "high": ("high", "max"),
    "low": ("low", "min"),
    "close": ("close", "last"),
    "volume": ("volume", "sum"),
}


class SessionError(ValueError):
    """An unknown --session value."""


def _empty_bars() -> pd.DataFrame:
    return pd.DataFrame(
        {c: pd.Series(dtype="float64") for c in BAR_COLUMNS},
        index=pd.DatetimeIndex([], tz="UTC", name=None),
    )


def to_minute_bars(trades: pd.DataFrame, session: str = "regular") -> pd.DataFrame:
    """Aggregate trade ticks into 1-minute OHLCV bars.

    `trades` must carry a tz-aware UTC index and `price`/`volume` columns.
    Bars are left-closed and left-labeled: the 09:30 bar covers [09:30, 09:31).
    Minutes with no trades are dropped rather than forward-filled -- an absent
    bar and a flat bar are different facts, and the comparison counts them
    differently.
    """
    if session not in SESSIONS:
        raise SessionError(
            f"unknown session {session!r}; expected one of {', '.join(SESSIONS)}"
        )
    if trades.empty:
        return _empty_bars()

    df = trades
    if session == "regular":
        local = df.index.tz_convert(EASTERN)
        # 09:30 inclusive, 16:00 exclusive -- 390 one-minute bars.
        mask = (local.time >= REGULAR_OPEN) & (local.time < REGULAR_CLOSE)
        df = df[mask]
        if df.empty:
            return _empty_bars()

    bars = df.resample("1min", label="left", closed="left").agg(
        open=("price", "first"),
        high=("price", "max"),
        low=("price", "min"),
        close=("price", "last"),
        volume=("volume", "sum"),
    )
    return bars.dropna(subset=["open"])[BAR_COLUMNS]


def aggregate(bars: pd.DataFrame, interval: str) -> pd.DataFrame:
    """Roll 1-minute bars up to a coarser interval."""
    if interval not in _RULES:
        raise ValueError(
            f"unsupported interval {interval!r}; expected one of {', '.join(_RULES)}"
        )
    if interval == "1m" or bars.empty:
        return bars

    if interval == "1d":
        # A US equity session spans one Eastern calendar day but can straddle
        # nothing in UTC terms -- still, bucket in Eastern so the daily bar
        # matches what a daily reference feed calls that date.
        local = bars.tz_convert(EASTERN)
        out = local.resample("1D", label="left", closed="left").agg(**_AGG)
        return out.dropna(subset=["open"])[BAR_COLUMNS].tz_convert("UTC")

    out = bars.resample(_RULES[interval], label="left", closed="left").agg(**_AGG)
    return out.dropna(subset=["open"])[BAR_COLUMNS]
