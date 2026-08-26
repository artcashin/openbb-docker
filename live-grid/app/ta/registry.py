"""Indicator definitions.

Every entry states its own conventions rather than inheriting a library's.
That is not pedantry: pandas_ta and EODHD *both* return slow %K under the name
"k" (spec S4, S6), and EODHD computes RSI and Bollinger on adjusted close but
ATR on raw OHLC (spec S5). An indicator that does not say which it means is an
indicator that is silently wrong somewhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import polars as pl

from app.ta.exprs import Base, price_col


@dataclass(frozen=True)
class Req:
    """One indicator with its parameters fully resolved."""

    name: str
    params: dict[str, Any]


@dataclass(frozen=True)
class EodhdMap:
    """How to ask EODHD for this indicator and read its answer.

    `params` maps EODHD query parameter -> our parameter name.
    `fields`  maps EODHD response field  -> our output column name.
    """

    function: str
    params: dict[str, str]
    fields: dict[str, str]
    price_basis: str
    note: str


@dataclass(frozen=True)
class Indicator:
    name: str
    label: str
    params: dict[str, Any]
    pane: str                      # "price" | "own"
    price_basis: str               # "adjusted" | "raw"
    convention: str
    deps: Callable[[dict], list[Base]]
    build: Callable[[dict, dict[str, Base]], list[pl.Expr]]
    render: dict[str, dict]
    guides: list[float] = field(default_factory=list)
    repaints: bool = False
    eodhd: EodhdMap | None = None


REGISTRY: dict[str, Indicator] = {}


def register(ind: Indicator) -> Indicator:
    if ind.name in REGISTRY:
        raise ValueError(f"indicator {ind.name!r} is already registered")
    REGISTRY[ind.name] = ind
    return ind


def get(name: str) -> Indicator:
    try:
        return REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown indicator {name!r}") from None


def resolve(name: str, **overrides: Any) -> Req:
    """Defaults from the registry, overridden by keyword. Unknown keys raise.

    `style` is accepted for every indicator: it is per-series presentation
    carried from a macro, not an indicator parameter, so it is not in
    `Indicator.params`.
    """
    ind = get(name)
    style = overrides.pop("style", None)
    for key in overrides:
        if key not in ind.params:
            raise ValueError(
                f"unknown parameter {key!r} for {name!r}; "
                f"expected one of {sorted(ind.params)}"
            )
    return Req(name, {**ind.params, "style": style, **overrides})


def _line(color: str | None = None) -> dict:
    return {"type": "line", "color": color}


# --- Simple moving average -------------------------------------------------

register(Indicator(
    name="sma", label="SMA", params={"period": 50}, pane="price",
    price_basis="adjusted",
    convention="Arithmetic mean of adjusted close over `period` bars.",
    deps=lambda p: [Base("sma", price_col("adjusted"), p["period"])],
    build=lambda p, b: [
        pl.col(b[f"sma:adj_close:{p['period']}"].key).alias("sma"),
    ],
    render={"sma": _line("#4c9be8")},
    eodhd=EodhdMap("sma", {"period": "period"}, {"sma": "sma"}, "adjusted",
                   "EODHD sma is computed on adjusted close."),
))

# --- Exponential moving average --------------------------------------------

register(Indicator(
    name="ema", label="EMA", params={"period": 50}, pane="price",
    price_basis="adjusted",
    convention="EMA with alpha = 2/(period+1), adjust=False, on adjusted close.",
    deps=lambda p: [Base("ewm", price_col("adjusted"), p["period"])],
    build=lambda p, b: [
        pl.col(b[f"ewm:adj_close:{p['period']}"].key).alias("ema"),
    ],
    render={"ema": _line("#e8b923")},
    eodhd=EodhdMap("ema", {"period": "period"}, {"ema": "ema"}, "adjusted",
                   "EODHD ema is computed on adjusted close."),
))

# --- Relative strength index -----------------------------------------------


def _rsi_build(p: dict, b: dict[str, Base]) -> list[pl.Expr]:
    n = p["period"]
    col = price_col("adjusted")
    delta = pl.col(col).diff()
    gain = pl.when(delta > 0).then(delta).otherwise(0.0)
    loss = pl.when(delta < 0).then(-delta).otherwise(0.0)
    wilder = dict(alpha=1 / n, adjust=False, ignore_nulls=True)
    rs = gain.ewm_mean(**wilder) / loss.ewm_mean(**wilder)
    # Bar 0 has no previous close, so gain and loss are both 0 and rs is 0/0.
    # Null is what every rolling indicator already uses for its warmup; NaN
    # would be a second, inconsistent spelling of "undefined". This also
    # covers a genuinely flat window, e.g. a halted ticker.
    return [(100 - 100 / (1 + rs)).fill_nan(None).alias("rsi")]


register(Indicator(
    name="rsi", label="RSI", params={"period": 14}, pane="own",
    price_basis="adjusted", guides=[30.0, 70.0],
    convention=(
        "Wilder's RSI: gains and losses smoothed with alpha=1/period "
        "(not span=period), on adjusted close. Matches EODHD to 1.5e-5."
    ),
    deps=lambda p: [],  # its smoothing is of derived gain/loss, not a shared Base
    build=_rsi_build,
    render={"rsi": _line("#8ed081")},
    eodhd=EodhdMap("rsi", {"period": "period"}, {"rsi": "rsi"}, "adjusted",
                   "EODHD rsi matches Wilder-on-adjusted-close at 1.5e-5 (spec S5)."),
))

# --- Bollinger bands --------------------------------------------------------


def _bbands_build(p: dict, b: dict[str, Base]) -> list[pl.Expr]:
    n, k = p["period"], p["k"]
    mid = pl.col(f"sma:adj_close:{n}")
    sd = pl.col(f"std:adj_close:{n}")
    return [
        mid.alias("bb_mid"),
        (mid + k * sd).alias("bb_up"),
        (mid - k * sd).alias("bb_lo"),
    ]


register(Indicator(
    name="bbands", label="Bollinger Bands", params={"period": 20, "k": 2.0},
    pane="price", price_basis="adjusted",
    convention=(
        "Middle = SMA(period) of adjusted close. Bands = mid +/- k * population "
        "standard deviation (ddof=0). Matches EODHD to 1.2e-7."
    ),
    deps=lambda p: [Base("sma", price_col("adjusted"), p["period"]),
                    Base("std", price_col("adjusted"), p["period"])],
    build=_bbands_build,
    render={"bb_mid": _line("#9aa0a6"), "bb_up": _line("#c678dd"),
            "bb_lo": _line("#c678dd")},
    eodhd=EodhdMap("bbands", {"period": "period"},
                   {"uband": "bb_up", "mband": "bb_mid", "lband": "bb_lo"},
                   "adjusted",
                   "EODHD bbands is adjusted-close with ddof=0 (spec S5)."),
))

# --- Average true range -----------------------------------------------------

register(Indicator(
    name="atr", label="ATR", params={"period": 14}, pane="own",
    price_basis="raw",
    convention=(
        "Wilder's average of true range on RAW OHLC. EODHD's atr matches raw, "
        "not adjusted -- feeding adjusted close here is a 96% error (spec S5)."
    ),
    deps=lambda p: [Base("tr", "raw", 0)],
    build=lambda p, b: [
        pl.col("tr:raw:0")
          .ewm_mean(alpha=1 / p["period"], adjust=False, ignore_nulls=True)
          .alias("atr"),
    ],
    render={"atr": _line("#e06c75")},
    eodhd=EodhdMap("atr", {"period": "period"}, {"atr": "atr"}, "raw",
                   "EODHD atr is raw OHLC (spec S5)."),
))
