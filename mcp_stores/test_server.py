"""Unit tests for mcp_stores/server.py with arcticdb and pykx MOCKED.

Runs on the Mac with no arcticdb/pykx/kdb installed:
    cd /Users/artcashin/Developer/openbb-docker
    uv run --with fastmcp --with pytest --with pandas python -m pytest mcp_stores/test_server.py -v

server.py imports arcticdb/pykx lazily inside helper functions, so these tests
inject fakes via sys.modules before any tool touches them.
"""
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server  # noqa: E402


# ---------- fakes ----------

class K:
    """Stand-in for a pykx K result object."""

    def __init__(self, value):
        self.value = value

    def py(self):
        return self.value

    def pd(self):
        return self.value


class FakeConn:
    """Records every (query, args) call; answers from a canned dict."""

    def __init__(self):
        self.responses = {}
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __call__(self, query, *args):
        self.calls.append((query, args))
        return K(self.responses[query])


@pytest.fixture
def arctic_store(monkeypatch):
    monkeypatch.setenv("ARCTICDB_URI", "s3://100.122.250.60:openbb?port=9000")
    store = MagicMock()
    monkeypatch.setitem(
        sys.modules, "arcticdb", types.SimpleNamespace(Arctic=lambda uri: store)
    )
    return store


@pytest.fixture
def kdb_conn(monkeypatch):
    conn = FakeConn()
    monkeypatch.setenv("KX_PORT", "127.0.0.1:5000")
    monkeypatch.setitem(
        sys.modules,
        "pykx",
        types.SimpleNamespace(SyncQConnection=lambda host, port: conn),
    )
    return conn


# ---------- arctic ----------

def test_arctic_list_libraries_sorted(arctic_store):
    arctic_store.list_libraries.return_value = ["ticks", "hrp_prices"]
    assert server.arctic_list_libraries() == ["hrp_prices", "ticks"]


def test_arctic_list_symbols_unknown_library_raises(arctic_store):
    arctic_store.list_libraries.return_value = ["ticks"]
    with pytest.raises(ValueError, match="unknown library"):
        server.arctic_list_symbols("nope")


def test_arctic_read_caps_rows_and_serializes_timestamps(arctic_store):
    idx = pd.date_range("2026-01-01", periods=5, freq="1min")
    df = pd.DataFrame({"bid": [1.0] * 5, "ask": [2.0] * 5}, index=idx)
    arctic_store.list_libraries.return_value = ["ticks"]
    lib = arctic_store.__getitem__.return_value
    lib.list_symbols.return_value = ["AAPL"]
    lib.read.return_value = SimpleNamespace(data=df)

    out = server.arctic_read("ticks", "AAPL", tail_rows=3)

    assert out["total_rows_in_range"] == 5
    assert out["returned_rows"] == 3
    # tail(3) of a 5-row 1-min series starts at 00:02, ISO-formatted
    assert out["rows"][0]["index"].startswith("2026-01-01T00:02")
    lib.read.assert_called_once_with("AAPL", date_range=None)


def test_arctic_read_passes_date_range(arctic_store):
    df = pd.DataFrame({"bid": [1.0]}, index=pd.date_range("2026-01-01", periods=1))
    arctic_store.list_libraries.return_value = ["ticks"]
    lib = arctic_store.__getitem__.return_value
    lib.list_symbols.return_value = ["AAPL"]
    lib.read.return_value = SimpleNamespace(data=df)

    server.arctic_read("ticks", "AAPL", start="2026-01-01", end="2026-01-02")

    _, kwargs = lib.read.call_args
    assert kwargs["date_range"] == (
        pd.Timestamp("2026-01-01"),
        pd.Timestamp("2026-01-02"),
    )


def test_arctic_read_unknown_symbol_raises(arctic_store):
    arctic_store.list_libraries.return_value = ["ticks"]
    lib = arctic_store.__getitem__.return_value
    lib.list_symbols.return_value = ["AAPL"]
    with pytest.raises(ValueError, match="unknown symbol"):
        server.arctic_read("ticks", "MSFT")


# ---------- kdb ----------

def test_kdb_tables_decodes_and_sorts(kdb_conn):
    kdb_conn.responses["tables[]"] = [b"trade", b"quote"]
    assert server.kdb_tables() == ["quote", "trade"]


def test_kdb_select_rejects_unknown_table(kdb_conn):
    kdb_conn.responses["tables[]"] = [b"trade"]
    with pytest.raises(ValueError, match="unknown table"):
        server.kdb_select("evil")


def test_kdb_select_rejects_hostile_symbol_before_connecting(kdb_conn):
    with pytest.raises(ValueError, match="invalid symbol"):
        server.kdb_select("trade", symbol='AAPL"; system "ls')
    assert kdb_conn.calls == []  # rejected before any IPC


def test_kdb_select_is_parameterized(kdb_conn):
    meta = pd.DataFrame(
        {"c": ["time", "sym", "price"], "t": ["p", "s", "f"],
         "f": ["", "", ""], "a": ["", "", ""]}
    )
    sel = pd.DataFrame(
        {"time": pd.to_datetime(["2026-01-01 09:30:00"]),
         "sym": [b"AAPL"], "price": [1.5]}
    )
    kdb_conn.responses["tables[]"] = [b"trade"]
    kdb_conn.responses[server._Q_META] = meta
    kdb_conn.responses[server._Q_SELECT] = sel

    out = server.kdb_select(
        "trade", symbol="AAPL", start_time="2026-01-01", limit=500
    )

    query, args = kdb_conn.calls[-1]
    assert query == server._Q_SELECT          # fixed q source, never rebuilt
    assert "AAPL" not in query                # user data NOT interpolated
    assert args == (b"trade", b"sym", b"time", b"AAPL", b"2026-01-01", b"", 500)
    assert out["returned_rows"] == 1
    assert out["rows"][0]["sym"] == "AAPL"    # bytes decoded


def test_kdb_select_caps_limit(kdb_conn):
    meta = pd.DataFrame(
        {"c": ["price"], "t": ["f"], "f": [""], "a": [""]}
    )
    kdb_conn.responses["tables[]"] = [b"trade"]
    kdb_conn.responses[server._Q_META] = meta
    kdb_conn.responses[server._Q_SELECT] = pd.DataFrame({"price": [1.0]})

    server.kdb_select("trade", limit=10_000_000)

    _, args = kdb_conn.calls[-1]
    assert args[-1] == server.MAX_ROWS
