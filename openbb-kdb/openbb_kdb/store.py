"""Every q statement the cache issues.

Kept in one module so the q surface is auditable and mockable in one place.

Two measured facts shape this file:
  * `heap`, not `used`, is what approaches `wmax` and kills q, so eviction
    watches heap.
  * `delete` frees `used` but leaves `heap` untouched; only `.Q.gc[]` returns
    it. Every eviction therefore ends in a collect.
"""

import re
from datetime import datetime

Range = tuple[datetime, datetime]

_SAFE = re.compile(r"[^A-Za-z0-9_]")


class KdbStore:
    """Typed access to the cache's q state."""

    def __init__(self, session):
        self.session = session

    def _conn(self):
        return self.session.connection()

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
        conn[f"_incoming"] = df
        conn(f"{name}: `t xasc 0!(`t xkey $[`{name} in key `.; {name}; 0#_incoming]) upsert _incoming")
        conn("delete _incoming from `.")

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
        process. Returns the table names evicted.
        """
        evicted: list[str] = []
        if self.memory().get("heap", 0) <= budget_bytes:
            return evicted
        rows = self._conn()("select sym, iv, atime from .cache.lru").py() or []
        if isinstance(rows, dict):
            rows = list(zip(rows["sym"], rows["iv"], rows["atime"]))
        for sym, iv, _ in sorted(rows, key=lambda r: r[2]):
            sym = sym.decode() if isinstance(sym, bytes) else str(sym)
            iv = iv.decode() if isinstance(iv, bytes) else str(iv)
            self.drop(sym, iv)
            evicted.append(self.table_name(sym, iv))
            if self.memory().get("heap", 0) <= budget_bytes:
                break
        return evicted


def _q_symbol(value: str):
    import pykx as kx

    return kx.SymbolAtom(str(value))


def _q_timestamp(value: datetime):
    import pykx as kx

    return kx.TimestampAtom(value)
