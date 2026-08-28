from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.structure.detect import DEFAULT_SCALES, detect, scale_for_bars
from tests.test_chart_routes import _NoSpanStore, bar
from tests.test_structure_pivots import make_bars, triangle


@pytest.fixture
def client(monkeypatch):
    async def fake_history(symbol, interval, start, end, provider="kdb"):
        return ([bar("2025-06-10T13:58:00"), bar("2025-06-10T13:59:00")],
                {"cache": "hit", "rows_from_cache": 2, "rows_from_upstream": 0,
                 "gaps_fetched": 0, "upstream_ms": 0.0, "kdb_ms": 1.0})

    monkeypatch.setattr("app.main.fetch_series", fake_history)
    monkeypatch.setattr("kdb_store.config.resolve_config", lambda: object())
    monkeypatch.setattr("kdb_store.session.KdbSession", lambda config: object())
    monkeypatch.setattr("kdb_store.store.KdbStore", lambda session: _NoSpanStore())
    return TestClient(create_app(api_key="test-key"))


class TestScaleSelection:
    @pytest.mark.parametrize("bars,expected", [
        (30, "swing"), (119, "swing"), (120, "intermediate"),
        (600, "intermediate"), (601, "primary"), (5000, "primary"),
    ])
    def test_scale_is_chosen_by_bar_count(self, bars, expected):
        assert scale_for_bars(bars) == expected


class TestDetect:
    def test_all_three_default_scales_are_returned(self):
        closes = triangle([100.0, 130.0, 105.0, 140.0, 110.0], run=25)
        result = detect(make_bars(closes), "TEST.US", "1d")
        assert [s.name for s in result.scales] == list(DEFAULT_SCALES)

    def test_scales_are_independent_not_nested(self):
        """The spec forbids assuming containment. A coarse scale may legitimately
        pick a pivot the fine scale skipped, so nothing may filter by subset."""
        closes = triangle([100.0, 130.0, 105.0, 140.0, 110.0], run=25)
        result = detect(make_bars(closes), "TEST.US", "1d")
        by_name = {s.name: s for s in result.scales}
        assert len(by_name["swing"].pivots) >= len(by_name["primary"].pivots)

    def test_each_scale_calls_find_pivots_independently_with_its_own_k(self):
        """The count comparison above is nearly always true by construction
        (a larger retracement threshold naturally yields fewer or equal
        pivots) -- it would still pass a broken `detect()` that computed
        swing's pivots once and then filtered them down for intermediate and
        primary instead of calling find_pivots three times. Spy on the real
        find_pivots to prove each scale gets its own independent call against
        the *same, full* frame -- not a subset carried over from another
        scale's result."""
        from app.structure.pivots import find_pivots as real_find_pivots

        closes = triangle([100.0, 130.0, 105.0, 140.0, 110.0], run=25)
        df = make_bars(closes)
        with patch("app.structure.detect.find_pivots",
                   wraps=real_find_pivots) as spy:
            detect(df, "TEST.US", "1d")

        assert spy.call_count == len(DEFAULT_SCALES)
        seen_ks = set()
        for call in spy.call_args_list:
            kwargs = call.kwargs
            call_df = call.args[0] if call.args else kwargs["df"]
            k = kwargs.get("k", call.args[1] if len(call.args) > 1 else None)
            # Every call receives the identical, full input frame -- never a
            # frame sliced or narrowed by a previous scale's result.
            assert call_df.height == df.height
            seen_ks.add(k)
        assert seen_ks == set(DEFAULT_SCALES.values())

    def test_a_window_too_short_for_a_scale_returns_a_note_not_an_error(self):
        # NOTE: the brief's own draft used range(20) here. At 20 bars, a
        # monotonic ramp already moves far enough (~19 points against an ATR
        # of roughly 1) to clear primary's k=8 retracement threshold once,
        # which makes find_pivots emit its one deliberately-unconfirmed
        # "developing swing" pivot (see pivots.py's trailing `if direction is
        # not None` block, from Task 1) -- so primary.pivots is [Pivot(...)],
        # not []. That is not a "too short" window; it is a real (if
        # unconfirmed) swing. 5 bars is short enough that the ramp never
        # clears the threshold at all, so direction stays None and find_pivots
        # correctly returns []. Verified empirically: n=5 -> 0 pivots at
        # k=8.0, n=8 -> 1 (unconfirmed). This is a test-data fix, not an
        # implementation change -- the "if not pivots" branch in detect() is
        # the right contract given find_pivots' documented behavior.
        result = detect(make_bars([100.0 + i for i in range(5)]), "T", "1d")
        primary = [s for s in result.scales if s.name == "primary"][0]
        assert primary.pivots == [] and primary.note

    def test_the_range_reports_what_was_actually_analysed(self):
        closes = triangle([100.0, 130.0, 105.0], run=25)
        result = detect(make_bars(closes), "TEST.US", "1d")
        assert result.range["bars"] == len(closes)
        assert result.symbol == "TEST.US" and result.interval == "1d"

    def test_to_dict_nests_trendline_anchors(self):
        closes = triangle([100.0, 130.0, 105.0, 140.0], run=25)
        payload = detect(make_bars(closes), "T", "1d").to_dict()
        for scale in payload["scales"]:
            for line in scale["trendlines"]:
                assert set(line["from"]) == {"date", "price"}
                assert "from_date" not in line

    def test_detection_is_deterministic(self):
        closes = triangle([100.0, 130.0, 105.0, 140.0], run=25)
        a = detect(make_bars(closes), "T", "1d").to_dict()
        b = detect(make_bars(closes), "T", "1d").to_dict()
        assert a == b


class TestStructureRoute:
    def test_the_route_returns_one_scale_chosen_by_range(self, client):
        body = client.get("/structure?symbol=AAPL&interval=1d").json()
        assert len(body["scales"]) == 1

    def test_an_unknown_scale_is_rejected_with_the_valid_names(self, client):
        r = client.get("/structure?symbol=AAPL&scale=hourly")
        assert r.status_code == 400
        assert "primary" in r.json()["detail"]
