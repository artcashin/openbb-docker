"""The subscription API: grouping, and a cap that counts the vendor's view."""

import asyncio

import pytest

from tests.test_main import make_client


def _client(tmp_path, monkeypatch, cap=50):
    monkeypatch.setenv("LIVE_GRID_WATCHLIST", str(tmp_path / "w.json"))
    monkeypatch.setenv("LIVE_GRID_MAX_SYMBOLS", str(cap))
    return make_client()


def test_an_empty_watchlist_reports_the_cap_and_nothing_used(tmp_path, monkeypatch):
    body = _client(tmp_path, monkeypatch).get("/api/subscriptions").json()
    assert body["service"] == "EODHD"
    assert body["cap"] == 50
    assert body["used"] == 0
    assert body["pinned"] == []


def test_adding_a_symbol_pins_it_and_counts_it(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client.post("/api/subscriptions", json={"symbol": "AAPL"}).status_code == 201
    body = client.get("/api/subscriptions").json()
    assert body["pinned"] == ["AAPL"]
    assert body["used"] == 1


def test_symbols_are_grouped_by_feed_under_display_names(tmp_path, monkeypatch):
    """classify() returns us/crypto/forex; the widget shows Equity/Crypto/Forex."""
    client = _client(tmp_path, monkeypatch)
    for s in ("MSFT", "AAPL", "BTC-USD", "EURUSD"):
        client.post("/api/subscriptions", json={"symbol": s})
    groups = client.get("/api/subscriptions").json()["groups"]
    assert groups["Equity"] == ["AAPL", "MSFT"], "alphabetical within a group"
    assert groups["Crypto"] == ["BTC-USD"]
    assert groups["Forex"] == ["EURUSD"]


def test_every_group_is_present_even_when_empty(tmp_path, monkeypatch):
    """The page renders three sections unconditionally; absent keys would break it."""
    groups = _client(tmp_path, monkeypatch).get("/api/subscriptions").json()["groups"]
    assert set(groups) == {"Equity", "Crypto", "Forex"}


def test_adding_a_symbol_twice_is_a_conflict(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/api/subscriptions", json={"symbol": "AAPL"})
    assert client.post("/api/subscriptions", json={"symbol": "AAPL"}).status_code == 409


def test_a_blank_symbol_is_rejected(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client.post("/api/subscriptions", json={"symbol": "   "}).status_code == 422


def test_the_cap_refuses_an_add_that_would_exceed_it(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, cap=2)
    client.post("/api/subscriptions", json={"symbol": "AAPL"})
    client.post("/api/subscriptions", json={"symbol": "MSFT"})
    r = client.post("/api/subscriptions", json={"symbol": "TSLA"})
    assert r.status_code == 507
    assert "cap" in r.json()["detail"].lower() or "50" in r.json()["detail"] or "2" in r.json()["detail"]
    assert client.get("/api/subscriptions").json()["pinned"] == ["AAPL", "MSFT"]


def test_a_leased_symbol_counts_against_the_cap(tmp_path, monkeypatch):
    """A lease occupies an EODHD slot exactly as a pin does. Ignoring leases would
    let the widget report free capacity the vendor does not have."""
    client = _client(tmp_path, monkeypatch, cap=2)
    client.post("/subscribe", json={"symbols": ["NVDA"], "ttl": 300})
    client.post("/api/subscriptions", json={"symbol": "AAPL"})
    body = client.get("/api/subscriptions").json()
    assert body["leased"] == ["NVDA"]
    assert body["used"] == 2, "one pinned + one leased"
    assert client.post("/api/subscriptions", json={"symbol": "MSFT"}).status_code == 507


def test_a_symbol_both_pinned_and_leased_counts_once(tmp_path, monkeypatch):
    """THE union rule. EODHD sees a SET of symbols per connection, so the same
    symbol pinned and leased is one subscription. Summing would refuse adds while
    slots were still free."""
    client = _client(tmp_path, monkeypatch, cap=2)
    client.post("/api/subscriptions", json={"symbol": "AAPL"})
    client.post("/subscribe", json={"symbols": ["AAPL"], "ttl": 300})
    body = client.get("/api/subscriptions").json()
    assert body["used"] == 1, "AAPL is pinned AND leased, but is one subscription"
    assert client.post("/api/subscriptions", json={"symbol": "MSFT"}).status_code == 201


def test_removing_a_symbol_unpins_it(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/api/subscriptions", json={"symbol": "AAPL"})
    assert client.delete("/api/subscriptions/AAPL").status_code == 200
    assert client.get("/api/subscriptions").json()["pinned"] == []


def test_removing_something_not_pinned_is_a_404(tmp_path, monkeypatch):
    assert _client(tmp_path, monkeypatch).delete("/api/subscriptions/AAPL").status_code == 404


def test_a_leased_symbol_cannot_be_removed_through_this_api(tmp_path, monkeypatch):
    """Leases lapse on their own TTL; this widget does not own them."""
    client = _client(tmp_path, monkeypatch)
    client.post("/subscribe", json={"symbols": ["NVDA"], "ttl": 300})
    assert client.delete("/api/subscriptions/NVDA").status_code == 404


def test_the_symbol_path_parameter_is_case_insensitive(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/api/subscriptions", json={"symbol": "AAPL"})
    assert client.delete("/api/subscriptions/aapl").status_code == 200


def test_a_pinned_symbol_reaches_the_feed(tmp_path, monkeypatch):
    """The point of pinning: the feed must actually want the symbol."""
    client = _client(tmp_path, monkeypatch)
    client.post("/api/subscriptions", json={"symbol": "AAPL"})
    assert "AAPL" in client.app.state.manager._union("us")


def test_unpinning_removes_it_from_the_feed(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/api/subscriptions", json={"symbol": "AAPL"})
    client.delete("/api/subscriptions/AAPL")
    assert "AAPL" not in client.app.state.manager._union("us")


def test_symbols_already_on_disk_are_fed_at_startup(tmp_path, monkeypatch):
    """A restart must restore the subscriptions, not just the list."""
    import json

    p = tmp_path / "w.json"
    p.write_text(json.dumps(["AAPL", "EURUSD"]))
    monkeypatch.setenv("LIVE_GRID_WATCHLIST", str(p))
    monkeypatch.setenv("LIVE_GRID_MAX_SYMBOLS", "50")
    client = make_client()
    with client:  # entering the context runs lifespan
        manager = client.app.state.manager
        assert "AAPL" in manager._union("us")
        assert "EURUSD" in manager._union("forex")


def test_pinning_does_not_disturb_a_lease_on_another_symbol(tmp_path, monkeypatch):
    """Watchlist and leases are separate _conns entries; _union merges them."""
    client = _client(tmp_path, monkeypatch)
    client.post("/subscribe", json={"symbols": ["NVDA"], "ttl": 300})
    client.post("/api/subscriptions", json={"symbol": "AAPL"})
    union = client.app.state.manager._union("us")
    assert {"AAPL", "NVDA"} <= union


def test_the_page_is_served_as_html(tmp_path, monkeypatch):
    r = _client(tmp_path, monkeypatch).get("/subscriptions")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "<html" in r.text.lower()


def test_the_page_is_self_contained_with_no_external_requests(tmp_path, monkeypatch):
    """It renders inside an iframe in the desktop app, which may be offline. A CDN
    script or webfont would leave the widget blank rather than degraded."""
    body = _client(tmp_path, monkeypatch).get("/subscriptions").text
    for bad in ("http://", "https://", "//cdn", "src=\"//"):
        assert bad not in body, f"page reaches outside for {bad!r}"


def test_the_widget_is_declared_as_an_iframe_pointing_at_the_page(tmp_path, monkeypatch):
    """A backend iframe widget's endpoint IS the URL the front end frames."""
    widgets = _client(tmp_path, monkeypatch).get("/widgets.json").json()
    w = widgets["subscriptions"]
    assert w["type"] == "iframe"
    assert w["endpoint"] == "subscriptions"
