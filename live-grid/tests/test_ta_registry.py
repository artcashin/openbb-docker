"""Registry shape, convention pinning, and the first five indicators."""

import polars as pl
import pytest

from app.ta.compute import compute
from app.ta.registry import REGISTRY, get, resolve

from tests.ta_helpers import fixture_frame


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
    base = compute(df, [resolve("rsi", period=14)])["rsi"].to_list()
    perturbed = compute(
        df.with_columns(pl.col("close") * 1.5), [resolve("rsi", period=14)]
    )["rsi"].to_list()
    assert base == perturbed


def test_rsi_bounds_to_zero_hundred():
    """RSI values always fall in [0, 100]."""
    out = compute(fixture_frame(), [resolve("rsi", period=14)])
    vals = [v for v in out["rsi"].to_list() if v is not None]
    assert len(vals) > 250
    assert min(vals) >= 0.0 and max(vals) <= 100.0


def test_atr_reads_raw_ohlc_and_ignores_adjusted_close():
    """Perturbing adj_close must not move ATR. Bounds checks cannot show this."""
    df = fixture_frame()
    base = compute(df, [resolve("atr", period=14)])["atr"].to_list()
    perturbed = compute(
        df.with_columns(pl.col("adj_close") * 1.5), [resolve("atr", period=14)]
    )["atr"].to_list()
    assert base == perturbed


def test_bbands_emits_three_ordered_bands():
    out = compute(fixture_frame(), [resolve("bbands", period=20, k=2.0)])
    row = out.row(100, named=True)
    assert row["bb_lo"] < row["bb_mid"] < row["bb_up"]


def test_sma_and_ema_land_on_the_price_pane():
    assert get("sma").pane == "price" and get("ema").pane == "price"


def test_rsi_carries_thirty_seventy_guides():
    assert get("rsi").guides == [30.0, 70.0]
