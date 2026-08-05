"""Store: q statement construction and eviction policy, against a fake connection."""

from datetime import datetime

import pytest

from openbb_kdb.store import KdbStore

D = lambda s: datetime.fromisoformat(s)  # noqa: E731


class FakeConn:
    """Records queries; returns canned values by substring match."""

    def __init__(self, responses=None):
        self.queries = []
        self.responses = responses or {}
        self.data = {}

    def __call__(self, query, *args):
        self.queries.append(query)
        for needle, value in self.responses.items():
            if needle in query:
                return _Wrapped(value)
        return _Wrapped(None)

    def __setitem__(self, key, value):
        self.data[key] = value


class _Wrapped:
    def __init__(self, value):
        self._value = value

    def py(self):
        return self._value

    def pd(self):
        return self._value


class FakeSession:
    def __init__(self, conn):
        self._conn = conn

    def connection(self):
        return self._conn


def store_with(responses=None):
    conn = FakeConn(responses)
    return KdbStore(FakeSession(conn)), conn


def test_table_name_is_symbol_and_interval():
    s, _ = store_with()
    assert s.table_name("AAPL", "1d") == "bars_AAPL_1d"


def test_table_name_sanitizes_punctuation():
    """BTC-USD and EUR.FOREX must not produce invalid q identifiers."""
    s, _ = store_with()
    assert s.table_name("BTC-USD", "1d") == "bars_BTC_USD_1d"
    assert s.table_name("BRK.B", "5m") == "bars_BRK_B_5m"


def test_memory_reads_heap_not_used():
    """heap is what approaches wmax and kills q; used trails it."""
    s, _ = store_with({".Q.w[]": {"used": 100, "heap": 500, "wmax": 1000}})
    assert s.memory()["heap"] == 500


def test_evict_stops_once_below_budget():
    s, conn = store_with({
        ".Q.w[]": {"used": 100, "heap": 100, "wmax": 1000},
        ".cache.lru": [("AAPL", "1d", 1.0)],
    })
    assert s.evict_until_below(500) == []
    assert not any(".Q.gc" in q for q in conn.queries)


def test_evict_drops_oldest_first_and_collects_garbage():
    """delete alone frees `used` but not `heap`; .Q.gc[] is what reclaims it."""
    calls = {"n": 0}

    class ShrinkingConn(FakeConn):
        def __call__(self, query, *args):
            self.queries.append(query)
            if ".Q.w[]" in query:
                calls["n"] += 1
                heap = 1000 if calls["n"] == 1 else 100
                return _Wrapped({"used": heap, "heap": heap, "wmax": 4000})
            if ".cache.lru" in query and "select" in query:
                return _Wrapped([("OLD", "1d", 1.0), ("NEW", "1d", 99.0)])
            return _Wrapped(None)

    conn = ShrinkingConn()
    s = KdbStore(FakeSession(conn))
    evicted = s.evict_until_below(500)
    assert evicted == ["bars_OLD_1d"]
    assert any(".Q.gc" in q for q in conn.queries)
    assert any("delete" in q and "bars_OLD_1d" in q for q in conn.queries)


def test_read_coverage_returns_ranges():
    s, _ = store_with({
        ".cache.cov": [(D("2024-01-01"), D("2024-06-30"))],
    })
    assert s.read_coverage("AAPL", "1d") == [(D("2024-01-01"), D("2024-06-30"))]


def test_read_coverage_empty_for_unknown_symbol():
    s, _ = store_with({".cache.cov": []})
    assert s.read_coverage("NOPE", "1d") == []


def test_drop_removes_table_and_coverage():
    s, conn = store_with()
    s.drop("AAPL", "1d")
    joined = " ".join(conn.queries)
    assert "bars_AAPL_1d" in joined
    assert ".cache.cov" in joined
