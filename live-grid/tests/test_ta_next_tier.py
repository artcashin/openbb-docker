"""The eleven next-tier indicators added at v12.0.0."""

import math
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


def test_ao_is_the_gap_between_two_hl2_means():
    df = fixture_frame()
    hl2 = ((pl.col("high") + pl.col("low")) / 2)
    expected = df.select((hl2.rolling_mean(5) - hl2.rolling_mean(34)).alias("e"))["e"][-1]
    assert compute(df, [resolve("ao")])[col("ao")][-1] == approx(expected)


def test_ao_takes_no_parameters():
    with pytest.raises(ValueError, match="unknown parameter 'period'"):
        resolve("ao", period=5)


def test_mfi_is_bounded_and_uses_typical_price_direction():
    df = fixture_frame()
    out = [v for v in compute(df, [resolve("mfi", period=14)])[col("mfi", period=14)]
           if v is not None]
    assert out, "mfi produced nothing"
    assert all(0.0 <= v <= 100.0 for v in out)


def test_mfi_matches_a_hand_computed_value_with_known_direction():
    """Bounds alone can't catch a swapped positive/negative bucket -- a bug
    that flips the ratio still lands in [0, 100]. Flat bars (high == low ==
    close) make typical price equal to close, so the up/down bucketing and
    the expected number below follow straight from the definition.

    Prices: 10, 12, 11, 13, 9 (up, down, up, down from the prior bar).
    Volumes: 100, 200, 100, 300, 100.
    period=3 at the last bar covers indices 2, 3, 4 (rolling_sum(3) trailing):
      positive flow = flow[3]        = 13*300 = 3900   (only idx3 rose)
      negative flow = flow[2]+flow[4] = 11*100 + 9*100 = 2000
      mfi = 100 * 3900 / (3900 + 2000)
    A swapped numerator/denominator would give 100 * 2000 / 5900 instead --
    a different number, so this test catches that inversion.
    """
    prices = [10.0, 12.0, 11.0, 13.0, 9.0]
    volumes = [100.0, 200.0, 100.0, 300.0, 100.0]
    df = pl.DataFrame({
        "date": [f"2026-01-{d:02d}" for d in range(1, 6)],
        "open": prices, "high": prices, "low": prices, "close": prices,
        "adj_close": prices, "volume": volumes, "vwap": prices,
    }).with_columns(pl.col("date").str.to_date())
    out = compute(df, [resolve("mfi", period=3)])[col("mfi", period=3)].to_list()
    expected = 100 * 3900 / (3900 + 2000)
    assert out[-1] == approx(expected)
    assert not math.isnan(out[-1])


def test_mfi_nulls_a_flat_window_rather_than_dividing_by_zero():
    """A window with no typical-price movement has pos+neg == 0."""
    flat = pl.DataFrame({
        "date": [f"2026-01-{d:02d}" for d in range(1, 21)],
        "open": [10.0] * 20, "high": [10.0] * 20, "low": [10.0] * 20,
        "close": [10.0] * 20, "adj_close": [10.0] * 20,
        "volume": [100.0] * 20, "vwap": [10.0] * 20,
    }).with_columns(pl.col("date").str.to_date())
    out = compute(flat, [resolve("mfi", period=14)])[col("mfi", period=14)].to_list()
    assert all(v is None for v in out)


def test_cmf_treats_a_flat_bar_as_zero_flow_not_nan():
    df = fixture_frame().with_columns([
        pl.when(pl.int_range(pl.len()) == 100).then(pl.col("close"))
          .otherwise(pl.col("high")).alias("high"),
        pl.when(pl.int_range(pl.len()) == 100).then(pl.col("close"))
          .otherwise(pl.col("low")).alias("low"),
    ])
    out = compute(df, [resolve("cmf", period=20)])[col("cmf", period=20)].to_list()
    # Assert INSIDE the flat bar's window. A 20-bar window at index 120 spans
    # rows 101-120 and never sees row 100 at all, so asserting there would
    # pass whatever the flat-bar branch did. Index 110 spans 91-110, and
    # index 100's own window spans 81-100.
    assert out[100] is not None, "the flat bar's own window must still resolve"
    assert out[110] is not None, "one flat bar must not poison the window"
    # "not None" alone lets NaN slip through and, worse, a NaN would silently
    # poison every window's rolling_sum -- unlike null, which rolling_sum
    # skips. Both rows must be real numbers, not NaN.
    assert not math.isnan(out[100]), "flat bar produced NaN, not a real number"
    assert not math.isnan(out[110]), "the flat bar's NaN leaked into a later window"


def test_the_three_declare_no_eodhd_map():
    for name in ("ao", "mfi", "cmf"):
        assert REGISTRY[name].eodhd is None, name
