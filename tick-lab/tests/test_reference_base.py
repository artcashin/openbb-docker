"""Interval step-down: try the finest, record why each attempt failed."""

import pandas as pd
import pytest

from tick_lab.reference.base import (
    INTERVAL_LADDER,
    ReferenceError,
    fetch_finest,
)


class FakeAdapter:
    name = "fake"
    supported_intervals = INTERVAL_LADDER

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls = []

    def fetch(self, symbol, start, end, interval):
        self.calls.append(interval)
        outcome = self.behaviour[interval]
        if isinstance(outcome, ReferenceError):
            raise outcome
        return outcome


def bars(n=1):
    idx = pd.date_range("2023-05-12 13:30:00Z", periods=n, freq="1min")
    return pd.DataFrame(
        {"open": [1.0] * n, "high": [1.0] * n, "low": [1.0] * n,
         "close": [1.0] * n, "volume": [1] * n},
        index=idx,
    )


def test_returns_the_finest_interval_that_works():
    adapter = FakeAdapter({"1m": bars()})
    result = fetch_finest(adapter, "MSFT", "2023-05-12", "2023-05-12")
    assert result.interval == "1m"
    assert adapter.calls == ["1m"]
    assert result.attempts[-1].error is None


def test_steps_down_past_retention_failures():
    adapter = FakeAdapter({
        "1m": ReferenceError("retention", "must be within the last 30 days"),
        "5m": ReferenceError("retention", "must be within the last 60 days"),
        "15m": ReferenceError("retention", "must be within the last 60 days"),
        "30m": ReferenceError("retention", "must be within the last 60 days"),
        "1h": ReferenceError("retention", "must be within the last 730 days"),
        "1d": bars(),
    })
    result = fetch_finest(adapter, "MSFT", "2023-05-12", "2023-05-12")
    assert result.interval == "1d"
    assert adapter.calls == list(INTERVAL_LADDER)


def test_every_attempt_is_recorded_for_the_report():
    adapter = FakeAdapter({
        "1m": ReferenceError("retention", "must be within the last 30 days"),
        "5m": bars(),
    })
    result = fetch_finest(adapter, "MSFT", "2023-05-12", "2023-05-12")
    assert [a.interval for a in result.attempts] == ["1m", "5m"]
    assert "last 30 days" in result.attempts[0].error.detail
    assert result.attempts[1].error is None


def test_entitlement_errors_are_not_swallowed_by_stepping_down():
    """A 403 means 'you may not have this', not 'try a coarser bar'."""
    adapter = FakeAdapter({"1m": ReferenceError("entitlement", "403 Forbidden")})
    with pytest.raises(ReferenceError) as exc:
        fetch_finest(adapter, "GOOG", "2023-05-12", "2023-05-12")
    assert exc.value.kind == "entitlement"
    assert adapter.calls == ["1m"]


def test_raises_when_the_whole_ladder_is_exhausted():
    adapter = FakeAdapter({i: ReferenceError("empty", "no rows") for i in INTERVAL_LADDER})
    with pytest.raises(ReferenceError, match="no interval"):
        fetch_finest(adapter, "MSFT", "2023-05-12", "2023-05-12")


def test_starts_from_the_requested_interval():
    adapter = FakeAdapter({"1h": bars(), "1d": bars()})
    result = fetch_finest(adapter, "MSFT", "2023-05-12", "2023-05-12", wanted="1h")
    assert adapter.calls == ["1h"]
    assert result.interval == "1h"


def test_skips_intervals_the_adapter_does_not_support():
    class DailyOnly(FakeAdapter):
        supported_intervals = ("1d",)

    adapter = DailyOnly({"1d": bars()})
    result = fetch_finest(adapter, "MSFT", "2023-05-12", "2023-05-12")
    assert adapter.calls == ["1d"]
    assert result.interval == "1d"
