"""The eleven next-tier indicators added at v12.0.0."""

import polars as pl
import pytest

from app.ta.compute import compute
from app.ta.registry import REGISTRY, resolve
from tests.ta_helpers import col, fixture_frame


def approx(value: float, rel: float = 1e-9):
    """Local alias. NOT named pytest_approx -- pytest collects any module-level
    name starting with `pytest_` as a hook implementation and errors on it."""
    return pytest.approx(value, rel=rel)


def _wma(values: list[float], length: int) -> float:
    weights = list(range(1, length + 1))
    window = values[-length:]
    return sum(v * w for v, w in zip(window, weights)) / sum(weights)


def test_hma_matches_a_hand_rolled_wma_of_wma():
    df = fixture_frame()
    out = compute(df, [resolve("hma", period=9)])[col("hma", period=9)].to_list()
    price = df["adj_close"].to_list()
    # HMA(9): WMA(2*WMA(p,4) - WMA(p,9), 3) -- round(9/2)=4, round(sqrt(9))=3
    raw = [
        2 * _wma(price[: i + 1], 4) - _wma(price[: i + 1], 9) if i >= 8 else None
        for i in range(len(price))
    ]
    expected = _wma([r for r in raw[-3:]], 3)
    assert out[-1] == approx(expected)


def test_hma_reads_adjusted_close_not_raw():
    df = fixture_frame()
    name = col("hma", period=9)
    base = compute(df, [resolve("hma", period=9)])[name].to_list()
    perturbed = compute(df.with_columns(pl.col("close") * 1.5),
                        [resolve("hma", period=9)])[name].to_list()
    assert base == perturbed


def test_trix_is_the_percent_change_of_a_triple_ema():
    df = fixture_frame()
    n = 18
    alpha = 2 / (n + 1)
    price = pl.col("adj_close")
    ema3 = (price.ewm_mean(alpha=alpha, adjust=False, ignore_nulls=True)
                 .ewm_mean(alpha=alpha, adjust=False, ignore_nulls=True)
                 .ewm_mean(alpha=alpha, adjust=False, ignore_nulls=True))
    expected = df.select((100 * ema3.pct_change(1)).alias("e"))["e"].to_list()
    out = compute(df, [resolve("trix", period=n)])[col("trix", period=n)].to_list()
    assert out[-1] == approx(expected[-1])


def test_both_declare_no_eodhd_map():
    for name in ("hma", "trix"):
        assert REGISTRY[name].eodhd is None, name


def test_hma_rounds_its_derived_lengths_to_nearest_int():
    """period=7 -> round(7/2)=4 and round(sqrt(7))=3; a truncating int() would
    give (3, 2) instead. Both lengths diverge from truncation here, and each
    scheme lands on a different final value, so this hand-rolled expectation
    -- built with (4, 3), the same way test_hma_matches_a_hand_rolled_wma_of_wma
    builds (4, 3) for period=9 -- fails if _hma_build ever truncates instead of
    rounding."""
    df = fixture_frame()
    out = compute(df, [resolve("hma", period=7)])[col("hma", period=7)].to_list()
    price = df["adj_close"].to_list()
    raw = [
        2 * _wma(price[: i + 1], 4) - _wma(price[: i + 1], 7) if i >= 6 else None
        for i in range(len(price))
    ]
    expected = _wma([r for r in raw[-3:]], 3)
    assert out[-1] == approx(expected)
