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


def test_health_reports_provider():
    assert client.get("/health").status_code == 200
