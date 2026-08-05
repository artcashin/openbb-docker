"""CLI argument handling and the filename -> (symbol, kind) convention."""

import argparse
import zipfile

import pytest

from tick_lab.cli import ADAPTERS, _iter_members, date_or_datetime, main, symbol_from_filename


@pytest.mark.parametrize(
    "name,expected",
    [
        ("MSFT_trades_2023-05-12.txt", ("MSFT", "trade")),
        ("GOOG_quotes_2023-05-12.txt", ("GOOG", "quote")),
        ("MSFT_trade.txt", ("MSFT", "trade")),
        ("msft_quotes.csv", ("MSFT", "quote")),
    ],
)
def test_symbol_and_kind_from_filename(name, expected):
    assert symbol_from_filename(name) == expected


def test_unrecognised_filename_is_rejected():
    with pytest.raises(ValueError, match="cannot tell"):
        symbol_from_filename("random-data.txt")


def test_yfinance_is_registered():
    assert "yfinance" in ADAPTERS


def test_no_subcommand_exits_nonzero(capsys):
    assert main([]) == 2


def test_unknown_reference_is_rejected(capsys):
    with pytest.raises(SystemExit):
        main(["compare", "--symbol", "MSFT", "--date", "2023-05-12",
              "--reference", "nope"])


def test_bad_config_is_reported_without_a_traceback(capsys, monkeypatch):
    for key in ("ARCTICDB_S3_ENDPOINT", "ARCTICDB_S3_BUCKET",
                "ARCTICDB_S3_ACCESS", "ARCTICDB_S3_SECRET"):
        monkeypatch.delenv(key, raising=False)
    code = main(["compare", "--symbol", "MSFT", "--date", "2023-05-12"])
    assert code == 1
    assert "ARCTICDB_S3_ENDPOINT" in capsys.readouterr().err


# --- --date/--end validation -------------------------------------------
#
# argparse gives --date/--end no type= validator in the brief's sample code,
# so a typo like "2023-13-45" or "last-tuesday" would reach pandas/ArcticDB
# unvalidated and surface as a confusing parse error deep in the stack.
# date_or_datetime() is the type= callable that rejects it right at the
# argparse boundary instead, with a message naming the expected format.


@pytest.mark.parametrize(
    "value",
    [
        "2023-05-12",
        "2023-05-12T15:00:00",
        "2023-05-12 15:00:00",
        "2023-05-12T00:00:00",
    ],
)
def test_date_or_datetime_accepts_what_the_store_supports(value):
    assert date_or_datetime(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "2023-13-45",
        "last-tuesday",
        "05/12/2023",
        "2023-02-30",
        "not-a-date",
    ],
)
def test_date_or_datetime_rejects_bad_shapes(value):
    with pytest.raises(argparse.ArgumentTypeError, match="YYYY-MM-DD"):
        date_or_datetime(value)


def test_bad_date_is_rejected_by_argparse_before_reaching_the_store(capsys):
    with pytest.raises(SystemExit):
        main(["compare", "--symbol", "MSFT", "--date", "2023-13-45"])
    assert "YYYY-MM-DD" in capsys.readouterr().err


def test_bad_end_is_rejected_by_argparse_before_reaching_the_store(capsys):
    with pytest.raises(SystemExit):
        main(["compare", "--symbol", "MSFT", "--date", "2023-05-12",
              "--end", "last-tuesday"])
    assert "YYYY-MM-DD" in capsys.readouterr().err


# --- _iter_members: `load` takes a zip OR an already-extracted directory --
#
# The real FirstRate zip is third-party licensed and never committed, so a
# reader may well have extracted it already -- both paths matter.


def test_iter_members_reads_an_extracted_directory(tmp_path):
    (tmp_path / "MSFT_trades_2023-05-12.txt").write_text("a")
    (tmp_path / "GOOG_quotes_2023-05-12.txt").write_text("b")
    (tmp_path / "_readme.txt").write_text("skip me")
    (tmp_path / ".DS_Store").write_text("finder noise")

    names = sorted(name for name, _ in _iter_members(tmp_path))
    assert names == ["GOOG_quotes_2023-05-12.txt", "MSFT_trades_2023-05-12.txt"]


def test_iter_members_reads_a_zip(tmp_path):
    zpath = tmp_path / "sample.zip"
    with zipfile.ZipFile(zpath, "w") as archive:
        archive.writestr("MSFT_trades_2023-05-12.txt", "a")
        archive.writestr("GOOG_quotes_2023-05-12.txt", "b")

    names = sorted(name for name, _ in _iter_members(zpath))
    assert names == ["GOOG_quotes_2023-05-12.txt", "MSFT_trades_2023-05-12.txt"]


def test_iter_members_skips_macos_zip_noise(tmp_path):
    # A zip built on macOS commonly carries a "__MACOSX/" resource-fork
    # sibling for every real entry -- checking only the leaf filename (not
    # the full path) lets "__MACOSX/junk" slip through as a bogus member.
    zpath = tmp_path / "sample.zip"
    with zipfile.ZipFile(zpath, "w") as archive:
        archive.writestr("MSFT_trades_2023-05-12.txt", "a")
        archive.writestr("__MACOSX/._MSFT_trades_2023-05-12.txt", "junk")
        archive.writestr("__MACOSX/junk", "junk")

    names = [name for name, _ in _iter_members(zpath)]
    assert names == ["MSFT_trades_2023-05-12.txt"]
