"""The read-through algorithm.

Serve what is cached, fetch only what is missing, store it, return the merge.
The cache is never allowed to be the reason a request fails: any kdb error
degrades to a straight upstream call reported as cache="bypass".
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta

from openbb_kdb.config import KdbConfig
from openbb_kdb.ranges import Range, coalesce, interval_step, subtract, trim_tail
from openbb_kdb.upstream import fetch_gap

logger = logging.getLogger(__name__)

_OVERLAP_BARS = 3  # tail bars re-fetched and compared, to catch corporate actions


def last_complete_boundary(interval: str, now: datetime) -> datetime:
    """The end of the most recent FULLY FORMED bar.

    Coverage is never recorded past this point, so the still-forming bar is
    refetched on every request rather than cached half-built.
    """
    step = interval_step(interval)
    if step >= timedelta(days=1):
        floor = datetime(now.year, now.month, now.day)
    else:
        seconds = int(step.total_seconds())
        epoch = datetime(1970, 1, 1)
        elapsed = int((now - epoch).total_seconds())
        floor = epoch + timedelta(seconds=elapsed - (elapsed % seconds))
    return floor - timedelta(microseconds=1)


class ReadThroughCache:
    """kdb-backed read-through cache over an upstream provider."""

    def __init__(self, store, config: KdbConfig):
        self.store = store
        self.config = config
        self._fetch_gap = fetch_gap
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    def _lock(self, symbol: str, interval: str) -> asyncio.Lock:
        key = (symbol, interval)
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def get(
        self,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
        model: str,
        params: dict,
        credentials: dict | None,
        now: datetime | None = None,
    ) -> tuple[list[dict], dict]:
        """Return (rows, metadata) for the requested window."""
        async with self._lock(symbol, interval):
            return await self._get(
                symbol, interval, start, end, model, params, credentials,
                now or datetime.now(),
            )

    async def _get(self, symbol, interval, start, end, model, params, credentials, now):
        meta = {
            "cache": "miss", "rows_from_cache": 0, "rows_from_upstream": 0,
            "gaps_fetched": 0, "upstream_ms": 0.0, "kdb_ms": 0.0,
        }
        try:
            return await self._read_through(
                symbol, interval, start, end, model, params, credentials, now, meta
            )
        except Exception as exc:  # noqa: BLE001 - the cache never fails a request
            logger.warning("kdb cache bypassed for %s %s: %s", symbol, interval, exc)
            rows, elapsed = await self._timed_fetch(
                model, params, credentials, symbol, start, end
            )
            meta.update(
                cache="bypass", rows_from_upstream=len(rows),
                gaps_fetched=1, upstream_ms=elapsed,
            )
            return rows, meta

    async def _read_through(
        self, symbol, interval, start, end, model, params, credentials, now, meta
    ):
        step = interval_step(interval)
        boundary = last_complete_boundary(interval, now)

        t0 = time.perf_counter()
        covered = coalesce(self.store.read_coverage(symbol, interval), step)
        meta["kdb_ms"] += (time.perf_counter() - t0) * 1000

        # The tail is never covered, so a request reaching into the present
        # always re-fetches the newest bars.
        effective = [r for r in (trim_tail(c, boundary) for c in covered) if r]
        gaps = subtract((start, end), effective, step)

        if covered and effective:
            await self._check_corporate_action(
                symbol, interval, effective, model, params, credentials, meta
            )
            t0 = time.perf_counter()
            covered = coalesce(self.store.read_coverage(symbol, interval), step)
            meta["kdb_ms"] += (time.perf_counter() - t0) * 1000
            effective = [r for r in (trim_tail(c, boundary) for c in covered) if r]
            gaps = subtract((start, end), effective, step)

        meta["cache"] = "hit" if not gaps else ("partial" if effective else "miss")

        for gap in gaps:
            rows, elapsed = await self._timed_fetch(
                model, params, credentials, symbol, gap[0], gap[1]
            )
            meta["upstream_ms"] += elapsed
            meta["rows_from_upstream"] += len(rows)
            meta["gaps_fetched"] += 1
            if not rows:
                continue
            frame = _to_frame(rows)
            t0 = time.perf_counter()
            self.store.write_bars(symbol, interval, frame)
            recorded = trim_tail(gap, boundary)
            if recorded:
                self.store.record_coverage(symbol, interval, recorded)
            meta["kdb_ms"] += (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        self.store.touch(symbol, interval)
        budget = int(self.config.memory_mb * 1024 * 1024 * self.config.watermark)
        self.store.evict_until_below(budget)
        frame = self.store.read_bars(symbol, interval, start, end)
        meta["kdb_ms"] += (time.perf_counter() - t0) * 1000

        rows = _from_frame(frame)
        meta["rows_from_cache"] = max(len(rows) - meta["rows_from_upstream"], 0)
        return rows, meta

    async def _check_corporate_action(
        self, symbol, interval, effective, model, params, credentials, meta
    ):
        """Compare re-fetched tail bars against cached ones.

        A split or dividend rewrites the whole adjusted series. The tail is
        being re-fetched anyway, so overlapping it with cached bars catches
        that for no extra traffic.
        """
        newest_end = max(e for _, e in effective)
        step = interval_step(interval)
        overlap_start = newest_end - step * _OVERLAP_BARS
        cached = self.store.read_bars(symbol, interval, overlap_start, newest_end)
        if cached is None or cached.empty:
            return
        fresh, elapsed = await self._timed_fetch(
            model, params, credentials, symbol, overlap_start, newest_end
        )
        meta["upstream_ms"] += elapsed
        if not fresh:
            return
        fresh_frame = _to_frame(fresh)
        merged = cached.merge(fresh_frame, on="t", suffixes=("_old", "_new"))
        if merged.empty:
            return
        drift = (merged["close_old"] - merged["close_new"]).abs() > 1e-6
        if bool(drift.any()):
            logger.info("adjusted history changed for %s; dropping cache", symbol)
            self.store.drop(symbol, interval)

    async def _timed_fetch(self, model, params, credentials, symbol, start, end):
        call = dict(params)
        call["symbol"] = symbol
        call["start_date"] = start.date() if isinstance(start, datetime) else start
        call["end_date"] = end.date() if isinstance(end, datetime) else end
        t0 = time.perf_counter()
        rows = await self._fetch_gap(self.config.upstream, model, call, credentials)
        return rows, (time.perf_counter() - t0) * 1000


def _to_frame(rows: list[dict]):
    import pandas as pd

    frame = pd.DataFrame(rows)
    stamp = "date" if "date" in frame.columns else frame.columns[0]
    frame = frame.rename(columns={stamp: "t"})
    frame["t"] = pd.to_datetime(frame["t"])
    return frame.sort_values("t").reset_index(drop=True)


def _from_frame(frame) -> list[dict]:
    if frame is None or getattr(frame, "empty", True):
        return []
    out = frame.rename(columns={"t": "date"})
    return out.to_dict(orient="records")
