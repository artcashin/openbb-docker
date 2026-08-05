"""Every q statement the cache issues.

Kept in one module so the q surface is auditable and mockable in one place.

Two measured facts shape this file:
  * `heap`, not `used`, is what approaches `wmax` and kills q, so eviction
    watches heap.
  * `delete` frees `used` but leaves `heap` untouched; only `.Q.gc[]` returns
    it. Every eviction therefore ends in a collect.
"""

import logging
import re
from datetime import datetime

from openbb_kdb.ranges import Range

logger = logging.getLogger(__name__)

_SAFE = re.compile(r"[^A-Za-z0-9_]")

# `.cache.cov` / `.cache.lru` don't exist on a fresh q process, and a q that
# crosses its -w gets silently respawned empty (see session.py) -- so this
# can't be a one-time Python-side flag. `if[not X in key `.cache; ...]` is
# idempotent on the q side (a no-op, not a wipe, if the tables already
# exist), and _conn() re-issues it whenever the session hands back a
# connection object we haven't initialised yet.
_INIT_SCHEMA = (
    "if[not `cov in key `.cache; .cache.cov: "
    "([] sym:`symbol$(); iv:`symbol$(); s:`timestamp$(); e:`timestamp$())]; "
    "if[not `lru in key `.cache; .cache.lru: "
    "([sym:`symbol$(); iv:`symbol$()] atime:`timestamp$())]"
)


class KdbStore:
    """Typed access to the cache's q state."""

    def __init__(self, session):
        self.session = session
        self._schema_conn = None  # the connection object last initialised

    def _conn(self):
        conn = self.session.connection()
        if conn is not self._schema_conn:
            conn(_INIT_SCHEMA)
            self._schema_conn = conn
        return conn

    @staticmethod
    def table_name(symbol: str, interval: str) -> str:
        """A valid q identifier for one (symbol, interval) pair."""
        sym = _SAFE.sub("_", str(symbol).strip().upper())
        iv = _SAFE.sub("_", str(interval).strip())
        return f"bars_{sym}_{iv}"

    def memory(self) -> dict:
        """`.Q.w[]` as a plain dict with str keys (used, heap, wmax, ...)."""
        return dict(self._conn()(".Q.w[]").py())

    def read_bars(self, symbol: str, interval: str, start: datetime, end: datetime):
        """Bars within [start, end] as a pandas DataFrame; empty if absent."""
        import pandas as pd

        name = self.table_name(symbol, interval)
        conn = self._conn()
        if not conn(f"`{name} in key `.").py():
            return pd.DataFrame()
        out = conn(
            f"select from {name} where t >= x, t <= y",
            _q_timestamp(start),
            _q_timestamp(end),
        ).pd()
        return out if out is not None else pd.DataFrame()

    def write_bars(self, symbol: str, interval: str, df) -> None:
        """Upsert bars, keeping the table sorted and free of duplicate stamps."""
        name = self.table_name(symbol, interval)
        conn = self._conn()
        # A leading underscore is q's drop/cut operator, not a valid
        # identifier start -- `incoming_bars` can't collide with a
        # `bars_<SYMBOL>_<INTERVAL>` cache table.
        conn["incoming_bars"] = df
        conn(
            f"{name}: `t xasc 0!(`t xkey $[`{name} in key `.; {name}; 0#incoming_bars]) "
            "upsert incoming_bars"
        )
        conn("delete incoming_bars from `.")

    def read_coverage(self, symbol: str, interval: str) -> list[Range]:
        """Ranges already fetched for this (symbol, interval)."""
        rows = self._conn()(
            "select s, e from .cache.cov where sym = x, iv = y",
            _q_symbol(symbol), _q_symbol(interval),
        ).py()
        if not rows:
            return []
        if isinstance(rows, dict):  # column-oriented result
            return list(zip(rows["s"], rows["e"]))
        return [(r[0], r[1]) for r in rows]

    def record_coverage(self, symbol: str, interval: str, r: Range) -> None:
        """Append a covered range. Coalescing happens on read, in Python."""
        self._conn()(
            ".cache.cov: .cache.cov upsert (x; y; z; w)",
            _q_symbol(symbol), _q_symbol(interval),
            _q_timestamp(r[0]), _q_timestamp(r[1]),
        )

    def touch(self, symbol: str, interval: str) -> None:
        """Record an access for LRU ordering."""
        self._conn()(
            ".cache.lru: .cache.lru upsert (x; y; .z.p)",
            _q_symbol(symbol), _q_symbol(interval),
        )

    def drop(self, symbol: str, interval: str) -> None:
        """Remove a table, its coverage, and its LRU entry, then collect."""
        name = self.table_name(symbol, interval)
        conn = self._conn()
        conn(f"if[`{name} in key `.; delete {name} from `.]")
        conn(
            "delete from `.cache.cov where sym = x, iv = y",
            _q_symbol(symbol), _q_symbol(interval),
        )
        conn(
            "delete from `.cache.lru where sym = x, iv = y",
            _q_symbol(symbol), _q_symbol(interval),
        )
        conn(".Q.gc[]")

    def evict_until_below(self, budget_bytes: int) -> list[str]:
        """Drop least-recently-used entries until heap is under budget.

        Preventive by necessity: crossing q's -w does not raise, it kills the
        process. Returns the table names actually evicted -- a name is only
        appended once its table is confirmed to have existed, so a stale LRU
        row naming an already-gone table isn't counted as an eviction.

        Logs a warning (heap left over budget) if the LRU runs out before the
        budget is reached, including if the LRU was empty to begin with:
        crossing q's -w kills the process, so "we couldn't get under budget"
        is exactly the condition an operator needs surfaced.
        """
        evicted: list[str] = []
        heap = self.memory().get("heap", 0)
        if heap <= budget_bytes:
            return evicted
        rows = self._conn()("select sym, iv, atime from .cache.lru").py() or []
        if isinstance(rows, dict):
            rows = list(zip(rows["sym"], rows["iv"], rows["atime"]))
        for sym, iv, _ in sorted(rows, key=lambda r: r[2]):
            sym = sym.decode() if isinstance(sym, bytes) else str(sym)
            iv = iv.decode() if isinstance(iv, bytes) else str(iv)
            name = self.table_name(sym, iv)
            existed = bool(self._conn()(f"`{name} in key `.").py())
            self.drop(sym, iv)
            if existed:
                evicted.append(name)
            heap = self.memory().get("heap", 0)
            if heap <= budget_bytes:
                return evicted
        logger.warning(
            "evict_until_below exhausted the LRU without reaching budget "
            "(heap=%s budget=%s)", heap, budget_bytes,
        )
        return evicted


def _q_symbol(value: str):
    import pykx as kx

    return kx.SymbolAtom(str(value))


def _q_timestamp(value: datetime):
    import pykx as kx

    return kx.TimestampAtom(value)
