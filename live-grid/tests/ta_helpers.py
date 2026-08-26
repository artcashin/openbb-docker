"""Shared loaders for TA tests."""

from pathlib import Path

import polars as pl

FIXTURE = Path(__file__).parent / "fixtures" / "ohlcv.csv"


def fixture_frame() -> pl.DataFrame:
    """The committed 300-bar fixture, typed the way the engine expects."""
    return pl.read_csv(FIXTURE).with_columns([
        pl.col("date").str.to_date(),
        pl.col(["open", "high", "low", "close", "adj_close", "volume"]).cast(pl.Float64),
    ])
