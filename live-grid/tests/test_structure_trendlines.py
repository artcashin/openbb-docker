import pytest

from app.structure.trendlines import find_trendlines
from app.structure.types import Pivot
from tests.test_structure_pivots import make_bars


def pivot(bar, price, kind, date=None, confirmed=True):
    return Pivot(id=f"p:t:{kind}:{bar}", date=date or f"2026-01-{bar + 1:02d}",
                 bar=bar, price=price, kind=kind, swing_atr=3.0,
                 swing_pct=3.0, confirmed=confirmed)


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

    def test_output_is_capped_and_ordered_by_score_descending(self):
        # The old version of this test fed 30 colinear pivots, which
        # canonicalise to ONE line (see the "Every pair among 3+ colinear
        # pivots..." comment in trendlines.py), so `len(lines) <= 8` held
        # trivially either way and cap truncation was never exercised --
        # mirrors the fix already applied to the equivalent LEVELS test.
        #
        # 9 separate resistance lines instead, each its own group of 3 flat
        # touches at a price far from any other group's (large, nonlinearly
        # spaced gaps so no two groups' anchors accidentally lie on a third
        # group's line too) and at a later, non-overlapping bar range than
        # the last. closes stay flat at 100.0 -- always below every group's
        # price -- so a resistance line is never "broken" by the close
        # series. touches (3) and span (6 bars) are identical across
        # groups, so score is driven purely by recency, which grows with
        # each group's (later) bar range -- making score strictly increasing
        # with price here, the same recency-monotonic trick the levels test
        # uses to pin down an exact, unambiguous ordering.
        closes = [100.0] * 250
        pivots = []
        for g in range(9):
            price = 100.0 + g * 713 + (g * g) * 197
            base = g * 11 + (g * g) * 2
            for off in (0, 3, 6):
                pivots.append(pivot(base + off, price, "high"))
        lines = find_trendlines(pivots, make_bars(closes), scale="t", cap=8)
        assert len(lines) == 8
        # Highest score first, and the lowest-scoring group (g=0, price
        # 100.0) is the one that got capped away -- not, say, the group in
        # the middle, and not the highest-scoring group either.
        assert [round(t.from_price, 1) for t in lines] == [
            18412.0, 14744.0, 11470.0, 8590.0, 6104.0, 4012.0, 2314.0, 1010.0]
        assert [t.score for t in lines] == sorted(
            [t.score for t in lines], reverse=True)

    def test_a_trendline_built_from_an_unconfirmed_pivot_is_provisional(self):
        """Finding 3 (final review). With MIN_TOUCHES = 3, a line's `to`
        anchor can BE the newest, still-developing pivot -- that line has no
        business reporting `touches: 3, violations: 0` as though settled."""
        closes = [100.0 + i for i in range(30)]
        pivots = [pivot(0, 100.0, "low"), pivot(10, 110.0, "low"),
                  pivot(20, 120.0, "low", confirmed=False)]
        line = find_trendlines(pivots, make_bars(closes), scale="t")[0]
        assert line.provisional is True

    def test_a_trendline_built_entirely_from_confirmed_pivots_is_not_provisional(self):
        closes = [100.0 + i for i in range(30)]
        pivots = [pivot(0, 100.0, "low"), pivot(10, 110.0, "low"),
                  pivot(20, 120.0, "low")]
        line = find_trendlines(pivots, make_bars(closes), scale="t")[0]
        assert line.provisional is False

    def test_provisional_survives_to_dict(self):
        """to_dict() rebuilds the trendline dict field by field for the
        nested from/to shape (see types.py) -- a new field on the dataclass
        is silently dropped there unless it is also listed explicitly."""
        from app.structure.types import ScaleResult, StructureResult

        closes = [100.0 + i for i in range(30)]
        pivots = [pivot(0, 100.0, "low"), pivot(10, 110.0, "low"),
                  pivot(20, 120.0, "low", confirmed=False)]
        line = find_trendlines(pivots, make_bars(closes), scale="t")[0]
        assert line.provisional is True
        result = StructureResult(
            symbol="T", interval="1d", range={}, atr_period=14,
            scales=[ScaleResult(name="t", k=3.0, trendlines=[line])],
        )
        payload = result.to_dict()
        assert payload["scales"][0]["trendlines"][0]["provisional"] is True

    def test_id_comes_from_the_origin_anchors_not_the_extent(self):
        closes = [100.0 + i for i in range(40)]
        three = [pivot(0, 100.0, "low"), pivot(10, 110.0, "low"),
                 pivot(20, 120.0, "low")]
        four = three + [pivot(30, 130.0, "low")]
        a = find_trendlines(three, make_bars(closes), scale="t")[0]
        b = find_trendlines(four, make_bars(closes), scale="t")[0]
        assert a.id == b.id            # identity survives the extension
        assert b.touches > a.touches   # extent does not
