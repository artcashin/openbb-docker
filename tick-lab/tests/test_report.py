"""Comparing two bar sets: price disagreements vs coverage gaps."""

import json

import pandas as pd

from tick_lab.report import compare

IDX = pd.date_range("2023-05-12 13:30:00Z", periods=3, freq="1min")


def frame(closes, index=IDX):
    n = len(closes)
    return pd.DataFrame(
        {
            "open": [100.0] * n,
            "high": [101.0] * n,
            "low": [99.0] * n,
            "close": list(closes),
            "volume": [1000] * n,
        },
        index=index[:n],
    )


def test_identical_frames_report_no_discrepancies():
    rep = compare(frame([100.0, 101.0, 102.0]), frame([100.0, 101.0, 102.0]), "MSFT", "1m")
    assert rep.bars_compared == 3
    assert rep.discrepancies == []
    assert rep.largest_close() is None


def test_differences_within_tolerance_are_not_counted():
    rep = compare(frame([100.000, 101.0, 102.0]), frame([100.005, 101.0, 102.0]), "MSFT", "1m")
    assert rep.discrepancies == []


def test_difference_beyond_tolerance_is_counted():
    rep = compare(frame([100.0, 101.0, 102.0]), frame([100.5, 101.0, 102.0]), "MSFT", "1m")
    closes = [d for d in rep.discrepancies if d.field == "close"]
    assert len(closes) == 1
    assert closes[0].diff == -0.5


def test_largest_close_discrepancy_is_reported_with_bps_and_timestamp():
    rep = compare(frame([100.0, 101.0, 90.0]), frame([100.5, 101.0, 100.0]), "MSFT", "1m")
    worst = rep.largest_close()
    assert worst.timestamp == IDX[2]
    assert worst.diff == -10.0
    assert round(worst.bps, 1) == -1000.0


def test_coverage_gaps_are_counted_separately_from_price_discrepancies():
    ours = frame([100.0, 101.0, 102.0])
    theirs = frame([100.0, 101.0], index=IDX)
    rep = compare(ours, theirs, "MSFT", "1m")
    assert rep.bars_compared == 2
    assert rep.ours_only == [IDX[2]]
    assert rep.theirs_only == []
    assert rep.discrepancies == []


def test_bars_missing_on_our_side_are_reported_too():
    ours = frame([100.0, 101.0], index=IDX)
    theirs = frame([100.0, 101.0, 102.0])
    rep = compare(ours, theirs, "MSFT", "1m")
    assert rep.theirs_only == [IDX[2]]


def test_volume_is_compared_but_never_becomes_the_largest_close():
    ours = frame([100.0])
    theirs = frame([100.0])
    theirs.loc[theirs.index[0], "volume"] = 5000
    rep = compare(ours, theirs, "MSFT", "1m")
    assert any(d.field == "volume" for d in rep.discrepancies)
    assert rep.largest_close() is None


def test_no_overlap_reports_zero_bars_compared():
    ours = frame([100.0], index=IDX)
    theirs = frame([100.0], index=pd.date_range("2024-01-02 13:30:00Z", periods=1, freq="1min"))
    rep = compare(ours, theirs, "MSFT", "1m")
    assert rep.bars_compared == 0
    assert len(rep.ours_only) == 1 and len(rep.theirs_only) == 1


def test_text_output_names_the_three_categories():
    rep = compare(frame([100.0, 101.0, 90.0]), frame([100.0, 101.0, 100.0]), "MSFT", "1m")
    text = rep.to_text()
    assert "bars compared" in text
    assert "price discrepancies" in text
    assert "coverage gaps" in text
    assert "MSFT" in text


def test_dict_output_is_json_serialisable():
    rep = compare(frame([100.0, 90.0]), frame([100.0, 100.0]), "MSFT", "1m")
    payload = json.dumps(rep.to_dict())
    assert "largest_close_discrepancy" in payload


def test_notes_are_carried_into_the_report():
    rep = compare(frame([100.0]), frame([100.0]), "MSFT", "1d", notes=["stepped down from 1m"])
    assert "stepped down from 1m" in rep.to_text()
