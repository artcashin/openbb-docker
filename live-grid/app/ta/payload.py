"""One builder, two routes.

`/ta_chart` and `/ta_chart_ws` both come through here. That is the reason the
live chart cannot disagree with the static one: there is no second
implementation to drift (spec D9).
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from app.ta.figure import build_ta_figure
from app.ta.macros import load_all
from app.ta.panes import Pane, all_reqs, assign
from app.ta.registry import Req, resolve
from app.ta.sources import LocalSource

_NUMERIC = ("period", "k", "d", "fast", "slow", "signal", "smooth_k",
            "stoch_period", "atr_period", "mult", "acceleration", "maximum")


@dataclass(frozen=True)
class ChartParams:
    symbol: str = "AAPL"
    interval: str = "1d"
    source: str = "local"
    macro: str = "none"
    indicators: str = ""
    start: str | None = None
    end: str | None = None
    provider: str = "kdb"


def _coerce(key: str, raw: str):
    if key not in _NUMERIC:
        return raw
    value = float(raw)
    return int(value) if value.is_integer() and key != "k" else value


def parse_indicators(raw: str) -> list[Req]:
    """`"rsi:period=14,sma:period=200"` -> resolved requests."""
    reqs: list[Req] = []
    for chunk in (raw or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        name, *pairs = chunk.split(":")
        params = {}
        for pair in pairs:
            if "=" not in pair:
                continue
            key, value = pair.split("=", 1)
            params[key.strip()] = _coerce(key.strip(), value.strip())
        reqs.append(resolve(name.strip(), **params))
    return reqs


def bars_to_frame(bars: list[dict]) -> pl.DataFrame:
    """Bars from build_series into the frame the engine expects.

    Tick-derived bars have no adjusted close, so raw close stands in. That is
    correct rather than a fudge: intraday ticks are already unadjusted, and the
    alternative is a null column that silently voids every adjusted indicator.
    """
    schema = {"date": pl.Date, "open": pl.Float64, "high": pl.Float64,
              "low": pl.Float64, "close": pl.Float64, "adj_close": pl.Float64,
              "volume": pl.Float64}
    if not bars:
        return pl.DataFrame(schema=schema)
    rows = []
    for bar in bars:
        close = bar.get("close")
        # `.get(key, default)` fires only when the key is ABSENT. Providers
        # return an explicit null adjusted_close for instruments that have no
        # adjustment data -- indices, forex, crypto -- and that must fall back
        # too. Without this, all 13 price_basis="adjusted" indicators render
        # blank on those symbols, with no error anywhere to explain it.
        adjusted = bar.get("adjusted_close")
        rows.append({
            "date": str(bar.get("date"))[:10],
            "open": bar.get("open"), "high": bar.get("high"),
            "low": bar.get("low"), "close": close,
            "adj_close": close if adjusted is None else adjusted,
            "volume": bar.get("volume") or 0.0,
        })
    return pl.DataFrame(rows).with_columns([
        pl.col("date").str.to_date(),
        pl.col(["open", "high", "low", "close", "adj_close", "volume"])
          .cast(pl.Float64, strict=False),
    ])


async def build_payload(
    params: ChartParams, frame: pl.DataFrame, eodhd_source=None,
) -> tuple[dict, list[Pane], pl.DataFrame]:
    """The figure, its panes, and the computed frame."""
    macro = None
    if params.macro and params.macro != "none":
        macros = load_all()
        if params.macro not in macros:
            raise KeyError(f"no such macro {params.macro!r}")
        macro = macros[params.macro]

    panes = assign(macro, parse_indicators(params.indicators))
    reqs = all_reqs(panes)

    annotations: list = []
    if params.source == "eodhd" and eodhd_source is not None and reqs:
        last_closed = str(frame["date"][-1]) if frame.height else ""
        result = await eodhd_source.series(
            frame, reqs, params.symbol, params.interval, last_closed
        )
        computed, annotations = result.frame, result.annotations
    else:
        computed = LocalSource().series(frame, reqs).frame

    subtitle = f"{params.interval} · {params.source}"
    figure = build_ta_figure(params.symbol, computed, panes, annotations, subtitle)
    return figure, panes, computed
