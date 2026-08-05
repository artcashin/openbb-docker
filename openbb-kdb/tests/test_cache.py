"""The read-through algorithm: what gets fetched, what gets served, what it reports."""

from datetime import datetime, timedelta

import pandas as pd
import pytest

from openbb_kdb.cache import ReadThroughCache, last_complete_boundary
from openbb_kdb.config import KdbConfig
from openbb_kdb.session import KdbUnavailable

D = lambda s: datetime.fromisoformat(s)  # noqa: E731


def cfg(**kw) -> KdbConfig:
    base = dict(host="127.0.0.1", port=5000, embedded=True, memory_mb=1024,
                watermark=0.75, upstream="eodhd", qhome="/opt/kx")
    base.update(kw)
    return KdbConfig(**base)


class FakeStore:
    """In-memory stand-in for KdbStore."""

    def __init__(self, coverage=None, bars=None, heap=0):
        self.coverage = coverage or {}
        self.bars = bars or {}
        self.heap = heap
        self.written = []
        self.dropped = []
        self.evicted_to = None

    def read_coverage(self, symbol, interval):
        return self.coverage.get((symbol, interval), [])

    def record_coverage(self, symbol, interval, r):
        self.coverage.setdefault((symbol, interval), []).append(r)

    def read_bars(self, symbol, interval, start, end):
        df = self.bars.get((symbol, interval), pd.DataFrame())
        if df.empty:
            return df
        return df[(df["t"] >= start) & (df["t"] <= end)]

    def write_bars(self, symbol, interval, df):
        self.written.append((symbol, interval, df))
        prior = self.bars.get((symbol, interval), pd.DataFrame())
        self.bars[(symbol, interval)] = pd.concat([prior, df]).sort_values("t")

    def touch(self, symbol, interval):
        pass

    def drop(self, symbol, interval):
        self.dropped.append((symbol, interval))
        self.coverage.pop((symbol, interval), None)
        self.bars.pop((symbol, interval), None)

    def memory(self):
        return {"used": self.heap, "heap": self.heap, "wmax": 4_000_000_000}

    def evict_until_below(self, budget):
        self.evicted_to = budget
        return []

    def table_name(self, symbol, interval):
        return f"bars_{symbol}_{interval}"


def bars_frame(dates, close=1.0):
    return pd.DataFrame({"t": [D(d) for d in dates], "close": [close] * len(dates)})


def make_cache(store, fetches, **cfg_kw):
    """Wire a cache whose upstream records calls and returns canned rows.

    Responses are returned in order; the LAST one is sticky, so a test that
    retries (the bypass path) keeps getting data instead of an empty list.
    """
    calls = []

    async def fake_fetch(provider, model, params, credentials):
        calls.append(params)
        if not fetches:
            return []
        return fetches.pop(0) if len(fetches) > 1 else fetches[0]

    cache = ReadThroughCache(store, cfg(**cfg_kw))
    cache._fetch_gap = fake_fetch
    return cache, calls


async def test_cold_cache_is_a_miss_and_fetches_everything():
    store = FakeStore()
    rows_up = [{"date": "2024-01-02", "close": 1.0}]
    cache, calls = make_cache(store, [rows_up])
    rows, meta = await cache.get(
        "AAPL", "1d", D("2024-01-01"), D("2024-01-31"),
        "EquityHistorical", {"symbol": "AAPL"}, None,
    )
    assert meta["cache"] == "miss"
    assert meta["gaps_fetched"] == 1
    assert len(calls) == 1


async def test_full_hit_makes_no_upstream_call():
    """The second identical request must not touch the network."""
    store = FakeStore(
        coverage={("AAPL", "1d"): [(D("2024-01-01"), D("2024-12-31"))]},
        bars={("AAPL", "1d"): bars_frame(["2024-06-01", "2024-06-02"])},
    )
    cache, calls = make_cache(store, [])
    rows, meta = await cache.get(
        "AAPL", "1d", D("2024-06-01"), D("2024-06-30"),
        "EquityHistorical", {"symbol": "AAPL"}, None,
    )
    assert meta["cache"] == "hit"
    assert meta["rows_from_upstream"] == 0
    assert calls == []


async def test_the_zoom_fetches_only_the_missing_years():
    """1y cached, 3y requested -> exactly one gap, and it is the missing prefix."""
    store = FakeStore(
        coverage={("AAPL", "1d"): [(D("2024-01-01"), D("2024-12-31"))]},
        bars={("AAPL", "1d"): bars_frame(["2024-06-01"])},
    )
    cache, calls = make_cache(store, [[{"date": "2022-01-03", "close": 1.0}]])
    rows, meta = await cache.get(
        "AAPL", "1d", D("2022-01-01"), D("2024-12-31"),
        "EquityHistorical", {"symbol": "AAPL"}, None,
    )
    assert meta["cache"] == "partial"
    assert len(calls) == 1
    assert calls[0]["start_date"] == D("2022-01-01").date()
    assert calls[0]["end_date"] == D("2023-12-31").date()


async def test_coverage_never_includes_the_incomplete_tail():
    """Today's bar is still forming, so it must be refetched next time."""
    store = FakeStore()
    now = D("2025-06-10T15:00:00")
    cache, _ = make_cache(store, [[{"date": "2025-06-09", "close": 1.0}]])
    await cache.get(
        "AAPL", "1d", D("2025-01-01"), now,
        "EquityHistorical", {"symbol": "AAPL"}, None, now=now,
    )
    recorded = store.coverage[("AAPL", "1d")]
    assert all(end < now for _, end in recorded)


async def test_a_split_invalidates_the_symbol():
    """Overlapping closes disagree -> adjusted history was rewritten."""
    store = FakeStore(
        coverage={("AAPL", "1d"): [(D("2024-01-01"), D("2024-12-31"))]},
        bars={("AAPL", "1d"): bars_frame(["2024-12-30", "2024-12-31"], close=200.0)},
    )
    cache, calls = make_cache(
        store,
        [[{"date": "2024-12-30", "close": 100.0}, {"date": "2024-12-31", "close": 100.0}],
         [{"date": "2024-01-02", "close": 100.0}]],
    )
    now = D("2025-01-05")
    rows, meta = await cache.get(
        "AAPL", "1d", D("2024-01-01"), now,
        "EquityHistorical", {"symbol": "AAPL"}, None, now=now,
    )
    # Dropping the symbol clears its coverage, so the refill is a full miss --
    # which is exactly right: none of the old adjusted history is trustworthy.
    assert ("AAPL", "1d") in store.dropped
    assert meta["cache"] == "miss"


async def test_unreachable_kdb_passes_through():
    """The cache must never be the reason a request fails."""

    class DeadStore(FakeStore):
        def read_coverage(self, symbol, interval):
            raise KdbUnavailable("no q")

    cache, calls = make_cache(DeadStore(), [[{"date": "2024-01-02", "close": 1.0}]])
    rows, meta = await cache.get(
        "AAPL", "1d", D("2024-01-01"), D("2024-01-31"),
        "EquityHistorical", {"symbol": "AAPL"}, None,
    )
    assert meta["cache"] == "bypass"
    assert rows == [{"date": "2024-01-02", "close": 1.0}]


async def test_dead_q_midway_still_returns_data():
    """A write failing after a successful fetch must not lose the fetched rows."""

    class FlakyStore(FakeStore):
        def write_bars(self, symbol, interval, df):
            raise RuntimeError("Attempted to use a closed IPC connection")

    cache, _ = make_cache(FlakyStore(), [[{"date": "2024-01-02", "close": 1.0}]])
    rows, meta = await cache.get(
        "AAPL", "1d", D("2024-01-01"), D("2024-01-31"),
        "EquityHistorical", {"symbol": "AAPL"}, None,
    )
    assert rows == [{"date": "2024-01-02", "close": 1.0}]
    assert meta["cache"] == "bypass"


async def test_eviction_runs_against_the_budget_not_the_workspace():
    store = FakeStore(heap=10_000_000)
    cache, _ = make_cache(store, [[{"date": "2024-01-02", "close": 1.0}]], memory_mb=1024)
    await cache.get(
        "AAPL", "1d", D("2024-01-01"), D("2024-01-31"),
        "EquityHistorical", {"symbol": "AAPL"}, None,
    )
    assert store.evicted_to == int(1024 * 1024 * 1024 * 0.75)


async def test_concurrent_requests_for_one_symbol_fetch_once():
    """Two widgets opening the same chart must not both hit the network."""
    import asyncio

    store = FakeStore()
    cache, calls = make_cache(store, [[{"date": "2024-01-02", "close": 1.0}], []])
    args = ("AAPL", "1d", D("2024-01-01"), D("2024-01-31"),
            "EquityHistorical", {"symbol": "AAPL"}, None)
    await asyncio.gather(cache.get(*args), cache.get(*args))
    assert len(calls) == 1


@pytest.mark.parametrize(
    "interval,now,expected",
    [
        ("1d", D("2025-06-10T15:00:00"), D("2025-06-09T23:59:59.999999")),
        ("1h", D("2025-06-10T15:30:00"), D("2025-06-10T14:59:59.999999")),
        ("5m", D("2025-06-10T15:07:00"), D("2025-06-10T15:04:59.999999")),
    ],
)
def test_last_complete_boundary(interval, now, expected):
    assert last_complete_boundary(interval, now) == expected
