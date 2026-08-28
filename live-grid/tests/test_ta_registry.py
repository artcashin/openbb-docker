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


def test_the_registry_is_twenty_six_indicators_with_twelve_eodhd_maps():
    """22 tier-one indicators plus the four Murphy names computable from OHLCV
    alone (momentum, envelopes, ad, mfi). The EODHD count does NOT move with
    them: none of the four exists on EODHD's function list, so none can claim
    parity. A map appearing here later means someone mapped an indicator their
    endpoint cannot answer."""
    assert len(REGISTRY) == 26, sorted(REGISTRY)
    mapped = [n for n, i in REGISTRY.items() if i.eodhd is not None]
    assert len(mapped) == 12, sorted(mapped)  # cci is local-only (see registry)


def test_resolve_rejects_a_style_that_is_not_a_mapping():
    """`?indicators=sma:style=x` reaches here as a bare string; `{**r, **"x"}`
    is a TypeError, not the 502-with-a-reason every other bad param gets."""
    with pytest.raises(ValueError, match="style must be a mapping"):
        resolve("sma", style="x")


class TestMurphyOhlcvIndicators:
    """The five Murphy names computable from OHLCV alone. Each test pins the
    property that distinguishes the indicator from its nearest neighbour in the
    registry -- the failure these would actually catch is someone 'simplifying'
    one into the other."""

    def test_momentum_is_a_difference_not_a_ratio(self):
        f = compute(fixture_frame(), [resolve("momentum", period=10)])
        m = f[col("momentum", period=10)]
        close = f["adj_close"]
        # Exactly close - close[10], to the last bit.
        assert m[50] == pytest.approx(close[50] - close[40], abs=1e-12)
        # And NOT the ratio form: roc would be a percentage of the same pair.
        roc_like = (close[50] / close[40] - 1.0) * 100.0
        assert m[50] != pytest.approx(roc_like, abs=1e-6)

    def test_momentum_crosses_zero_where_price_returns_to_its_level(self):
        f = compute(fixture_frame(), [resolve("momentum", period=10)])
        m, close = f[col("momentum", period=10)], f["adj_close"]
        for i in range(10, len(m)):
            if m[i] is not None:
                assert (m[i] > 0) == (close[i] > close[i - 10])

    def test_envelope_width_ignores_volatility(self):
        """The property that separates envelopes from bbands. Band width here is
        a fixed fraction of the middle line, whatever the market is doing."""
        f = compute(fixture_frame(), [resolve("envelopes", period=20, pct=2.5)])
        mid = f[col("envelopes", "env_mid", period=20, pct=2.5)]
        up = f[col("envelopes", "env_up", period=20, pct=2.5)]
        lo = f[col("envelopes", "env_lo", period=20, pct=2.5)]
        for i in range(25, len(mid)):
            assert up[i] / mid[i] == pytest.approx(1.025, abs=1e-12)
            assert lo[i] / mid[i] == pytest.approx(0.975, abs=1e-12)

    def test_envelope_pct_is_percent_not_fraction(self):
        f = compute(fixture_frame(), [resolve("envelopes", period=20, pct=10)])
        mid = f[col("envelopes", "env_mid", period=20, pct=10)]
        up = f[col("envelopes", "env_up", period=20, pct=10)]
        assert up[30] / mid[30] == pytest.approx(1.10, abs=1e-12)

    def test_ad_survives_a_bar_whose_high_equals_its_low(self):
        """0/0 on a doji. Contributing 0 keeps the running sum finite; a NaN here
        would poison every later bar, which is the bug worth pinning."""
        df = fixture_frame()
        row = 40
        flat = df.with_columns([
            pl.when(pl.int_range(pl.len()) == row).then(pl.col("close"))
              .otherwise(pl.col("high")).alias("high"),
            pl.when(pl.int_range(pl.len()) == row).then(pl.col("close"))
              .otherwise(pl.col("low")).alias("low"),
        ])
        ad = compute(flat, [resolve("ad")])[col("ad")]
        assert ad[row] is not None and ad[row] == ad[row]           # not NaN
        assert ad[row] == pytest.approx(ad[row - 1], abs=1e-12)      # contributed 0
        assert ad[-1] == ad[-1]                                      # and never poisoned

    def test_ad_is_not_obv(self):
        """obv assigns a bar's whole volume by the sign of the close change; ad
        weights it by where the close landed inside the bar. Same inputs, and
        they must not collapse onto each other."""
        f = compute(fixture_frame(), [resolve("ad"), resolve("obv")])
        a, o = f[col("ad")], f[col("obv")]
        assert any(abs(a[i] - o[i]) > 1e-6 for i in range(20, len(a)))

    def test_mfi_stays_inside_zero_and_one_hundred(self):
        f = compute(fixture_frame(), [resolve("mfi", period=14)])
        v = [x for x in f[col("mfi", period=14)] if x is not None]
        assert v, "mfi produced no values"
        assert all(0.0 <= x <= 100.0 for x in v)

    def test_mfi_reads_exactly_one_hundred_when_no_bar_falls(self):
        """No negative flow makes the ratio diverge. The limit is 100, and
        returning it directly is what keeps inf out of the pane."""
        df = fixture_frame()
        n = len(df)
        rising = df.with_columns([
            (pl.lit(10.0) + pl.int_range(pl.len()).cast(pl.Float64)).alias("close"),
            (pl.lit(11.0) + pl.int_range(pl.len()).cast(pl.Float64)).alias("high"),
            (pl.lit(9.0) + pl.int_range(pl.len()).cast(pl.Float64)).alias("low"),
            # adj_close must move WITH close, or the adj_close/close factor
            # rescales each bar differently and the series is no longer rising.
            (pl.lit(10.0) + pl.int_range(pl.len()).cast(pl.Float64)).alias("adj_close"),
        ])
        v = compute(rising, [resolve("mfi", period=14)])[col("mfi", period=14)]
        assert v[n - 1] == pytest.approx(100.0, abs=1e-12)

    def test_the_new_indicators_declare_no_eodhd_map(self):
        """None of these exist on EODHD's function list, so none may claim
        parity -- an eodhd map here would send a query that cannot be answered."""
        for name in ("momentum", "envelopes", "ad", "mfi"):
            assert REGISTRY[name].eodhd is None, name

    def test_ad_is_identical_on_the_adjusted_series(self):
        """Close location value is a ratio taken WITHIN one bar, so the
        adj_close/close factor cancels top and bottom ALGEBRAICALLY. It does not
        cancel to the bit: a factor like 0.985 is not exactly representable, so
        the scaled prices carry rounding the running sum accumulates. The
        tolerance below is the measured floor (3.7e-08 relative on this fixture),
        not a guess, and it is orders of magnitude below anything that could
        change a reading. The test exists so nobody 'fixes' `ad` into an adjusted
        variant believing the numbers will move."""
        df = fixture_frame()
        f = pl.col("adj_close") / pl.col("close")
        adjusted = df.with_columns([(pl.col(c) * f).alias(c)
                                    for c in ("open", "high", "low", "close")])
        a = compute(df, [resolve("ad")])[col("ad")]
        b = compute(adjusted, [resolve("ad")])[col("ad")]
        assert all(x == pytest.approx(y, rel=1e-6)
                   for x, y in zip(a, b) if x is not None and y is not None)

    def test_mfi_uses_the_adjusted_series_across_a_split(self):
        """mfi compares typical price ACROSS bars, so unlike ad the factor does
        not cancel. On a raw series a 2:1 split reads as a 50% crash and every
        window spanning it is misclassified."""
        df = fixture_frame()
        # Raw prices halve at bar 150; adj_close stays continuous, which is
        # exactly what a real split looks like in the vendor's data.
        split = df.with_columns([
            pl.when(pl.int_range(pl.len()) >= 150).then(pl.col(c) / 2)
              .otherwise(pl.col(c)).alias(c)
            for c in ("open", "high", "low", "close")
        ])
        v = compute(split, [resolve("mfi", period=14)])[col("mfi", period=14)]
        # The split bar is not a crash on the adjusted series, so the window
        # spanning it must not read as washed-out selling.
        window = [x for x in v[150:164] if x is not None]
        assert window, "no mfi values across the split"
        assert min(window) > 5.0, f"split read as a collapse: min {min(window)}"

    def test_the_adjustment_factor_is_one_when_a_provider_sends_no_adjustment(self):
        """payload.py falls back to adj_close = close for indices, forex and
        crypto. The factor must be exactly 1.0 there, not null or inf."""
        from app.ta.exprs import adj_factor

        df = fixture_frame().with_columns(pl.col("close").alias("adj_close"))
        f = df.select(adj_factor().alias("f"))["f"]
        assert all(x == pytest.approx(1.0, abs=1e-15) for x in f)

    def test_the_adjustment_factor_survives_a_zero_close(self):
        df = fixture_frame().with_columns([
            pl.when(pl.int_range(pl.len()) == 7).then(0.0)
              .otherwise(pl.col("close")).alias("close"),
        ])
        from app.ta.exprs import adj_factor
        f = df.select(adj_factor().alias("f"))["f"]
        assert f[7] == pytest.approx(1.0, abs=1e-15)
        assert all(x is not None and x == x for x in f)
