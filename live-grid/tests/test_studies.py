"""Tests for app.studies: the live grid's RSI(14) and anchored-VWAP columns.

Both ride one compute() call over one frame, so the grid's numbers cannot
drift from the chart's -- see the brief for why this task does not transcribe
the supplied Perspective prototype's Cutler RSI or its own kdb websocket.
"""

from datetime import datetime, timedelta

from app.studies import studies_for
from app.ta.compute import compute
from app.ta.payload import bars_to_frame
from app.ta.registry import resolve
from tests.ta_helpers import col


def _tick_bars(
    n: int, shock_at: int | None = None, shock: float = 80.0, base: float = 100.0
) -> list[dict]:
    """`n` one-minute tick-derived bars, each carrying a real trade vwap.

    `shock_at` inserts one large move at that bar -- the fixture the
    Wilder/Cutler test needs, since the two conventions only diverge once a
    single bar's move dwarfs the rest of the window.
    """
    bars = []
    price = base
    start = datetime(2026, 1, 1, 9, 30)
    for i in range(n):
        if shock_at is not None and i == shock_at:
            price += shock
        elif i > 0:
            price += 1.0 if i % 2 == 0 else -0.5
        ts = start + timedelta(minutes=i)
        bars.append({
            "date": ts.isoformat(), "open": price, "high": price, "low": price,
            "close": price, "volume": 100.0, "vwap": price,
        })
    return bars


def test_rsi_and_avwap_ride_the_same_computed_frame():
    """One compute() call, one frame, both columns — so the grid's RSI and
    the chart's RSI cannot disagree, and neither can the two AVWAPs."""
    frame = bars_to_frame(_tick_bars(60))
    reqs = [resolve("rsi", period=14), resolve("avwap", anchor=None)]
    out = compute(frame, reqs)
    assert out[col("rsi", period=14)][-1] is not None
    assert out[col("avwap")][-1] is not None


def test_rsi_is_wilders_not_cutlers():
    """Wilder smooths with alpha = 1/n; Cutler takes a flat mean. On a series
    with one large early move they diverge, which is what this pins — the
    supplied prototype uses Cutler's and this grid must not."""
    frame = bars_to_frame(_tick_bars(60, shock_at=5))
    wilder = compute(frame, [resolve("rsi", period=14)])[col("rsi", period=14)][-1]
    prices = frame["adj_close"].to_list()
    window = prices[-15:]
    gains = sum(max(b - a, 0) for a, b in zip(window, window[1:])) / 14
    losses = sum(max(a - b, 0) for a, b in zip(window, window[1:])) / 14
    cutlers = 100.0 if losses == 0 else 100 - 100 / (1 + gains / losses)
    assert abs(wilder - cutlers) > 0.5, "the two conventions must be distinguishable"


def test_a_symbol_with_too_little_history_reports_null_rsi_not_fifty():
    """The prototype returns 50.0 for a flat or short window. A null says
    "unknown"; 50 says "neutral", and a scanner sorted on RSI would rank
    every cold symbol in the middle of the pack."""
    assert studies_for("NEW", bars=[]) == {
        "symbol": "NEW", "rsi": None, "avwap": None, "avwap_dev": None,
    }


def test_studies_for_matches_the_raw_compute_and_fills_avwap_dev():
    """studies_for is not a second implementation: it must return exactly
    what compute() over the same frame produces, plus the signed deviation
    the grid's bar needs and the raw compute() call does not provide."""
    bars = _tick_bars(60)
    frame = bars_to_frame(bars)
    reqs = [resolve("rsi", period=14), resolve("avwap", anchor=None)]
    out = compute(frame, reqs)
    expected_rsi = out[col("rsi", period=14)][-1]
    expected_avwap = out[col("avwap")][-1]
    expected_price = out["close"][-1]

    row = studies_for("AAPL", bars)

    assert row["symbol"] == "AAPL"
    assert row["rsi"] == expected_rsi
    assert row["avwap"] == expected_avwap
    assert row["avwap_dev"] == (expected_price - expected_avwap) / expected_avwap
