"""Routes."""

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def stub_series(monkeypatch):
    async def fake(symbol, interval, start, end, provider):
        return (
            [{"date": "2024-01-02", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}],
            {"cache": "hit", "rows_from_cache": 1, "rows_from_upstream": 0,
             "gaps_fetched": 0, "upstream_ms": 0.0, "kdb_ms": 0.4},
        )

    monkeypatch.setattr("app.main.fetch_series", fake)


def test_widgets_json_is_served():
    r = client.get("/widgets.json")
    assert r.status_code == 200
    assert isinstance(r.json(), dict)


def test_series_returns_bars_and_cache_block():
    r = client.get("/series", params={"symbol": "AAPL", "start": "2024-01-01",
                                      "end": "2024-12-31"})
    assert r.status_code == 200
    body = r.json()
    assert body["cache"]["cache"] == "hit"
    assert len(body["bars"]) == 1


def test_chart_returns_plotly_figure_json():
    r = client.get("/chart", params={"symbol": "AAPL"})
    assert r.status_code == 200
    assert "data" in r.json() and "layout" in r.json()


def test_demo_page_is_html_with_scroll_enabled():
    r = client.get("/demo")
    assert r.status_code == 200
    assert "scrollZoom" in r.text
    assert "plotly_relayout" in r.text


def test_demo_page_headline_metric_is_honest():
    """Every response travels browser<->service; only the backend->vendor hop
    is ever skipped on a cache hit. The page must not claim otherwise."""
    text = client.get("/demo").text
    assert "without touching the network" not in text
    assert "served from cache, without asking the data vendor" in text


def test_demo_page_registers_relayout_handler_once():
    """chart.on(...) must appear exactly once -- re-registering after every
    gap fetch leaks a listener per zoom (Finding 5)."""
    text = client.get("/demo").text
    assert text.count('chart.on("plotly_relayout"') == 1


def test_demo_page_client_handles_request_failures():
    """The client must not assume a response body has a `cache` key -- an
    error payload (or an unparsable body) needs a safe fallback so hud()
    can't throw and blank the page (Finding 3)."""
    text = client.get("/demo").text
    assert "cache: \"error\"" in text or "cache: 'error'" in text
    assert "catch" in text


def test_health_reports_provider():
    assert client.get("/health").status_code == 200


def test_series_returns_clean_error_when_upstream_fails(monkeypatch):
    async def boom(symbol, interval, start, end, provider):
        raise RuntimeError("upstream exploded")

    monkeypatch.setattr("app.main.fetch_series", boom)
    r = client.get("/series", params={"symbol": "AAPL"})
    assert r.status_code == 502
    body = r.json()
    assert body["bars"] == []
    assert body["cache"]["cache"] == "error"
    assert "upstream exploded" in body["cache"]["error"]


def test_chart_returns_clean_error_when_upstream_fails(monkeypatch):
    async def boom(symbol, interval, start, end, provider):
        raise RuntimeError("upstream exploded")

    monkeypatch.setattr("app.main.fetch_series", boom)
    r = client.get("/chart", params={"symbol": "AAPL"})
    assert r.status_code == 502
    body = r.json()
    assert body["data"] == []
    assert "upstream exploded" in body["layout"]["title"]["text"]
