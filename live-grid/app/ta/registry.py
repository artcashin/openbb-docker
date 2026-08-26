"""Indicator definitions.

Every entry states its own conventions rather than inheriting a library's.
That is not pedantry: pandas_ta and EODHD *both* return slow %K under the name
"k" (spec S4, S6), and EODHD computes RSI and Bollinger on adjusted close but
ATR on raw OHLC (spec S5). An indicator that does not say which it means is an
indicator that is silently wrong somewhere.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

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
    iterative: bool = False
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
    wilder = {"alpha": 1 / n, "adjust": False, "ignore_nulls": True}
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

# --- Weighted moving average ------------------------------------------------

register(Indicator(
    name="wma", label="WMA", params={"period": 50}, pane="price",
    price_basis="adjusted",
    convention="Linearly weighted mean of adjusted close; weight i = i for i in 1..period.",
    deps=lambda p: [],
    build=lambda p, b: [
        pl.col(price_col("adjusted"))
          .rolling_mean(p["period"],
                        weights=[float(i) for i in range(1, p["period"] + 1)])
          .alias("wma"),
    ],
    render={"wma": _line("#56b6c2")},
    eodhd=EodhdMap("wma", {"period": "period"}, {"wma": "wma"}, "adjusted",
                   "EODHD wma is adjusted close."),
))

# --- Keltner channels -------------------------------------------------------


def _keltner_build(p: dict, b: dict[str, Base]) -> list[pl.Expr]:
    mid = pl.col(f"ewm:adj_close:{p['period']}")
    atr = pl.col("tr:raw:0").ewm_mean(
        alpha=1 / p["atr_period"], adjust=False, ignore_nulls=True
    )
    return [mid.alias("kc_mid"),
            (mid + p["mult"] * atr).alias("kc_up"),
            (mid - p["mult"] * atr).alias("kc_lo")]


register(Indicator(
    name="keltner", label="Keltner Channels",
    params={"period": 20, "mult": 2.0, "atr_period": 10}, pane="price",
    price_basis="adjusted",
    convention="EMA(period) of adjusted close +/- mult * Wilder ATR(atr_period) of raw OHLC.",
    deps=lambda p: [Base("ewm", price_col("adjusted"), p["period"]), Base("tr", "raw", 0)],
    build=_keltner_build,
    render={"kc_mid": _line("#9aa0a6"), "kc_up": _line("#61afef"),
            "kc_lo": _line("#61afef")},
))

# --- Price channels (Donchian) ---------------------------------------------

register(Indicator(
    name="donchian", label="Price Channels", params={"period": 20}, pane="price",
    price_basis="raw",
    convention="Highest high and lowest low of raw OHLC over `period` bars; mid is their mean.",
    deps=lambda p: [Base("max", "high", p["period"]), Base("min", "low", p["period"])],
    build=lambda p, b: [
        pl.col(f"max:high:{p['period']}").alias("dc_up"),
        pl.col(f"min:low:{p['period']}").alias("dc_lo"),
        ((pl.col(f"max:high:{p['period']}") + pl.col(f"min:low:{p['period']}")) / 2)
        .alias("dc_mid"),
    ],
    render={"dc_up": _line("#98c379"), "dc_lo": _line("#98c379"),
            "dc_mid": _line("#9aa0a6")},
))

# --- VWAP -------------------------------------------------------------------

register(Indicator(
    name="vwap", label="VWAP", params={}, pane="price", price_basis="raw",
    convention=(
        "Cumulative typical-price-weighted mean over the whole window "
        "(not session-anchored). Typical price = (H+L+C)/3 on raw OHLC."
    ),
    deps=lambda p: [],
    build=lambda p, b: [
        (((pl.col("high") + pl.col("low") + pl.col("close")) / 3 * pl.col("volume")).cum_sum()
         / pl.col("volume").cum_sum()).alias("vwap"),
    ],
    render={"vwap": _line("#d19a66")},
))

# --- Volume -----------------------------------------------------------------

register(Indicator(
    name="volume", label="Volume", params={}, pane="own", price_basis="raw",
    convention="Raw bar volume, drawn as bars.",
    deps=lambda p: [],
    build=lambda p, b: [pl.col("volume").alias("volume_bar")],
    render={"volume_bar": {"type": "bar", "color": "#5c6370"}},
))

# --- MACD -------------------------------------------------------------------


def _macd_build(p: dict, b: dict[str, Base]) -> list[pl.Expr]:
    line = pl.col(f"ewm:adj_close:{p['fast']}") - pl.col(f"ewm:adj_close:{p['slow']}")
    signal = line.ewm_mean(span=p["signal"], adjust=False)
    return [line.alias("macd"), signal.alias("macd_signal"),
            (line - signal).alias("macd_hist")]


register(Indicator(
    name="macd", label="MACD", params={"fast": 12, "slow": 26, "signal": 9},
    pane="own", price_basis="adjusted", guides=[0.0],
    convention="EMA(fast) - EMA(slow) on adjusted close; signal is EMA(signal) of that line.",
    deps=lambda p: [Base("ewm", price_col("adjusted"), p["fast"]),
                    Base("ewm", price_col("adjusted"), p["slow"])],
    build=_macd_build,
    render={"macd": _line("#61afef"), "macd_signal": _line("#e06c75"),
            "macd_hist": {"type": "bar", "color": "#5c6370"}},
    eodhd=EodhdMap("macd", {"fast_period": "fast", "slow_period": "slow",
                            "signal_period": "signal"},
                   {"macd": "macd", "macd_signal": "macd_signal",
                    "macd_hist": "macd_hist"},
                   "adjusted", "EODHD macd is adjusted close."),
))

# --- Stochastic oscillator --------------------------------------------------


def _stoch_build(p: dict, b: dict[str, Base]) -> list[pl.Expr]:
    hh = pl.col(f"max:high:{p['k']}")
    ll = pl.col(f"min:low:{p['k']}")
    raw = 100 * (pl.col("close") - ll) / (hh - ll)
    k = raw if p["smooth_k"] <= 1 else raw.rolling_mean(p["smooth_k"])
    # A flat window makes hh == ll and the ratio 0/0. Null, not NaN.
    return [k.fill_nan(None).alias("stoch_k"),
            k.rolling_mean(p["d"]).fill_nan(None).alias("stoch_d")]


register(Indicator(
    name="stoch", label="Stochastic", params={"k": 14, "smooth_k": 1, "d": 3},
    pane="own", price_basis="raw", guides=[20.0, 80.0],
    convention=(
        "smooth_k=1 is FAST %K, 3 is SLOW, anything else is FULL. Raw OHLC. "
        "EODHD and pandas_ta both return SLOW under the bare name 'k' "
        "(spec S4, S6) -- this default is fast, and stated."
    ),
    deps=lambda p: [Base("max", "high", p["k"]), Base("min", "low", p["k"])],
    build=_stoch_build,
    render={"stoch_k": _line("#61afef"), "stoch_d": _line("#e06c75")},
    eodhd=EodhdMap("stochastic",
                   {"period": "k", "slow_kperiod": "smooth_k", "slow_dperiod": "d"},
                   {"k_values": "stoch_k", "d_values": "stoch_d"}, "raw",
                   "EODHD returns SLOW %K (smooth_k=3) on raw close (spec S6)."),
))

# --- Stochastic RSI ---------------------------------------------------------


def _stochrsi_build(p: dict, b: dict[str, Base]) -> list[pl.Expr]:
    n = p["period"]
    delta = pl.col(price_col("adjusted")).diff()
    wilder = {"alpha": 1 / n, "adjust": False, "ignore_nulls": True}
    gain = pl.when(delta > 0).then(delta).otherwise(0.0).ewm_mean(**wilder)
    loss = pl.when(delta < 0).then(-delta).otherwise(0.0).ewm_mean(**wilder)
    rsi = 100 - 100 / (1 + gain / loss)
    lo, hi = rsi.rolling_min(p["stoch_period"]), rsi.rolling_max(p["stoch_period"])
    # Two 0/0 routes here: the inline RSI at bar 0, and hi == lo on a flat
    # window. Both resolve to null.
    return [(100 * (rsi - lo) / (hi - lo)).fill_nan(None).alias("stochrsi")]


register(Indicator(
    name="stochrsi", label="StochRSI",
    params={"period": 14, "stoch_period": 14}, pane="own",
    price_basis="adjusted", guides=[20.0, 80.0],
    convention="Stochastic of Wilder RSI(period) over stoch_period bars, scaled 0-100, on adjusted close.",
    deps=lambda p: [],
    build=_stochrsi_build,
    render={"stochrsi": _line("#c678dd")},
    eodhd=EodhdMap("stochrsi", {"period": "period"}, {"stochrsi": "stochrsi"},
                   "adjusted", "EODHD stochrsi; verify scaling in the parity test."),
))

# --- ADX and directional movement -------------------------------------------


def _adx_build(p: dict, b: dict[str, Base]) -> list[pl.Expr]:
    n = p["period"]
    wilder = {"alpha": 1 / n, "adjust": False, "ignore_nulls": True}
    up = pl.col("high").diff()
    down = -pl.col("low").diff()
    plus_dm = pl.when((up > down) & (up > 0)).then(up).otherwise(0.0)
    minus_dm = pl.when((down > up) & (down > 0)).then(down).otherwise(0.0)
    atr = pl.col("tr:raw:0").ewm_mean(**wilder)
    di_plus = 100 * plus_dm.ewm_mean(**wilder) / atr
    di_minus = 100 * minus_dm.ewm_mean(**wilder) / atr
    dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus)
    # Zero ATR (a flat window) makes both DI undefined, and DX is then 0/0.
    return [dx.ewm_mean(**wilder).fill_nan(None).alias("adx"),
            di_plus.fill_nan(None).alias("di_plus"),
            di_minus.fill_nan(None).alias("di_minus")]


register(Indicator(
    name="adx", label="ADX / DMI", params={"period": 14}, pane="own",
    price_basis="raw", guides=[20.0, 25.0],
    convention=(
        "Wilder's ADX on raw OHLC: +DM/-DM smoothed with alpha=1/period, "
        "divided by Wilder ATR, then DX smoothed again."
    ),
    deps=lambda p: [Base("tr", "raw", 0)],
    build=_adx_build,
    render={"adx": _line("#e5c07b"), "di_plus": _line("#98c379"),
            "di_minus": _line("#e06c75")},
    eodhd=EodhdMap("adx", {"period": "period"}, {"adx": "adx"}, "raw",
                   "EODHD adx is raw OHLC; +DI/-DI come from function=dmi."),
))

# --- Commodity channel index ------------------------------------------------


def _cci_build(p: dict, b: dict[str, Base]) -> list[pl.Expr]:
    n = p["period"]
    tp = (pl.col("high") + pl.col("low") + pl.col("close")) / 3
    sma_tp = tp.rolling_mean(n)
    # Mean absolute deviation about the rolling mean. Polars has no rolling MAD,
    # so this is the explicit form rather than a clever one.
    mad = (tp - sma_tp).abs().rolling_mean(n)
    # MAD is zero on a flat window, giving 0/0.
    return [((tp - sma_tp) / (0.015 * mad)).fill_nan(None).alias("cci")]


register(Indicator(
    name="cci", label="CCI", params={"period": 20}, pane="own",
    price_basis="raw", guides=[-100.0, 100.0],
    convention=(
        "(typical price - SMA) / (0.015 * mean absolute deviation), raw OHLC. "
        "MAD is taken about the rolling mean, not the rolling median."
    ),
    deps=lambda p: [],
    build=_cci_build,
    render={"cci": _line("#d19a66")},
    eodhd=EodhdMap("cci", {"period": "period"}, {"cci": "cci"}, "raw",
                   "EODHD cci is raw OHLC."),
))

# --- Williams %R ------------------------------------------------------------

register(Indicator(
    name="willr", label="Williams %R", params={"period": 14}, pane="own",
    price_basis="raw", guides=[-80.0, -20.0],
    convention="-100 * (highest high - close) / (highest high - lowest low), raw OHLC.",
    deps=lambda p: [Base("max", "high", p["period"]), Base("min", "low", p["period"])],
    build=lambda p, b: [
        (-100 * (pl.col(f"max:high:{p['period']}") - pl.col("close"))
         / (pl.col(f"max:high:{p['period']}") - pl.col(f"min:low:{p['period']}")))
        .fill_nan(None)  # hh == ll on a flat window
        .alias("willr"),
    ],
    render={"willr": _line("#56b6c2")},
))

# --- Rate of change ---------------------------------------------------------

register(Indicator(
    name="roc", label="Rate of Change", params={"period": 12}, pane="own",
    price_basis="adjusted", guides=[0.0],
    convention="Percent change of adjusted close over `period` bars.",
    deps=lambda p: [],
    build=lambda p, b: [
        (pl.col(price_col("adjusted")).pct_change(p["period"]) * 100).alias("roc"),
    ],
    render={"roc": _line("#98c379")},
))

# --- On balance volume ------------------------------------------------------

register(Indicator(
    name="obv", label="On Balance Volume", params={}, pane="own",
    price_basis="adjusted",
    convention="Running sum of signed volume; sign from the adjusted close change.",
    deps=lambda p: [],
    build=lambda p, b: [
        (pl.col(price_col("adjusted")).diff().sign() * pl.col("volume"))
        .cum_sum().alias("obv"),
    ],
    render={"obv": _line("#61afef")},
))

# --- Standard deviation -----------------------------------------------------

register(Indicator(
    name="stddev", label="Standard Deviation", params={"period": 20}, pane="own",
    price_basis="adjusted",
    convention="Population standard deviation (ddof=0) of adjusted close.",
    deps=lambda p: [Base("std", price_col("adjusted"), p["period"])],
    build=lambda p, b: [pl.col(f"std:adj_close:{p['period']}").alias("stddev")],
    render={"stddev": _line("#c678dd")},
    eodhd=EodhdMap("stddev", {"period": "period"}, {"stddev": "stddev"},
                   "adjusted", "EODHD stddev is adjusted close."),
))

# --- %B and BandWidth: both reuse the Bollinger bases ------------------------

register(Indicator(
    name="pct_b", label="%B", params={"period": 20, "k": 2.0}, pane="own",
    price_basis="adjusted", guides=[0.0, 1.0],
    convention="(close - lower) / (upper - lower), on adjusted close. Shares Bollinger's bases exactly.",
    deps=lambda p: [Base("sma", price_col("adjusted"), p["period"]),
                    Base("std", price_col("adjusted"), p["period"])],
    build=lambda p, b: [
        ((pl.col(price_col("adjusted"))
          - (pl.col(f"sma:adj_close:{p['period']}")
             - p["k"] * pl.col(f"std:adj_close:{p['period']}")))
         / (2 * p["k"] * pl.col(f"std:adj_close:{p['period']}")))
        .fill_nan(None)  # zero stddev on a flat window
        .alias("pct_b"),
    ],
    render={"pct_b": _line("#e5c07b")},
))

register(Indicator(
    name="bandwidth", label="Bollinger BandWidth",
    params={"period": 20, "k": 2.0}, pane="own", price_basis="adjusted",
    convention="(upper - lower) / middle * 100, on adjusted close. Shares Bollinger's bases exactly.",
    deps=lambda p: [Base("sma", price_col("adjusted"), p["period"]),
                    Base("std", price_col("adjusted"), p["period"])],
    build=lambda p, b: [
        (2 * p["k"] * pl.col(f"std:adj_close:{p['period']}")
         / pl.col(f"sma:adj_close:{p['period']}") * 100).alias("bandwidth"),
    ],
    render={"bandwidth": _line("#d19a66")},
))

# --- Parabolic SAR: a recurrence, handled by iterative.py --------------------

register(Indicator(
    name="sar", label="Parabolic SAR",
    params={"acceleration": 0.02, "maximum": 0.2}, pane="price",
    price_basis="raw", iterative=True,
    convention=(
        "Wilder's Parabolic SAR on raw OHLC. Path-dependent: the value at each "
        "bar carries the extreme point and acceleration factor forward, so it "
        "cannot be a Polars expression."
    ),
    deps=lambda p: [],
    build=lambda p, b: [],  # produced by iterative.parabolic_sar
    render={"sar": {"type": "scatter", "mode": "markers", "color": "#abb2bf"}},
    eodhd=EodhdMap("sar", {"acceleration": "acceleration", "maximum": "maximum"},
                   {"sar": "sar"}, "raw", "EODHD sar is raw OHLC."),
))
