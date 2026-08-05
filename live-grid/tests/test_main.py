"""Server-layer tests: widgets.json contract, REST seeding, health, key gate.
The SDK is never imported — the seed client is injected."""
from fastapi.testclient import TestClient

from app.main import create_app


class FakeRest:
    def __init__(self, snaps=None):
        self.snaps = snaps or {}
        self.calls = []

    def get_live_stock_prices(self, ticker):
        self.calls.append(ticker)
        return self.snaps.get(ticker, {"close": "100", "previousClose": "90", "volume": "5"})


def make_client(**kw):
    app = create_app(api_key=kw.pop("api_key", "test-key"),
                     seed_client=kw.pop("seed_client", FakeRest()),
                     client_factory=kw.pop("client_factory", lambda *a, **k: _NullWs()))
    return TestClient(app)


class _NullWs:
    running = False
    data_list: list = []

    def start(self):
        self.running = True

    def stop(self):
        self.running = False


def test_widgets_json_declares_the_live_grid_contract():
    body = make_client().get("/widgets.json").json()
    w = body["live_grid"]
    assert w["type"] == "live_grid"
    assert w["wsEndpoint"] == "live_grid_ws"
    assert w["data"]["wsRowIdColumn"] == "symbol"
    fields = [c["field"] for c in w["data"]["table"]["columnsDefs"]]
    assert fields[0] == "symbol"
    # snapshot-only column must never update over the socket
    vol = next(c for c in w["data"]["table"]["columnsDefs"] if c["field"] == "volume")
    assert vol["enableCellChangeWs"] is False


def test_live_grid_seeds_rows_in_request_order():
    rest = FakeRest({"AAPL.US": {"close": "150", "previousClose": "100", "volume": "7"}})
    rows = make_client(seed_client=rest).get("/live_grid?symbol=AAPL,BTC-USD").json()
    assert [r["symbol"] for r in rows] == ["AAPL", "BTC-USD"]
    assert rows[0]["price"] == 150.0
    assert rows[0]["change"] == 50.0
    assert rest.calls == ["AAPL.US", "BTC-USD.CC"]


def test_live_grid_with_no_symbols_is_an_empty_list():
    assert make_client().get("/live_grid").json() == []


def test_missing_api_key_is_a_clear_500_not_a_stacktrace():
    app = create_app(api_key=None, seed_client=None,
                     client_factory=lambda *a, **k: _NullWs())
    resp = TestClient(app).get("/live_grid?symbol=AAPL")
    assert resp.status_code == 500
    assert "EODHD_API_KEY" in resp.json()["detail"]


def test_health_reports_per_feed_state():
    body = make_client().get("/health").json()
    assert body["status"] == "ok"
    assert set(body["feeds"]) == {"us", "crypto", "forex"}


def test_health_omits_ticks_key_when_chart_is_disabled(monkeypatch):
    monkeypatch.setenv("LIVE_GRID_CHART", "false")
    body = make_client().get("/health").json()
    assert "ticks" not in body


def test_health_includes_ticks_key_when_chart_is_enabled_by_default(monkeypatch):
    monkeypatch.delenv("LIVE_GRID_CHART", raising=False)
    body = make_client().get("/health").json()
    assert set(body["ticks"]) == {"buffered", "written", "dropped"}


def test_websocket_registers_params_and_streams_dirty_rows():
    client = make_client()
    with client.websocket_connect("/live_grid_ws") as ws:
        ws.send_json({"params": {"symbol": "AAPL"}})
        # Reach into the app: mark a tick so the producer has something to flush.
        app = client.app
        manager = app.state.manager
        quotes = app.state.quotes
        import time
        for _ in range(50):
            if manager._conns:
                break
            time.sleep(0.02)
        assert any("AAPL" in by_feed["us"] for by_feed in manager._conns.values())
        quotes.rows["AAPL"] = {"symbol": "AAPL", "price": 151.0}
        for dirty in manager._dirty.values():
            dirty.add("AAPL")
        row = ws.receive_json()
        assert row["symbol"] == "AAPL"
        assert row["price"] == 151.0
