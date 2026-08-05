"""The seam: where tick-derived bars meet cached history."""

from datetime import datetime, timedelta

import pytest

from app.series import seam_boundary, stitch, tick_capable

D = lambda s: datetime.fromisoformat(s)  # noqa: E731


def bar(stamp, close=1.0):
    return {"date": D(stamp), "open": close, "high": close, "low": close,
            "close": close, "volume": 1.0}


def test_boundary_rounds_up_to_the_next_bar():
    """A tick mid-bar cannot own that bar -- it is missing the bar's opening trades."""
    assert seam_boundary(D("2025-06-10T14:00:30"), "1m") == D("2025-06-10T14:01:00")


def test_boundary_of_a_tick_exactly_on_a_boundary_is_that_boundary():
    assert seam_boundary(D("2025-06-10T14:01:00"), "1m") == D("2025-06-10T14:01:00")


def test_boundary_for_five_minute_bars():
    assert seam_boundary(D("2025-06-10T14:02:10"), "5m") == D("2025-06-10T14:05:00")


def test_stitch_drops_history_at_or_after_the_boundary():
    """Otherwise the seam emits two bars for the same timestamp."""
    history = [bar("2025-06-10T13:58:00"), bar("2025-06-10T13:59:00"),
               bar("2025-06-10T14:01:00", close=9.0)]
    ticks = [bar("2025-06-10T14:01:00", close=5.0), bar("2025-06-10T14:02:00", close=6.0)]
    out = stitch(history, ticks, D("2025-06-10T14:01:00"))
    stamps = [r["date"] for r in out]
    assert stamps == sorted(stamps)
    assert len(stamps) == len(set(stamps)), "duplicate timestamps across the seam"
    assert [r["close"] for r in out if r["date"] == D("2025-06-10T14:01:00")] == [5.0]


def test_stitch_with_no_ticks_returns_history():
    history = [bar("2025-06-10T13:58:00")]
    assert stitch(history, [], D("2025-06-10T14:01:00")) == history


def test_stitch_with_no_history_returns_ticks():
    ticks = [bar("2025-06-10T14:01:00")]
    assert stitch([], ticks, D("2025-06-10T14:01:00")) == ticks


def test_stitch_output_is_time_ordered_even_if_inputs_are_not():
    history = [bar("2025-06-10T13:59:00"), bar("2025-06-10T13:58:00")]
    ticks = [bar("2025-06-10T14:02:00"), bar("2025-06-10T14:01:00")]
    out = stitch(history, ticks, D("2025-06-10T14:01:00"))
    assert [r["date"] for r in out] == sorted(r["date"] for r in out)


@pytest.mark.parametrize("interval,ok", [("1m", True), ("5m", True), ("1h", True),
                                         ("1d", False), ("1w", False)])
def test_tick_capable_rejects_intervals_wider_than_the_window(interval, ok):
    assert tick_capable(interval, timedelta(hours=6)) is ok
