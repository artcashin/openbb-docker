"""Parsing FirstRate trade/quote files, including the timezone contract."""

from pathlib import Path

import pandas as pd
import pytest

from tick_lab.firstrate import FirstRateFormatError, detect_kind, parse

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name):
    return (FIXTURES / name).read_text()


def test_detects_trade_by_column_count():
    assert detect_kind("2023-05-12 09:30:00.000000,310.55,500,2,O") == "trade"


def test_detects_quote_by_column_count():
    assert detect_kind("2023-05-12 09:30:00.000000,310.5,100,1,310.6,200,2") == "quote"


def test_detects_quote_with_the_documented_eighth_field():
    """The vendor's format doc lists eight fields and names 'offer price' twice.

    Detection keys on the column count, not on that document, so an eight-field
    quote line is still a quote.
    """
    line = "2023-05-12 09:30:00.000000,310.5,100,1,310.6,200,2,310.6"
    assert detect_kind(line) == "quote"


def test_rejects_an_unrecognised_shape():
    with pytest.raises(FirstRateFormatError, match="3 field"):
        detect_kind("2023-05-12 09:30:00.000000,310.55,500")


def test_parses_trades_with_expected_columns():
    kind, df = parse(_read("MSFT_trades_sample.txt"))
    assert kind == "trade"
    assert list(df.columns) == ["price", "volume", "exchange", "conditions"]
    assert len(df) == 7


def test_trade_index_is_utc_and_shifted_from_eastern():
    """09:30 US/Eastern in May is EDT (UTC-4), so it must land at 13:30 UTC."""
    _, df = parse(_read("MSFT_trades_sample.txt"))
    assert str(df.index.tz) == "UTC"
    assert pd.Timestamp("2023-05-12 13:30:00", tz="UTC") in df.index


def test_index_is_sorted():
    _, df = parse(_read("MSFT_trades_sample.txt"))
    assert df.index.is_monotonic_increasing


def test_numeric_columns_are_numeric():
    _, df = parse(_read("MSFT_trades_sample.txt"))
    assert df["price"].dtype.kind == "f"
    assert df["volume"].dtype.kind in "iu"


def test_empty_conditions_become_empty_strings_not_nan():
    _, df = parse(_read("MSFT_trades_sample.txt"))
    assert df["conditions"].iloc[2] == ""


def test_parses_quotes():
    kind, df = parse(_read("MSFT_quotes_sample.txt"))
    assert kind == "quote"
    assert list(df.columns) == [
        "bid_price", "bid_size", "bid_exchange",
        "ask_price", "ask_size", "ask_exchange",
    ]
    assert len(df) == 2


def test_blank_lines_are_ignored():
    kind, df = parse("\n2023-05-12 09:30:00.000000,310.55,500,2,O\n\n")
    assert kind == "trade" and len(df) == 1


def test_empty_input_raises():
    with pytest.raises(FirstRateFormatError, match="no data"):
        parse("   \n\n")


def test_ragged_row_is_reported_with_its_line_number():
    text = (
        "2023-05-12 09:30:00.000000,310.55,500,2,O\n"
        "2023-05-12 09:30:01.000000,310.60,100\n"
    )
    with pytest.raises(FirstRateFormatError, match="line 2"):
        parse(text)
