"""Read-only MCP server exposing ArcticDB and kdb+ discovery/query tools.

Runs beside openbb-api inside the NAS shared network namespace
(network_mode: service:tailscale), serving streamable-http MCP on
127.0.0.1:6902 (path /mcp/), published by Tailscale Serve on
https://openbb.tailb9874f.ts.net:8444/mcp/.

Why it exists: the REST arcticdb/kdb providers only answer known-symbol OHLCV
queries and return 204 against this store's raw quote/trade tick data. These
tools give an agent discovery (libraries/symbols/tables/schemas) plus capped
raw reads.

Config (already present in the NAS env files):
  ARCTICDB_URI       s3://100.122.250.60:openbb?port=9000&...  (raw tailnet IP;
                     MagicDNS names do NOT resolve inside NAS containers)
  KX_HOST / KX_PORT  kdb IPC target; KX_PORT may be the combined
                     "127.0.0.1:5000" form used by deps.env — parsed here.
  STORES_HOST/PORT   bind address, default 127.0.0.1:6902.

Safety: all tools are read-only. kdb access NEVER interpolates user input into
q source: the table name is validated against tables[], symbols/timestamps are
regex-gated and passed as typed ARGUMENTS to fixed q lambdas (_Q_META,
_Q_SELECT). Strings cross IPC as char vectors (bytes) and are cast server-side
(`$s / "P"$st), so a hostile string can never become q code.
"""
import json
import os
import re

from fastmcp import FastMCP

MAX_ROWS = 10_000
_IDENT_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_TIME_RE = re.compile(r"^[0-9T:. \-]{1,35}$")  # ISO-ish date/timestamp text

# Fixed q sources. Only ever sent verbatim with arguments — never edited.
_Q_META = '{[t] 0!meta get `$t}'
_Q_SELECT = (
    '{[t;sc;tc;s;st;et;n] '
    'tbl:get `$t; '
    'm:(count tbl)#1b; '
    'if[(count s)>0; m:m and (tbl `$sc)=`$s]; '
    'if[(count st)>0; m:m and (tbl `$tc)>="P"$st]; '
    'if[(count et)>0; m:m and (tbl `$tc)<="P"$et]; '
    'n sublist select from tbl where m}'
)
_KDB_TIME_TYPES = tuple("pmdznuvt")  # q temporal type chars

mcp = FastMCP("stores")


# ---------- shared helpers ----------

def _arctic():
    from arcticdb import Arctic  # lazy: unit tests mock the module

    return Arctic(os.environ["ARCTICDB_URI"])


def _kdb_conn():
    import pykx  # lazy: unit tests mock the module

    host = os.environ.get("KX_HOST", "127.0.0.1")
    port = os.environ.get("KX_PORT", "5000")
    if ":" in port:  # deps.env combined form: KX_PORT=127.0.0.1:5000
        host, port = port.rsplit(":", 1)
    return pykx.SyncQConnection(host=host, port=int(port))


def _decode_df(df):
    """Decode q symbol/char cells that arrive as bytes."""

    def dec(v):
        return v.decode() if isinstance(v, (bytes, bytearray)) else v

    func = getattr(df, "map", None) or df.applymap  # pandas >=2.1 / older
    return func(dec)


def _records(df):
    """DataFrame -> JSON-safe row dicts (ISO-8601 timestamps)."""
    return json.loads(df.to_json(orient="records", date_format="iso"))


def _check_ident(kind, value):
    if not _IDENT_RE.match(value or ""):
        raise ValueError(f"invalid {kind} {value!r}: must match {_IDENT_RE.pattern}")


# ---------- ArcticDB tools ----------

def arctic_list_libraries() -> list[str]:
    """List ArcticDB libraries in the store (e.g. 'ticks')."""
    return sorted(_arctic().list_libraries())


def arctic_list_symbols(library: str) -> list[str]:
    """List symbols stored in an ArcticDB library."""
    ac = _arctic()
    if library not in ac.list_libraries():
        raise ValueError(
            f"unknown library {library!r}; call arctic_list_libraries first"
        )
    return sorted(ac[library].list_symbols())


def arctic_read(
    library: str,
    symbol: str,
    start: str | None = None,
    end: str | None = None,
    tail_rows: int = 1000,
) -> dict:
    """Read a symbol from an ArcticDB library.

    start/end are ISO dates/timestamps filtering the stored index
    (ArcticDB date_range). Returns at most tail_rows rows (the most recent
    ones in range, hard cap 10000) as JSON records with ISO timestamps.
    """
    import pandas as pd  # available: arcticdb depends on pandas

    tail_rows = max(1, min(int(tail_rows), MAX_ROWS))
    ac = _arctic()
    if library not in ac.list_libraries():
        raise ValueError(
            f"unknown library {library!r}; call arctic_list_libraries first"
        )
    lib = ac[library]
    if symbol not in lib.list_symbols():
        raise ValueError(
            f"unknown symbol {symbol!r} in {library!r}; call arctic_list_symbols first"
        )
    date_range = None
    if start or end:
        date_range = (
            pd.Timestamp(start) if start else None,
            pd.Timestamp(end) if end else None,
        )
    df = lib.read(symbol, date_range=date_range).data
    total = len(df)
    df = df.tail(tail_rows).reset_index()
    return {
        "library": library,
        "symbol": symbol,
        "total_rows_in_range": total,
        "returned_rows": len(df),
        "rows": _records(df),
    }


# ---------- kdb+ tools ----------

def _tables(conn) -> list[str]:
    raw = conn("tables[]").py()
    return sorted(
        t.decode() if isinstance(t, (bytes, bytearray)) else str(t) for t in raw
    )


def _meta_df(conn, table):
    return _decode_df(conn(_Q_META, table.encode()).pd())


def _cols_by_type(meta) -> tuple[str, str]:
    """(symbol_column, time_column) from a q meta frame; '' when absent."""
    sym_col = time_col = ""
    for _, row in meta.iterrows():
        if not sym_col and row["t"] == "s":
            sym_col = row["c"]
        if not time_col and row["t"] in _KDB_TIME_TYPES:
            time_col = row["c"]
    return sym_col, time_col


def kdb_tables() -> list[str]:
    """List tables defined in the kdb+ server."""
    with _kdb_conn() as conn:
        return _tables(conn)


def kdb_table_schema(table: str) -> list[dict]:
    """Column names/types of a kdb+ table (q meta: c=column, t=type char)."""
    _check_ident("table", table)
    with _kdb_conn() as conn:
        if table not in _tables(conn):
            raise ValueError(f"unknown table {table!r}; call kdb_tables first")
        return _records(_meta_df(conn, table))


def kdb_select(
    table: str,
    symbol: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    limit: int = 1000,
) -> dict:
    """Filtered read of a kdb+ table (parameterized; never raw q).

    symbol filters the table's symbol-typed column; start_time/end_time are
    ISO timestamps filtering the first temporal column. Returns at most
    `limit` rows (hard cap 10000).
    """
    _check_ident("table", table)
    if symbol is not None:
        _check_ident("symbol", symbol)
    for name, val in (("start_time", start_time), ("end_time", end_time)):
        if val is not None and not _TIME_RE.match(val):
            raise ValueError(f"invalid {name} {val!r}")
    limit = max(1, min(int(limit), MAX_ROWS))

    with _kdb_conn() as conn:
        if table not in _tables(conn):
            raise ValueError(f"unknown table {table!r}; call kdb_tables first")
        sym_col, time_col = _cols_by_type(_meta_df(conn, table))
        if symbol and not sym_col:
            raise ValueError(f"table {table!r} has no symbol-typed column")
        if (start_time or end_time) and not time_col:
            raise ValueError(f"table {table!r} has no temporal column")
        res = conn(
            _Q_SELECT,
            table.encode(),
            sym_col.encode(),
            time_col.encode(),
            (symbol or "").encode(),
            (start_time or "").encode(),
            (end_time or "").encode(),
            limit,
        )
        df = _decode_df(res.pd())
        return {"table": table, "returned_rows": len(df), "rows": _records(df)}


# Registered here (not via decorator) so tests import the plain functions.
for _fn in (
    arctic_list_libraries,
    arctic_list_symbols,
    arctic_read,
    kdb_tables,
    kdb_table_schema,
    kdb_select,
):
    mcp.tool(_fn)


if __name__ == "__main__":
    mcp.run(
        transport="http",
        host=os.environ.get("STORES_HOST", "127.0.0.1"),
        port=int(os.environ.get("STORES_PORT", "6902")),
    )
