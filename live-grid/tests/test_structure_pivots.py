import polars as pl
import pytest

from app.structure.atr import adjusted_atr
from app.structure.pivots import find_pivots


def make_bars(closes: list[float], atr_hint: float = 1.0) -> pl.DataFrame:
    """A frame with a known, near-constant ATR so `k` is easy to reason about.

    high/low sit atr_hint/2 either side of close, which makes true range about
    atr_hint on every bar. adj_close == close, so the adjustment factor is 1.0
    and these tests isolate the ZigZag from the adjustment.
    """
    n = len(closes)
    return pl.DataFrame({
        "date": [f"2026-01-{i + 1:02d}" if i < 31 else f"2026-02-{i - 30:02d}"
                 for i in range(n)],
        "open": closes,
        "high": [c + atr_hint / 2 for c in closes],
        "low": [c - atr_hint / 2 for c in closes],
        "close": closes,
        "adj_close": closes,
        "volume": [1000.0] * n,
    })


def triangle(peaks: list[float], run: int = 10) -> list[float]:
    """Linear ramps between successive levels — turning points exactly at the
    junctions, so the right answer is knowable by construction."""
    out: list[float] = [peaks[0]]
    for a, b in zip(peaks, peaks[1:]):
        out += [a + (b - a) * (i + 1) / run for i in range(run)]
    return out


class TestFindPivots:
    def test_turning_points_land_on_the_exact_bars(self):
        closes = triangle([100.0, 120.0, 105.0, 130.0])
        pivots = find_pivots(make_bars(closes), k=3.0, scale="test")
        # Junctions of the ramps: index 10 (120), 20 (105).
        confirmed = [p for p in pivots if p.confirmed]
        assert [p.bar for p in confirmed] == [10, 20]
        assert [p.kind for p in confirmed] == ["high", "low"]
        # emit() reads a high pivot's price from highs[bar], and make_bars sets
        # high = close + atr_hint/2, so the peak's price is 120.0 + 0.5.
        assert confirmed[0].price == pytest.approx(120.5, abs=1e-9)

    def test_a_sub_threshold_wiggle_produces_no_extra_pivots(self):
        """The whole claim of the method: noise below k*ATR is not structure."""
        clean = triangle([100.0, 120.0, 105.0, 130.0])
        noisy = [c + (0.4 if i % 2 else -0.4) for i, c in enumerate(clean)]
        a = [p.bar for p in find_pivots(make_bars(clean), k=3.0, scale="t")
             if p.confirmed]
        b = [p.bar for p in find_pivots(make_bars(noisy), k=3.0, scale="t")
             if p.confirmed]
        assert a == b

    def test_atr_normalisation_makes_k_portable(self):
        """Same shape at a different price and volatility gives the same BARS.
        If this fails, k is not portable and the default scales are meaningless."""
        closes = triangle([100.0, 120.0, 105.0, 130.0])
        small = find_pivots(make_bars(closes, atr_hint=1.0), k=3.0, scale="t")
        scaled = [c * 10 for c in closes]
        big = find_pivots(make_bars(scaled, atr_hint=10.0), k=3.0, scale="t")
        assert [p.bar for p in small] == [p.bar for p in big]

    def test_the_last_extreme_is_provisional(self):
        """A series ending mid-swing has an unconfirmed final pivot."""
        closes = triangle([100.0, 120.0, 105.0]) + [106.0, 107.0]
        pivots = find_pivots(make_bars(closes), k=3.0, scale="t")
        assert pivots[-1].confirmed is False

    def test_extending_past_the_retracement_confirms_it(self):
        closes = triangle([100.0, 120.0, 105.0])
        short = find_pivots(make_bars(closes), k=3.0, scale="t")
        long = find_pivots(make_bars(closes + triangle([105.0, 130.0])), k=3.0,
                           scale="t")
        provisional = [p for p in short if not p.confirmed]
        assert provisional, "expected a provisional pivot to exist"
        # closes + triangle([105.0, 130.0]) duplicates the seam value (105.0)
        # at bars 20 and 21. find_pivots' tie-break rule extends a tied
        # extreme forward to the newest bar sharing that value, so the
        # confirmed low lands at bar 21 -- one bar past the provisional one,
        # same price.
        same = [p for p in long if p.bar == provisional[0].bar + 1]
        assert len(same) == 1 and same[0].confirmed is True
        assert same[0].price == provisional[0].price

    def test_a_bigger_k_yields_fewer_pivots(self):
        closes = triangle([100.0, 112.0, 104.0, 118.0, 108.0, 126.0])
        fine = find_pivots(make_bars(closes), k=1.0, scale="t")
        coarse = find_pivots(make_bars(closes), k=8.0, scale="t")
        assert len(coarse) < len(fine)

    def test_ids_are_deterministic_and_encode_the_anchor(self):
        closes = triangle([100.0, 120.0, 105.0, 130.0])
        a = find_pivots(make_bars(closes), k=3.0, scale="intermediate")
        b = find_pivots(make_bars(closes), k=3.0, scale="intermediate")
        assert [p.id for p in a] == [p.id for p in b]
        # See test_turning_points_land_on_the_exact_bars: the pivot's price is
        # the bar's HIGH (120.5), not its close (120.0).
        assert a[0].id == f"p:intermediate:high:{a[0].date}:120.5000"

    def test_a_frame_too_short_to_hold_a_swing_returns_nothing(self):
        pivots = find_pivots(make_bars([100.0, 101.0, 102.0]), k=8.0, scale="t")
        assert pivots == []

    def test_a_split_does_not_invent_a_pivot(self):
        """Detection runs on the ADJUSTED series. On raw prices a 2:1 split is a
        50% crash and the ZigZag confirms a pivot that never happened.

        Built the way a vendor actually reports one: raw OHLC halves at the
        split bar while adj_close stays continuous. The measured cost of getting
        this wrong elsewhere in this codebase was 14 MFI points, so it is worth
        a test rather than a comment.

        The bar-equality check below is necessary but not sufficient: this
        fixture's post-split swing (~25 points) clears even a threshold
        mis-scaled by the split, so it alone cannot catch the ATR itself
        being computed from raw (rather than adjusted) OHLC. The ATR
        continuity check that follows targets that directly."""
        closes = triangle([100.0, 120.0, 105.0, 130.0])
        clean = make_bars(closes)
        split_at = 15
        split = clean.with_columns([
            pl.when(pl.int_range(pl.len()) >= split_at)
              .then(pl.col(c) / 2).otherwise(pl.col(c)).alias(c)
            for c in ("open", "high", "low", "close")
        ])
        # adj_close is left continuous, so adj_close/close is 2.0 after the
        # split and the adjusted series is identical to the unsplit one.
        assert [p.bar for p in find_pivots(split, k=3.0, scale="t")] == \
               [p.bar for p in find_pivots(clean, k=3.0, scale="t")]

        # The threshold pivots use is k * ATR, so the ATR itself must be
        # computed from the adjusted series too. If it were derived from
        # RAW OHLC instead (app.ta.exprs.true_range()), raw true range would
        # roughly halve at split_at while the adjusted series does not, and
        # these two ATR series -- which reconstruct the same adjusted OHLC --
        # would diverge sharply right where the split lands.
        assert adjusted_atr(split, 14) == pytest.approx(
            adjusted_atr(clean, 14), rel=1e-9)
