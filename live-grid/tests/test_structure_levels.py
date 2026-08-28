import pytest

from app.structure.levels import find_levels
from tests.test_structure_pivots import make_bars
from tests.test_structure_trendlines import pivot


class TestFindLevels:
    def test_pivots_at_nearly_one_price_make_one_level(self):
        closes = [100.0] * 40
        pivots = [pivot(5, 120.0, "high"), pivot(15, 120.2, "high"),
                  pivot(25, 119.9, "high")]
        levels = find_levels(pivots, make_bars(closes), scale="t")
        assert len(levels) == 1
        assert levels[0].touches == 3
        assert levels[0].price == pytest.approx(120.0, abs=0.3)

    def test_pivots_far_apart_stay_separate(self):
        closes = [100.0] * 40
        pivots = [pivot(5, 120.0, "high"), pivot(15, 150.0, "high")]
        assert len(find_levels(pivots, make_bars(closes), scale="t")) == 2

    def test_a_level_tested_from_both_sides_records_both(self):
        """The classic flip -- resistance that later held as support."""
        closes = [100.0] * 40
        pivots = [pivot(5, 120.0, "high"), pivot(25, 120.1, "low")]
        level = find_levels(pivots, make_bars(closes), scale="t")[0]
        assert sorted(level.sides) == ["resistance", "support"]

    def test_first_and_last_span_the_members(self):
        closes = [100.0] * 40
        pivots = [pivot(5, 120.0, "high"), pivot(25, 120.1, "high")]
        level = find_levels(pivots, make_bars(closes), scale="t")[0]
        assert level.first == pivots[0].date and level.last == pivots[1].date

    def test_more_touches_scores_higher(self):
        closes = [100.0] * 60
        few = [pivot(5, 120.0, "high"), pivot(15, 120.1, "high")]
        many = few + [pivot(25, 119.9, "high"), pivot(35, 120.05, "high")]
        a = find_levels(few, make_bars(closes), scale="t")[0]
        b = find_levels(many, make_bars(closes), scale="t")[0]
        assert b.score > a.score

    def test_output_is_capped_and_ordered_by_score_descending(self):
        # 9 pivots, each 10 points apart -- ATR is ~1.0 here, tol is 0.75*ATR,
        # so every pivot is its own single-member cluster: touches=1 and
        # span=0 for all of them, so score is driven purely by recency, which
        # grows monotonically with bar. That makes the 9 clusters strictly
        # ordered lowest score (bar 0, price 100) to highest (bar 8, price
        # 180) -- and it is the only case in this file with more than one
        # output level, so it is the only case that can catch either half of
        # "highest score first": a missing/inverted sort, or a cap that keeps
        # the wrong end of the ranking. A `reverse=False` bug keeps the 8
        # LOWEST-scoring clusters in ascending order (100..170) instead of
        # the 8 HIGHEST in descending order (180..110) -- same length (8) and
        # same set-vs-9 truncation, so a len()-only or single-element
        # assertion cannot see it; the exact price sequence below can.
        closes = [100.0] * 100
        pivots = [pivot(i, 100.0 + i * 10, "high") for i in range(9)]
        levels = find_levels(pivots, make_bars(closes), scale="t", cap=8)
        assert len(levels) == 8
        assert [lvl.price for lvl in levels] == [
            180.0, 170.0, 160.0, 150.0, 140.0, 130.0, 120.0, 110.0]

    def test_ids_are_deterministic(self):
        closes = [100.0] * 40
        pivots = [pivot(5, 120.0, "high"), pivot(15, 120.1, "high")]
        a = find_levels(pivots, make_bars(closes), scale="s")
        b = find_levels(pivots, make_bars(closes), scale="s")
        assert [x.id for x in a] == [x.id for x in b]
