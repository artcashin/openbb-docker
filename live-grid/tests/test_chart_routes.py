"""Chart routes on live-grid, including the tick/history join."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

D = lambda s: datetime.fromisoformat(s)  # noqa: E731


def bar(stamp, close=1.0):
    return {"date": stamp, "open": close, "high": close, "low": close,
            "close": close, "volume": 1.0}


@pytest.fixture
def client(monkeypatch):
    async def fake_history(symbol, interval, start, end, provider="kdb"):
        return ([bar("2025-06-10T13:58:00"), bar("2025-06-10T13:59:00")],
                {"cache": "hit", "rows_from_cache": 2, "rows_from_upstream": 0,
                 "gaps_fetched": 0, "upstream_ms": 0.0, "kdb_ms": 1.0})

    monkeypatch.setattr("app.main.fetch_series", fake_history)
    return TestClient(create_app(api_key="test-key"))


def test_series_returns_bars_and_a_cache_block(client):
    body = client.get("/series", params={"symbol": "AAPL"}).json()
    assert "bars" in body and "cache" in body


def test_series_reports_rows_from_ticks(client):
    body = client.get("/series", params={"symbol": "AAPL"}).json()
    assert "rows_from_ticks" in body["cache"]


def test_chart_returns_plotly_figure_json(client):
    body = client.get("/chart", params={"symbol": "AAPL"}).json()
    assert "data" in body and "layout" in body


def test_demo_page_is_served(client):
    res = client.get("/demo")
    assert res.status_code == 200
    assert "plotly_relayout" in res.text


def test_live_grid_widget_is_still_registered(client):
    """Episode 8's widget must survive this change."""
    assert "live_grid" in client.get("/widgets.json").json()


def test_chart_widget_is_registered(client):
    assert any("chart" in key for key in client.get("/widgets.json").json())


def test_series_works_with_no_recorder(client):
    """No kdb: history only, and the route must not error."""
    body = client.get("/series", params={"symbol": "AAPL"}).json()
    assert body["cache"]["rows_from_ticks"] == 0


def _extract_braced_block(text, marker):
    """Return `marker`'s following `{ ... }` block, matched by brace-depth
    counting (the file has no unbalanced braces inside strings/comments, so
    plain counting is safe here -- this is a test-only helper, not a JS
    parser)."""
    start = text.index(marker)
    brace_start = text.index("{", start)
    depth = 0
    for i in range(brace_start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[brace_start:i + 1]
    raise ValueError(f"unbalanced braces after {marker!r}")


def test_demo_page_binds_relayout_handler_only_after_first_plot(client):
    """Regression test carried over from cache-chart: Plotly only attaches its
    `.on` event-emitter to the chart <div> once that div has been plotted at
    least once (Plotly.newPlot/react). Binding `chart.on(...)` in boot() --
    which runs before any plot exists -- throws a TypeError and leaves the
    page completely blank while every server-side test still passes. This
    page has already been through that bug once; deleting cache-chart must
    not silently drop the regression coverage for it.
    """
    text = client.get("/demo").text

    boot_body = _extract_braced_block(text, "async function boot()")
    assert "chart.on(" not in boot_body, (
        "chart.on(...) is called directly in boot(), which runs before any "
        "plot exists -- on a bare <div>, Plotly has not attached `.on` yet, "
        "so this throws a TypeError and the page never boots"
    )

    draw_body = _extract_braced_block(text, "function draw()")
    plot_positions = [
        i for i in (draw_body.find("Plotly.react(chart"), draw_body.find("Plotly.newPlot(chart"))
        if i != -1
    ]
    on_position = draw_body.find("chart.on(")
    if on_position != -1:
        assert plot_positions and on_position > min(plot_positions), (
            "chart.on(...) appears in draw() before the Plotly plot call -- "
            "it must be bound only after the chart has actually been plotted"
        )


def test_demo_page_registers_relayout_handler_once(client):
    """chart.on(...) must appear exactly once -- re-registering after every
    gap fetch leaks a listener per zoom."""
    text = client.get("/demo").text
    assert text.count('chart.on("plotly_relayout"') == 1
