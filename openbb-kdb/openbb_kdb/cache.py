"""The read-through algorithm.

Serve what is cached, fetch only what is missing, store it, return the merge.
The cache is never allowed to be the reason a request fails: any kdb error
degrades to a straight upstream call reported as cache="bypass".

Time is handled in ONE convention throughout: a range is (start, end) with BOTH
ends inclusive, and both ends are BAR TIMESTAMPS -- the stamp a provider puts on
the bar, not the instant the period ends. `last_complete_boundary` therefore
returns the timestamp of the last completed bar, so it composes with
`ranges.trim_tail` / `ranges.subtract`, which advance by whole bars.

Note on clocks: `datetime.now()` is naive LOCAL time, while provider bar
timestamps are typically exchange-local or UTC. The boundary is consequently
only as accurate as the container's timezone; it is deliberately conservative
in the sense that being an hour "early" merely refetches one extra bar, and
callers that care pass an explicit `now`.
"""

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta

from openbb_kdb.config import KdbConfig
from openbb_kdb.ranges import coalesce, interval_step, subtract, trim_tail
from openbb_kdb.upstream import fetch_gap

logger = logging.getLogger(__name__)

_OVERLAP_BARS = 3  # tail bars re-fetched and compared, to catch corporate actions

# One asyncio.Lock is kept per (event loop, symbol, interval). The dict is
# bounded so a long-lived process serving many symbols cannot grow it forever.
_MAX_LOCKS = 512

_MONTH_UNITS = frozenset({"mo", "mon", "month", "months"})
_INTERVAL_RE = re.compile(r"(\d*)\s*([a-zA-Z]+)")

# Column names accepted as a row's timestamp, in preference order.
_STAMP_KEYS = ("date", "t", "timestamp", "datetime")

# Preference order for the column the corporate-action backstop compares.
_PRICE_PREFERENCE = ("close", "adj_close", "adjusted_close", "close_adj", "price")
# Numeric columns that legitimately change without any corporate action.
_NON_PRICE = frozenset(
    {"volume", "open_interest", "transactions", "dividend", "split_ratio", "change",
     "change_percent"}
)


def _interval_parts(interval: str) -> tuple[int, str]:
    """(multiplier, unit) for an OpenBB interval string ('1d', '5m', '3mo')."""
    m = _INTERVAL_RE.fullmatch(str(interval).strip())
    if not m:
        raise ValueError(f"Could not parse interval {interval!r}.")
    return int(m.group(1) or 1), m.group(2).lower()


def _shift_months(when: datetime, months: int) -> datetime:
    """`when` moved by `months`, snapped to the first of the month."""
    index = when.year * 12 + (when.month - 1) + months
    return datetime(index // 12, index % 12 + 1, 1)


def last_complete_boundary(interval: str, now: datetime) -> datetime:
    """Timestamp of the most recent FULLY FORMED bar.

    Coverage is never recorded past this point, so the still-forming bar is
    refetched on every request rather than cached half-built. The value is a
    bar timestamp, matching the inclusive-range convention used by
    `ranges.trim_tail` and `ranges.subtract`: for daily bars and
    now=2025-06-10T15:00 it is 2025-06-09T00:00:00 (yesterday's bar), so a
    later request the same day sees 2025-06-10 as a gap and refetches it.

    Weekly and monthly bars are only complete once their week/month has ended,
    which is why they cannot be derived from a fixed timedelta.
    """
    count, unit = _interval_parts(interval)
    if unit in _MONTH_UNITS:
        return _shift_months(datetime(now.year, now.month, 1), -count)
    if unit.startswith("w"):
        week_start = datetime(now.year, now.month, now.day) - timedelta(days=now.weekday())
        return week_start - timedelta(weeks=count)
    step = interval_step(interval)
    if step >= timedelta(days=1):
        return datetime(now.year, now.month, now.day) - step
    seconds = int(step.total_seconds())
    epoch = datetime(1970, 1, 1)
    elapsed = int((now - epoch).total_seconds())
    floor = epoch + timedelta(seconds=elapsed - (elapsed % seconds))
    return floor - step


class _FetchLog:
    """What this call has already pulled from upstream.

    Kept outside `_read_through` so the bypass path can reuse work instead of
    refetching a window it has already paid for.
    """

    __slots__ = ("rows", "ranges")

    def __init__(self):
        self.rows: list[dict] = []
        self.ranges: list[tuple[datetime, datetime]] = []


class ReadThroughCache:
    """kdb-backed read-through cache over an upstream provider."""

    def __init__(self, store, config: KdbConfig):
        self.store = store
        self.config = config
        self._fetch_gap = fetch_gap
        # Keyed by the running event loop as well as (symbol, interval):
        # OpenBB may run fetchers from more than one loop, and an
        # asyncio.Lock created on a different loop does not serialise
        # correctly (and raises on older Pythons).
        self._locks: dict[tuple, asyncio.Lock] = {}

    def _lock(self, symbol: str, interval: str) -> asyncio.Lock:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # no loop (sync caller): one bucket is fine
            loop = None
        key = (loop, symbol, interval)
        # pop + reinsert keeps the dict in least-recently-used order. There is
        # no await in between, so a lock held by another task is never lost.
        lock = self._locks.pop(key, None)
        if lock is None:
            self._prune_locks()
            lock = asyncio.Lock()
        self._locks[key] = lock
        return lock

    def _prune_locks(self) -> None:
        """Drop least-recently-used, currently-unheld locks."""
        if len(self._locks) < _MAX_LOCKS:
            return
        for key, lock in list(self._locks.items()):
            if len(self._locks) < _MAX_LOCKS:
                return
            if not lock.locked():
                del self._locks[key]

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
                now or datetime.now(),  # naive local time; see module docstring
            )

    async def _get(self, symbol, interval, start, end, model, params, credentials, now):
        meta = {
            "cache": "miss", "rows_from_cache": 0, "rows_from_upstream": 0,
            "gaps_fetched": 0, "upstream_ms": 0.0, "kdb_ms": 0.0,
        }
        log = _FetchLog()
        try:
            return await self._read_through(
                symbol, interval, start, end, model, params, credentials, now, meta, log
            )
        except Exception as exc:  # noqa: BLE001 - the cache never fails a request
            logger.warning("kdb cache bypassed for %s %s: %s", symbol, interval, exc)
            return await self._bypass(
                symbol, interval, start, end, model, params, credentials, meta, log
            )

    async def _bypass(self, symbol, interval, start, end, model, params, credentials,
                      meta, log):
        """Serve the request straight from upstream, reusing work already done.

        Whatever this call already fetched before the store failed is kept: only
        the parts of the window nobody has fetched yet go back to the network.
        The metadata stays ADDITIVE -- `upstream_ms` and `gaps_fetched` already
        carry the earlier fetches and must not be overwritten by the retry.
        """
        try:
            step = interval_step(interval)
            remaining = subtract((start, end), log.ranges, step)
        except Exception:  # noqa: BLE001 - an unparseable interval must not fail the call
            remaining = [] if log.ranges else [(start, end)]
        for gap in remaining:
            await self._fetch(
                model, params, credentials, symbol, interval, gap[0], gap[1], meta, log
            )
        rows = _dedupe(log.rows)
        meta["cache"] = "bypass"
        # Nothing here came out of kdb, so the split is unambiguous -- and it
        # still satisfies rows_from_cache + rows_from_upstream == len(rows).
        meta["rows_from_upstream"] = len(rows)
        meta["rows_from_cache"] = 0
        return rows, meta

    async def _read_through(
        self, symbol, interval, start, end, model, params, credentials, now, meta, log
    ):
        step = interval_step(interval)
        boundary = last_complete_boundary(interval, now)
        args = (symbol, interval, start, end, model, params, credentials, meta, log,
                step, boundary)
        rows = await self._pass(*args, backstop=True)
        if rows is None:
            # The backstop found rewritten history and dropped the symbol; the
            # second pass refills from scratch and cannot drop again.
            rows = await self._pass(*args, backstop=False)
        _attribute_rows(rows or [], log, meta)
        return rows or [], meta

    async def _pass(
        self, symbol, interval, start, end, model, params, credentials, meta, log,
        step, boundary, backstop,
    ):
        """One read-through attempt. Returns served rows, or None if the symbol
        was dropped mid-flight and the whole thing has to start over."""
        t0 = time.perf_counter()
        covered = coalesce(self.store.read_coverage(symbol, interval), step)
        meta["kdb_ms"] += (time.perf_counter() - t0) * 1000

        # The tail is never covered, so a request reaching into the present
        # always re-fetches the newest bars.
        effective = [r for r in (trim_tail(c, boundary) for c in covered) if r]
        gaps = subtract((start, end), effective, step)
        meta["cache"] = "hit" if not gaps else ("partial" if effective else "miss")

        for index, gap in enumerate(gaps):
            # The corporate-action backstop rides on the LAST gap only -- the
            # one that reaches the tail of coverage -- by widening its start a
            # few bars into already-cached territory. That is the same single
            # upstream call, so the check costs no extra network traffic.
            #
            # Consequence, accepted deliberately: a purely historical request
            # that is fully cached issues no upstream call at all, so it can
            # serve pre-split adjusted values until some request touches the
            # tail. The cache is memory-only and lives for the process, which
            # bounds how long that can persist.
            compare = backstop and index == len(gaps) - 1 and bool(effective)
            fetch_start = gap[0]
            if compare:
                # Never reach back past where the cached data starts: a gap
                # that sits BEFORE all coverage (the zoom-out case) has no
                # cached bars to compare against, so it is fetched untouched.
                floor = max(min(s for s, _ in effective), gap[0] - step * _OVERLAP_BARS)
                if floor < gap[0]:
                    fetch_start = floor
                compare = fetch_start < gap[0]

            rows = await self._fetch(
                model, params, credentials, symbol, interval, fetch_start, gap[1], meta, log
            )
            if not rows:
                continue
            frame = _to_frame(rows)

            t0 = time.perf_counter()
            # Compare ONLY the overlap that is both already cached and already
            # complete. The still-forming bar changes value constantly by
            # design -- comparing it would read as a split on every request.
            cached = self.store.read_bars(
                symbol, interval, fetch_start, min(gap[0] - step, boundary)
            ) if compare else None
            self.store.write_bars(symbol, interval, frame)
            recorded = trim_tail((fetch_start, gap[1]), boundary)
            if recorded:
                self.store.record_coverage(symbol, interval, recorded)
            meta["kdb_ms"] += (time.perf_counter() - t0) * 1000

            if compare and _adjusted_history_changed(cached, frame):
                logger.info("adjusted history changed for %s; dropping cache", symbol)
                t0 = time.perf_counter()
                self.store.drop(symbol, interval)
                meta["kdb_ms"] += (time.perf_counter() - t0) * 1000
                return None

        t0 = time.perf_counter()
        self.store.touch(symbol, interval)
        budget = int(self.config.memory_mb * 1024 * 1024 * self.config.watermark)
        self.store.evict_until_below(budget)
        served = self.store.read_bars(symbol, interval, start, end)
        meta["kdb_ms"] += (time.perf_counter() - t0) * 1000
        return _from_frame(served)

    async def _fetch(self, model, params, credentials, symbol, interval, start, end,
                     meta, log):
        """One upstream call, recorded in both the metadata and the fetch log."""
        rows, elapsed = await self._timed_fetch(
            model, params, credentials, symbol, interval, start, end
        )
        meta["upstream_ms"] += elapsed
        meta["gaps_fetched"] += 1
        log.rows.extend(rows)
        log.ranges.append((start, end))
        return rows

    async def _timed_fetch(self, model, params, credentials, symbol, interval, start, end):
        call = dict(params)
        call["symbol"] = symbol
        call["start_date"], call["end_date"] = _fetch_bounds(interval, start, end)
        t0 = time.perf_counter()
        rows = await self._fetch_gap(self.config.upstream, model, call, credentials)
        return rows, (time.perf_counter() - t0) * 1000


def _fetch_bounds(interval: str, start, end):
    """Bounds as the upstream fetcher wants them.

    Daily-and-longer intervals take plain dates. Intraday keeps the time of
    day: sending `.date()` for a 14:00->15:07 five-minute gap would refetch the
    whole day, every time.
    """
    if interval_step(interval) >= timedelta(days=1):
        return (
            start.date() if isinstance(start, datetime) else start,
            end.date() if isinstance(end, datetime) else end,
        )
    return start, end


def _adjusted_history_changed(cached, fresh) -> bool:
    """True when bars we already had disagree with the ones just fetched.

    A split or dividend rewrites the whole adjusted series, so overlapping the
    tail fetch with cached bars catches it. The comparison column must exist on
    BOTH sides -- a provider that returns `adj_close` and no `close` must not
    turn this into a KeyError that permanently bypasses the cache -- and `t` is
    normalised on both sides first, or a timezone-awareness mismatch would make
    the merge empty and silently skip the check.
    """
    import pandas as pd

    if cached is None or getattr(cached, "empty", True):
        return False
    if fresh is None or getattr(fresh, "empty", True):
        return False
    column = _comparable_column(cached, fresh)
    if column is None:
        logger.debug("no shared price column; skipping the corporate-action check")
        return False
    left = pd.DataFrame({
        "t": _naive_t(cached["t"]).reset_index(drop=True),
        "v": cached[column].to_numpy(),
    })
    right = pd.DataFrame({
        "t": _naive_t(fresh["t"]).reset_index(drop=True),
        "v": fresh[column].to_numpy(),
    })
    merged = left.merge(right, on="t", suffixes=("_old", "_new"))
    if merged.empty:
        return False
    old = pd.to_numeric(merged["v_old"], errors="coerce")
    new = pd.to_numeric(merged["v_new"], errors="coerce")
    return bool(((old - new).abs() > 1e-6).fillna(False).any())


def _comparable_column(cached, fresh) -> str | None:
    """A numeric price column present on both frames, or None."""
    import pandas as pd

    fresh_columns = set(fresh.columns)
    shared = [
        c for c in cached.columns
        if c != "t" and c in fresh_columns and c not in _NON_PRICE
        and pd.api.types.is_numeric_dtype(cached[c])
        and pd.api.types.is_numeric_dtype(fresh[c])
    ]
    for name in _PRICE_PREFERENCE:
        if name in shared:
            return name
    return shared[0] if shared else None


def _naive_t(values):
    """Timestamps as timezone-naive datetime64, whatever came in."""
    import pandas as pd

    series = values if isinstance(values, pd.Series) else pd.Series(list(values))
    try:
        out = pd.to_datetime(series)
    except (TypeError, ValueError):
        out = pd.to_datetime(series, utc=True)
    if pd.api.types.is_object_dtype(out):
        # Mixed offsets: pandas leaves those as objects unless told to unify.
        out = pd.to_datetime(series, utc=True)
    if getattr(getattr(out, "dt", None), "tz", None) is not None:
        out = out.dt.tz_convert("UTC").dt.tz_localize(None)
    return out


def _attribute_rows(rows: list[dict], log: _FetchLog, meta: dict) -> None:
    """Split the served rows into cache-served and upstream-served.

    A served row counts as upstream only if THIS call fetched that timestamp.
    Providers routinely pad a window with rows outside it, and rows pulled in
    purely to widen the corporate-action overlap are never served -- neither
    can be counted, or the two numbers stop adding up to what was returned.
    """
    stamps = _stamp_set(log.rows)
    upstream = sum(1 for row in rows if _stamp(row.get("date")) in stamps) if stamps else 0
    meta["rows_from_upstream"] = upstream
    meta["rows_from_cache"] = len(rows) - upstream


def _stamp_set(rows: list[dict]) -> set:
    if not rows:
        return set()
    try:
        return set(_to_frame(rows)["t"].tolist())
    except Exception:  # noqa: BLE001 - attribution must never fail a request
        return set()


def _stamp(value):
    import pandas as pd

    try:
        return pd.Timestamp(value)
    except (TypeError, ValueError):
        return None


def _dedupe(rows: list[dict]) -> list[dict]:
    """Merge rows fetched across several calls, newest write per stamp winning."""
    if not rows:
        return []
    try:
        frame = _to_frame(rows)
    except Exception:  # noqa: BLE001 - a bypass must still return the raw rows
        return list(rows)
    return _from_frame(frame.drop_duplicates(subset="t", keep="last"))


def _to_frame(rows: list[dict]):
    import pandas as pd

    if not rows:
        return pd.DataFrame({"t": pd.Series(dtype="datetime64[ns]")})
    frame = pd.DataFrame(rows)
    stamp = next((k for k in _STAMP_KEYS if k in frame.columns), None)
    if stamp is None:
        raise ValueError(
            "Upstream rows carry no recognisable timestamp column (looked for "
            f"{', '.join(_STAMP_KEYS)}); got {list(frame.columns)}."
        )
    frame = frame.rename(columns={stamp: "t"})
    frame["t"] = _naive_t(frame["t"])
    return frame.sort_values("t").reset_index(drop=True)


def _from_frame(frame) -> list[dict]:
    if frame is None or getattr(frame, "empty", True):
        return []
    out = frame.rename(columns={"t": "date"})
    return out.to_dict(orient="records")
