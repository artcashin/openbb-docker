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


def test_demo_page_binds_relayout_handler_only_after_first_plot():
    """Regression test for the page being completely non-functional: Plotly
    only attaches its `.on` event-emitter to the chart <div> once that div
    has been plotted at least once (Plotly.newPlot/react). A previous fix for
    listener accumulation (see test above) moved the single `chart.on(...)`
    call into boot(), which runs synchronously before any plot exists -- so
    `chart.on` was `undefined`, boot() threw a TypeError on its first
    statement, and reload() (and thus the whole page) never ran.

    A plain "does `chart.on(` come before `Plotly.react(` in the file text"
    check does NOT catch this: `draw()` (which contains `Plotly.react`) is
    *defined* earlier in the file than the `boot()` IIFE regardless of which
    one actually calls `chart.on` first at runtime -- text position reflects
    definition order, not execution order. So instead this test looks at
    which *function body* the registration lives in: `boot()` runs
    immediately, before `draw()` has ever run, so a `chart.on(` call directly
    inside `boot()`'s body reproduces the crash; a `chart.on(` call inside
    `draw()`'s body (necessarily after `Plotly.react`/`newPlot`, since that's
    the only plot call in that function) only runs once a plot already
    exists.

    This test does NOT execute the JavaScript or a real Plotly -- it cannot
    prove the page runs correctly end to end (see the live-page checks in the
    fix report instead). It only proves the specific ordering bug that broke
    the page cannot silently come back in this shape.
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
