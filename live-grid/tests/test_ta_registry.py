"""Registry shape, convention pinning, and the first five indicators."""

import polars as pl
import pytest

from app.ta.compute import compute
from app.ta.registry import REGISTRY, get, resolve
from tests.ta_helpers import col, fixture_frame


def test_every_registered_indicator_states_its_conventions():
    for name, ind in REGISTRY.items():
        assert ind.price_basis in ("adjusted", "raw"), name
        assert ind.convention.strip(), f"{name} has no pinned convention"
        assert ind.pane in ("price", "own"), name
        assert isinstance(ind.repaints, bool), name


def test_resolve_applies_defaults_then_overrides():
    assert resolve("sma").params["period"] == 50
    assert resolve("sma", period=200).params["period"] == 200


def test_resolve_rejects_an_unknown_parameter():
    with pytest.raises(ValueError, match="unknown parameter 'window'"):
        resolve("sma", window=200)


def test_resolve_accepts_a_style_override_but_still_rejects_unknown_keys():
    assert resolve("sma", style={"color": "#fff"}).params["style"] == {"color": "#fff"}
    assert resolve("sma").params["style"] is None
    with pytest.raises(ValueError, match="unknown parameter 'window'"):
        resolve("sma", window=200)


def test_get_rejects_an_unknown_indicator():
    with pytest.raises(KeyError, match="ichimoku"):
        get("ichimoku")


def test_rsi_reads_adjusted_close_and_ignores_raw_close():
    """The mirror image: perturbing raw close must not move RSI."""
    df = fixture_frame()
    rsi = col("rsi", period=14)
    base = compute(df, [resolve("rsi", period=14)])[rsi].to_list()
    perturbed = compute(
        df.with_columns(pl.col("close") * 1.5), [resolve("rsi", period=14)]
    )[rsi].to_list()
    assert base == perturbed


def test_rsi_bounds_to_zero_hundred():
    """RSI values always fall in [0, 100]."""
    out = compute(fixture_frame(), [resolve("rsi", period=14)])
    vals = [v for v in out[col("rsi", period=14)].to_list() if v is not None]
    assert len(vals) > 250
    assert min(vals) >= 0.0 and max(vals) <= 100.0


def test_atr_reads_raw_ohlc_and_ignores_adjusted_close():
    """Perturbing adj_close must not move ATR. Bounds checks cannot show this."""
    df = fixture_frame()
    atr = col("atr", period=14)
    base = compute(df, [resolve("atr", period=14)])[atr].to_list()
    perturbed = compute(
        df.with_columns(pl.col("adj_close") * 1.5), [resolve("atr", period=14)]
    )[atr].to_list()
    assert base == perturbed


def test_bbands_emits_three_ordered_bands():
    out = compute(fixture_frame(), [resolve("bbands", period=20, k=2.0)])
    row = out.row(100, named=True)
    lo, mid, up = (col("bbands", c, period=20, k=2.0)
                   for c in ("bb_lo", "bb_mid", "bb_up"))
    assert row[lo] < row[mid] < row[up]


def test_sma_and_ema_land_on_the_price_pane():
    assert get("sma").pane == "price" and get("ema").pane == "price"


def test_rsi_carries_thirty_seventy_guides():
    assert get("rsi").guides == [30.0, 70.0]


def test_tier_one_is_twenty_three_indicators_with_twelve_eodhd_maps():
    assert len(REGISTRY) == 23, sorted(REGISTRY)  # 22 tier-1 + avwap
    mapped = [n for n, i in REGISTRY.items() if i.eodhd is not None]
    assert len(mapped) == 12, sorted(mapped)  # cci is local-only (see registry)


def test_resolve_rejects_a_style_that_is_not_a_mapping():
    """`?indicators=sma:style=x` reaches here as a bare string; `{**r, **"x"}`
    is a TypeError, not the 502-with-a-reason every other bad param gets."""
    with pytest.raises(ValueError, match="style must be a mapping"):
        resolve("sma", style="x")


def test_avwap_without_anchor_equals_vwap():
    """No anchor means the window start -- the plain vwap, by construction."""
    out = compute(fixture_frame(), [resolve("vwap"), resolve("avwap")])
    diff = (out[col("vwap")] - out[col("avwap")]).abs().max()
    assert diff < 1e-9


def test_avwap_is_null_before_the_anchor_and_cumulative_after():
    frame = fixture_frame()
    anchor = str(frame["date"][150])
    out = compute(frame, [resolve("avwap", anchor=anchor)])
    series = out[col("avwap", anchor=anchor)]
    assert series[:150].null_count() == 150
    row = frame.row(150, named=True)
    typical = (row["high"] + row["low"] + row["close"]) / 3
    assert abs(series[150] - typical) < 1e-9
    tail = frame[150:]
    manual = (((tail["high"] + tail["low"] + tail["close"]) / 3)
              * tail["volume"]).sum() / tail["volume"].sum()
    assert abs(series[-1] - manual) < 1e-9


def test_avwap_rejects_a_malformed_anchor():
    with pytest.raises(ValueError):
        compute(fixture_frame(), [resolve("avwap", anchor="not-a-time")])
