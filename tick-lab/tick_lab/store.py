"""ArcticDB access for tick-lab.

This talks to ArcticDB directly rather than through OpenBB: the whole point of
the chapter is that the store is a shared network service usable from any
Python process, with no Platform install on the client.
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime
from typing import Any

import pandas as pd

from tick_lab.config import S3Config


def to_bounds(start: Any, end: Any) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    """Build an ArcticDB `date_range` pair.

    A pure-date `end` is widened to the end of that day so "2023-05-12" means
    the whole session; a datetime `end` is honoured exactly.
    """
    start_ts = None if start is None else pd.Timestamp(start)

    if end is None:
        return start_ts, None

    end_ts = pd.Timestamp(end)
    is_pure_date = isinstance(end, date_type) and not isinstance(end, datetime)
    if isinstance(end, str):
        is_pure_date = ":" not in end
    if is_pure_date:
        end_ts = end_ts.normalize() + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
    return start_ts, end_ts


class TickStore:
    """A thin, typed wrapper over an ArcticDB connection."""

    def __init__(self, cfg: S3Config):
        from arcticdb import Arctic

        self._cfg = cfg
        self._arctic = Arctic(cfg.uri)

    def _library(self, library: str, create: bool = True):
        return self._arctic.get_library(library, create_if_missing=create)

    def write(
        self,
        library: str,
        symbol: str,
        frame: pd.DataFrame,
        metadata: dict | None = None,
    ) -> None:
        """Overwrite `symbol`, so re-running a load is idempotent."""
        self._library(library).write(symbol, frame, metadata=metadata)

    def read(
        self,
        library: str,
        symbol: str,
        start: Any = None,
        end: Any = None,
    ) -> pd.DataFrame:
        """Read a symbol, filtering by date range on the server where possible."""
        bounds = to_bounds(start, end)
        date_range = None if bounds == (None, None) else bounds
        return self._library(library, create=False).read(
            symbol, date_range=date_range
        ).data

    def list_symbols(self, library: str) -> list[str]:
        if not self._arctic.has_library(library):
            return []
        return sorted(self._library(library, create=False).list_symbols())

    def has(self, library: str, symbol: str) -> bool:
        return symbol in self.list_symbols(library)
