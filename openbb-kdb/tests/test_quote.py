"""Quote assembly: live tick over daily-bar session fields."""

from datetime import datetime

import pytest

from openbb_kdb.models.quote import build_quote


def test_a_tick_supplies_last_price_size_and_timestamp():
    got = build_quote("AAPL", {"time": datetime(2026, 8, 26, 15, 14), "price": 312.95,
                               "size": 40.0}, prev_close=309.9)
    assert got["symbol"] == "AAPL"
    assert got["last_price"] == 312.95
    assert got["last_size"] == 40.0
    assert got["last_timestamp"] == datetime(2026, 8, 26, 15, 14)


def test_change_is_computed_against_the_previous_close():
    got = build_quote("AAPL", {"time": datetime(2026, 8, 26), "price": 312.95, "size": 1.0},
                      prev_close=309.9)
    assert got["prev_close"] == 309.9
    assert round(got["change"], 2) == 3.05
    assert round(got["change_percent"], 4) == round(3.05 / 309.9, 4)


def test_intraday_session_fields_are_absent_not_guessed():
    """Daily bars are end-of-day, so today's OHLC does not exist yet. Leaving
    them out is the documented behaviour (spec D5); inventing them from the
    tick would make open == high == low == last_price, which reads as real."""
    got = build_quote("AAPL", {"time": datetime(2026, 8, 26), "price": 312.95, "size": 1.0},
                      prev_close=309.9)
    for field in ("open", "high", "low", "volume"):
        assert got.get(field) is None


def test_a_missing_previous_close_leaves_change_undefined_rather_than_zero():
    """change=0.0 would render as "unchanged", which is a claim we cannot make."""
    got = build_quote("AAPL", {"time": datetime(2026, 8, 26), "price": 312.95, "size": 1.0},
                      prev_close=None)
    assert got["last_price"] == 312.95
    assert got.get("change") is None
    assert got.get("change_percent") is None


def test_a_zero_previous_close_does_not_divide_by_zero():
    got = build_quote("AAPL", {"time": datetime(2026, 8, 26), "price": 5.0, "size": 1.0},
                      prev_close=0.0)
    assert got.get("change_percent") is None


def test_no_tick_yields_no_row():
    assert build_quote("AAPL", None, prev_close=309.9) is None


@pytest.mark.asyncio
async def test_waiting_returns_the_first_tick_that_appears():
    from openbb_kdb.models.quote import _await_tick

    calls = {"n": 0}

    class Store:
        def latest_tick(self, symbol):
            calls["n"] += 1
            return {"time": datetime(2026, 8, 26), "price": 1.0, "size": 1.0} \
                if calls["n"] >= 2 else None

    assert await _await_tick(Store(), "AAPL", deadline=2.0) is not None
    assert calls["n"] >= 2


@pytest.mark.asyncio
async def test_waiting_gives_up_at_the_deadline_rather_than_hanging():
    from openbb_kdb.models.quote import _await_tick

    class Never:
        def latest_tick(self, symbol):
            return None

    assert await _await_tick(Never(), "AAPL", deadline=0.3) is None


def test_the_fetcher_is_registered_for_the_quote_model():
    """This registration is what puts `kdb` on /equity/price/quote."""
    import openbb_kdb

    assert openbb_kdb.kdb_provider.fetcher_dict["EquityQuote"] is not None


def test_transform_data_omits_a_fractional_last_size_rather_than_raising():
    """last_size is int | None on EquityQuoteData; kdb reports size as a
    float. A fractional size must not raise ValidationError -- it must
    validate through the real model with last_size simply absent."""
    from openbb_kdb.models.quote import KdbEquityQuoteFetcher

    data = {"symbol": "AAPL",
            "tick": {"time": datetime(2026, 8, 26), "price": 312.95, "size": 40.5},
            "prev_close": None}
    rows = KdbEquityQuoteFetcher.transform_data(None, data)
    assert len(rows) == 1
    assert rows[0].last_size is None


def test_transform_data_keeps_a_whole_last_size():
    from openbb_kdb.models.quote import KdbEquityQuoteFetcher

    data = {"symbol": "AAPL",
            "tick": {"time": datetime(2026, 8, 26), "price": 312.95, "size": 40.0},
            "prev_close": None}
    rows = KdbEquityQuoteFetcher.transform_data(None, data)
    assert rows[0].last_size == 40


@pytest.mark.asyncio
async def test_waiting_is_capped_by_the_deadline_even_when_a_single_call_blocks_past_it():
    """A cold/dead kdb session's own connect budget (or a wedged call) must
    not be able to push the total wait past `deadline` -- each `to_thread`
    call is bounded by `wait_for`, not just checked for between calls."""
    import time

    from openbb_kdb.models.quote import _await_tick

    class SlowThenTick:
        def latest_tick(self, symbol):
            time.sleep(0.6)
            return {"time": datetime(2026, 8, 26), "price": 1.0, "size": 1.0}

    start = time.monotonic()
    got = await _await_tick(SlowThenTick(), "AAPL", deadline=0.2)
    elapsed = time.monotonic() - start

    assert got is None
    assert elapsed < 0.6
