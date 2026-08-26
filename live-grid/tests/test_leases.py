"""Symbol-keyed TTL leases: a subscription that outlives the request."""

from datetime import datetime

from app.leases import LeaseRegistry


class FakeManager:
    def __init__(self):
        self.registered = {}
        self.register_calls = []
        self.unregistered = []

    def register(self, conn_id, symbols):
        self.register_calls.append(conn_id)
        self.registered[conn_id] = list(symbols)

    def unregister(self, conn_id):
        self.unregistered.append(conn_id)
        self.registered.pop(conn_id, None)


def test_a_lease_registers_the_symbol_under_its_own_id():
    """Per-symbol ids, not one shared id: symbols must expire independently."""
    m = FakeManager()
    LeaseRegistry(m, ttl=300.0).renew(["AAPL"], now=1000.0)
    assert m.registered == {"lease:AAPL": ["AAPL"]}


def test_renewing_extends_the_expiry_without_a_second_registration():
    """The fetcher leases on EVERY quote; that must renew, not accumulate."""
    m = FakeManager()
    reg = LeaseRegistry(m, ttl=300.0)
    first = reg.renew(["AAPL"], now=1000.0)
    second = reg.renew(["AAPL"], now=1200.0)
    assert first["AAPL"] == 1300.0
    assert second["AAPL"] == 1500.0
    assert list(m.registered) == ["lease:AAPL"]
    assert m.register_calls == ["lease:AAPL"]


def test_sweep_unregisters_only_the_lapsed_symbols():
    m = FakeManager()
    reg = LeaseRegistry(m, ttl=300.0)
    reg.renew(["AAPL"], now=1000.0)
    reg.renew(["MSFT"], now=1200.0)
    expired = reg.sweep(now=1400.0)
    assert expired == ["AAPL"]
    assert m.unregistered == ["lease:AAPL"]
    assert "lease:MSFT" in m.registered


def test_sweep_is_idempotent():
    """The sweeper runs on a timer; a second pass must not re-unregister."""
    m = FakeManager()
    reg = LeaseRegistry(m, ttl=300.0)
    reg.renew(["AAPL"], now=1000.0)
    reg.sweep(now=1400.0)
    assert reg.sweep(now=1500.0) == []
    assert m.unregistered == ["lease:AAPL"]


def test_a_symbol_relased_after_expiry_registers_again():
    m = FakeManager()
    reg = LeaseRegistry(m, ttl=300.0)
    reg.renew(["AAPL"], now=1000.0)
    reg.sweep(now=1400.0)
    reg.renew(["AAPL"], now=1500.0)
    assert m.registered == {"lease:AAPL": ["AAPL"]}


def test_symbols_are_upper_cased_so_case_cannot_split_a_lease():
    m = FakeManager()
    reg = LeaseRegistry(m, ttl=300.0)
    reg.renew(["aapl"], now=1000.0)
    reg.renew(["AAPL"], now=1100.0)
    assert list(m.registered) == ["lease:AAPL"]


def test_subscribe_route_returns_iso_expiries():
    from tests.test_main import make_client

    client = make_client()
    body = client.post("/subscribe", json={"symbols": ["AAPL"]}).json()
    assert set(body) == {"leases"}
    datetime.fromisoformat(body["leases"]["AAPL"])  # parses, or raises


def test_subscribe_route_rejects_an_empty_symbol_list():
    from tests.test_main import make_client

    client = make_client()
    assert client.post("/subscribe", json={"symbols": []}).status_code == 422


def test_subscribe_route_puts_the_symbol_into_the_feed_union():
    """The point of the lease: the feed must actually want the symbol."""
    from tests.test_main import make_client

    client = make_client()
    client.post("/subscribe", json={"symbols": ["AAPL"]})
    manager = client.app.state.manager
    assert "AAPL" in manager._union("us")
