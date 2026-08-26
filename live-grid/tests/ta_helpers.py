"""Shared loaders for TA tests."""

from pathlib import Path

import polars as pl

from app.ta.registry import col_suffix, get, resolve

FIXTURE = Path(__file__).parent / "fixtures" / "ohlcv.csv"


def fixture_frame() -> pl.DataFrame:
    """The committed 300-bar fixture, typed the way the engine expects."""
    return pl.read_csv(FIXTURE).with_columns([
        pl.col("date").str.to_date(),
        pl.col(["open", "high", "low", "close", "adj_close", "volume"]).cast(pl.Float64),
    ])


def cols(req) -> list[str]:
    """The columns this request lands in, suffixed by its parameters."""
    return [c + col_suffix(req) for c in get(req.name).render]


def col(name: str, base: str | None = None, **params) -> str:
    """One indicator output column: `col("sma", period=200)` -> sma|period=200."""
    return (base or next(iter(get(name).render))) + col_suffix(resolve(name, **params))
