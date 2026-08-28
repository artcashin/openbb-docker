import pytest

from app.structure.trendlines import find_trendlines
from app.structure.types import Pivot
from tests.test_structure_pivots import make_bars


def pivot(bar, price, kind, date=None):
    return Pivot(id=f"p:t:{kind}:{bar}", date=date or f"2026-01-{bar + 1:02d}",
                 bar=bar, price=price, kind=kind, swing_atr=3.0,
                 swing_pct=3.0, confirmed=True)


class TestFindTrendlines:
    def test_three_pivots_on_a_line_yield_one_trendline(self):
        # Rising support: 100 at bar 0, 110 at 10, 120 at 20 -- slope 1.0/bar.
        closes = [100.0 + i for i in range(30)]
        pivots = [pivot(0, 100.0, "low"), pivot(10, 110.0, "low"),
                  pivot(20, 120.0, "low")]
        lines = find_trendlines(pivots, make_bars(closes), scale="t")
        assert len(lines) == 1
        assert lines[0].kind == "support"
        assert lines[0].touches == 3
        assert lines[0].slope_per_bar == pytest.approx(1.0, abs=1e-9)

    def test_two_pivots_alone_yield_nothing(self):
        """Two points define ANY line, so two touches prove nothing."""
        closes = [100.0 + i for i in range(30)]
        pivots = [pivot(0, 100.0, "low"), pivot(10, 110.0, "low")]
        assert find_trendlines(pivots, make_bars(closes), scale="t") == []

    def test_a_line_broken_before_its_last_touch_is_rejected(self):
        closes = [100.0 + i for i in range(30)]
        closes[15] = 80.0                      # decisive close through support
        pivots = [pivot(0, 100.0, "low"), pivot(10, 110.0, "low"),
                  pivot(20, 120.0, "low")]
        assert find_trendlines(pivots, make_bars(closes), scale="t") == []

    def test_a_break_after_the_last_touch_is_reported_not_withheld(self):
        """A broken trendline is information. Keep it, count the violation."""
        closes = [100.0 + i for i in range(30)]
        closes[25] = 80.0
        pivots = [pivot(0, 100.0, "low"), pivot(10, 110.0, "low"),
                  pivot(20, 120.0, "low")]
        lines = find_trendlines(pivots, make_bars(closes), scale="t")
        assert len(lines) == 1 and lines[0].violations >= 1

    def test_highs_make_resistance_and_lows_make_support(self):
        closes = [100.0 + i for i in range(30)]
        highs = [pivot(0, 100.0, "high"), pivot(10, 110.0, "high"),
                 pivot(20, 120.0, "high")]
        lines = find_trendlines(highs, make_bars(closes), scale="t")
        assert lines and all(t.kind == "resistance" for t in lines)

    def test_mixed_direction_pivots_never_form_one_line(self):
        closes = [100.0 + i for i in range(30)]
        mixed = [pivot(0, 100.0, "low"), pivot(10, 110.0, "high"),
                 pivot(20, 120.0, "low")]
        assert find_trendlines(mixed, make_bars(closes), scale="t") == []

    def test_output_is_capped(self):
        closes = [100.0] * 60
        pivots = [pivot(i, 100.0, "low") for i in range(0, 60, 2)]
        lines = find_trendlines(pivots, make_bars(closes), scale="t", cap=8)
        assert len(lines) <= 8

    def test_id_comes_from_the_origin_anchors_not_the_extent(self):
        closes = [100.0 + i for i in range(40)]
        three = [pivot(0, 100.0, "low"), pivot(10, 110.0, "low"),
                 pivot(20, 120.0, "low")]
        four = three + [pivot(30, 130.0, "low")]
        a = find_trendlines(three, make_bars(closes), scale="t")[0]
        b = find_trendlines(four, make_bars(closes), scale="t")[0]
        assert a.id == b.id            # identity survives the extension
        assert b.touches > a.touches   # extent does not
