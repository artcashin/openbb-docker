"""Mapping yfinance behaviour onto classified ReferenceErrors."""

import pandas as pd
import pytest

from tick_lab.reference.base import ReferenceError
from tick_lab.reference.yfinance_adapter import YFinanceAdapter, classify

RETENTION_1M = (
    '$MSFT: possibly delisted; no price data found  (1m 2023-05-12 -> 2023-05-13) '
    '(Yahoo error = "1m data not available for startTime=1683864000 and '
    'endTime=1683950400. The requested range must be within the last 30 days.")'
)
RETENTION_1H = (
    '$MSFT: possibly delisted; no price data found  (1h 2023-05-12 -> 2023-05-13) '
    '(Yahoo error = "1h data not available for startTime=1683864000 and '
    'endTime=1683950400. The requested range must be within the last 730 days.")'
)


def test_classifies_the_1m_retention_message():
    err = classify(RETENTION_1M)
    assert err.kind == "retention"
    assert "last 30 days" in err.detail


def test_classifies_the_1h_retention_message():
    assert classify(RETENTION_1H).kind == "retention"


def test_classifies_an_unknown_message_as_empty():
    assert classify("something else entirely").kind == "empty"


def test_supported_intervals_cover_the_ladder():
    assert "1m" in YFinanceAdapter().supported_intervals
    assert "1d" in YFinanceAdapter().supported_intervals


def test_normalises_columns_and_index(monkeypatch):
    adapter = YFinanceAdapter()

    raw = pd.DataFrame(
        {"Open": [1.0], "High": [2.0], "Low": [0.5], "Close": [1.5], "Volume": [100]},
        index=pd.DatetimeIndex(["2023-05-12 09:30:00"], tz="America/New_York"),
    )
    monkeypatch.setattr(adapter, "_history", lambda *a, **k: raw)

    out = adapter.fetch("MSFT", "2023-05-12", "2023-05-13", "1m")
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]
    assert str(out.index.tz) == "UTC"


def test_empty_frame_becomes_a_classified_error(monkeypatch):
    adapter = YFinanceAdapter()
    monkeypatch.setattr(adapter, "_history", lambda *a, **k: pd.DataFrame())
    with pytest.raises(ReferenceError) as exc:
        adapter.fetch("MSFT", "2023-05-12", "2023-05-13", "1m")
    assert exc.value.kind == "empty"
