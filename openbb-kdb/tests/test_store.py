"""Store: q statement construction and eviction policy, against a fake connection."""

import re
from datetime import datetime

from openbb_kdb.store import _INIT_SCHEMA, KdbStore

D = lambda s: datetime.fromisoformat(s)  # noqa: E731


class FakeConn:
    """Records queries; returns canned values by substring match."""

    def __init__(self, responses=None):
        self.queries = []
        self.calls = []  # (query, args) -- args matter for the lambda-binding check
        self.responses = responses or {}
        self.data = {}

    def __call__(self, query, *args):
        self.queries.append(query)
        self.calls.append((query, args))
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
        self.runs = 0

    def connection(self):
        return self._conn

    def run(self, fn):
        """Stand-in for the real owner-thread marshalling: run it here, now."""
        self.runs += 1
        return fn()


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
            if "in key" in query:  # bars_OLD_1d exists, so its eviction counts
                return _Wrapped(True)
            return _Wrapped(None)

    conn = ShrinkingConn()
    s = KdbStore(FakeSession(conn))
    evicted = s.evict_until_below(500)
    assert evicted == ["bars_OLD_1d"]
    assert any(".Q.gc" in q for q in conn.queries)
    assert any("delete" in q and "bars_OLD_1d" in q for q in conn.queries)


def test_evict_warns_and_returns_empty_when_lru_is_empty_but_heap_over_budget(caplog):
    """An empty LRU can't tell us anything to evict -- but crossing q's -w kills
    the process, so failing to reach budget must be visible, not silent."""
    s, conn = store_with({
        ".Q.w[]": {"used": 900, "heap": 900, "wmax": 1000},
        ".cache.lru": [],
    })
    with caplog.at_level("WARNING"):
        evicted = s.evict_until_below(500)
    assert evicted == []
    assert any("budget" in r.message.lower() for r in caplog.records)
    assert not any(".Q.gc" in q for q in conn.queries)


def test_evict_does_not_count_a_stale_lru_row_whose_table_is_already_gone():
    """A row in .cache.lru naming a table that no longer exists must not be
    reported as an eviction -- callers use the return value to account for
    freed memory, and a phantom entry would overstate what was actually freed."""
    calls = {"n": 0}

    class StaleRowConn(FakeConn):
        def __call__(self, query, *args):
            self.queries.append(query)
            if ".Q.w[]" in query:
                calls["n"] += 1
                # heap never drops -- the phantom table was never really there
                # to free, so this also exercises the "exhausted the LRU
                # without reaching budget" warning path.
                return _Wrapped({"used": 900, "heap": 900, "wmax": 4000})
            if ".cache.lru" in query and "select" in query:
                return _Wrapped([("GHOST", "1d", 1.0)])
            if "in key" in query:  # bars_GHOST_1d does not exist
                return _Wrapped(False)
            return _Wrapped(None)

    conn = StaleRowConn()
    s = KdbStore(FakeSession(conn))
    evicted = s.evict_until_below(500)
    assert evicted == []


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


def test_schema_init_runs_before_first_coverage_read():
    """.cache.cov/.cache.lru must exist before anything queries or writes them."""
    s, conn = store_with({".cache.cov": []})
    s.read_coverage("AAPL", "1d")
    assert conn.queries[0] == _INIT_SCHEMA
    assert "select s, e from .cache.cov" in conn.queries[1]


def test_lru_schema_is_keyed_so_touch_upsert_replaces_not_appends():
    """.cache.lru must be keyed on (sym, iv): on an unkeyed table `upsert`
    appends, so repeated touches of the same symbol pile up stale rows that
    can outrank genuinely hot data during eviction."""
    s, conn = store_with()
    s.touch("AAPL", "1d")
    assert "([sym:`symbol$(); iv:`symbol$()] atime:`timestamp$())" in conn.queries[0]


def test_schema_init_runs_before_first_write():
    s, conn = store_with()
    s.touch("AAPL", "1d")
    assert conn.queries[0] == _INIT_SCHEMA
    assert ".cache.lru" in conn.queries[1]


def test_schema_init_reruns_on_new_connection_but_not_redundantly():
    """A respawned q is empty -- init must re-run for a new connection object,
    but not on every call against the connection already initialised."""
    conn1 = FakeConn()
    session = FakeSession(conn1)
    s = KdbStore(session)

    s.touch("AAPL", "1d")
    s.touch("AAPL", "1d")
    assert conn1.queries.count(_INIT_SCHEMA) == 1  # no redundant re-init

    conn2 = FakeConn()
    session._conn = conn2  # simulate session.connection() handing back a respawned q
    s.touch("AAPL", "1d")
    assert conn2.queries.count(_INIT_SCHEMA) == 1  # re-ran for the new connection
    assert conn1.queries.count(_INIT_SCHEMA) == 1  # old connection's log untouched


def test_no_statement_uses_a_leading_underscore_identifier():
    """`_incoming` was a q parse error (`_` is the drop/cut operator); guard
    against any recorded statement reintroducing an identifier like it."""
    s, conn = store_with({".cache.cov": [], ".cache.lru": []})
    s.write_bars("AAPL", "1d", object())
    s.read_bars("AAPL", "1d", D("2024-01-01"), D("2024-01-02"))
    s.read_coverage("AAPL", "1d")
    s.record_coverage("AAPL", "1d", (D("2024-01-01"), D("2024-01-02")))
    s.touch("AAPL", "1d")
    s.drop("AAPL", "1d")

    leading_underscore_ident = re.compile(r"(?<![\w.])_[A-Za-z]")
    for q in conn.queries:
        assert not leading_underscore_ident.search(q), q


def exercise_every_statement(store):
    """Drive every store method that issues q, on one fake connection."""
    store.write_bars("AAPL", "1d", object())
    store.read_bars("AAPL", "1d", D("2024-01-01"), D("2024-01-02"))
    store.read_coverage("AAPL", "1d")
    store.record_coverage("AAPL", "1d", (D("2024-01-01"), D("2024-01-02")))
    store.touch("AAPL", "1d")
    store.drop("AAPL", "1d")
    store.memory()
    store.evict_until_below(10**9)


def test_every_parameterised_statement_is_a_lambda():
    """q binds x/y/z/w as implicit parameters only INSIDE {...}. A bare
    expression referencing them raises 'x, which the cache swallows as a
    bypass -- so the whole cache silently did nothing against a real q."""
    s, conn = store_with({
        ".cache.cov": [], ".cache.lru": [],
        ".Q.w[]": {"used": 1, "heap": 1, "wmax": 10},
        "in key": True,
    })
    exercise_every_statement(s)

    parameterised = [(q, args) for q, args in conn.calls if args]
    assert parameterised, "no statement passed arguments -- the check is vacuous"
    for query, args in parameterised:
        assert query.startswith("{["), f"unbound implicit args in: {query}"
        # The lambda must actually declare as many parameters as are sent.
        declared = query[2:query.index("]")].split(";")
        assert len(declared) == len(args), f"{len(args)} args, {declared} declared: {query}"


def test_lambda_parameters_never_shadow_a_column_name():
    """Inside select/delete, a table's own columns shadow the lambda's
    parameters: `{[s;e] ... where sym = s}` against .cache.cov (columns s, e)
    would compare a column with itself and match every row."""
    s, conn = store_with({
        ".cache.cov": [], ".cache.lru": [],
        ".Q.w[]": {"used": 1, "heap": 1, "wmax": 10},
        "in key": True,
    })
    exercise_every_statement(s)

    columns = {"sym", "iv", "s", "e", "atime", "t", "open", "high", "low", "close",
               "volume"}
    for query, args in conn.calls:
        if not args:
            continue
        declared = {p.strip() for p in query[2:query.index("]")].split(";")}
        assert not declared & columns, f"parameter shadows a column: {query}"


def test_every_q_call_is_marshalled_onto_the_session_owner_thread():
    """PyKX is unusable from more than one thread -- not merely unserialised --
    so no query, K-object construction or conversion may happen outside
    session.run()."""
    conn = FakeConn({
        ".cache.cov": [], ".cache.lru": [],
        ".Q.w[]": {"used": 1, "heap": 1, "wmax": 10},
    })

    class StrictSession(FakeSession):
        """Refuses to hand out a connection except from inside run()."""

        def __init__(self, conn):
            super().__init__(conn)
            self.inside = False

        def connection(self):
            assert self.inside, "connection() reached outside run()"
            return self._conn

        def run(self, fn):
            self.runs += 1
            self.inside = True
            try:
                return fn()
            finally:
                self.inside = False

    session = StrictSession(conn)
    exercise_every_statement(KdbStore(session))
    assert session.runs >= 8
