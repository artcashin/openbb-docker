"""The /ta_chart route and macro discovery in /widgets.json."""

from fastapi.testclient import TestClient

from app.main import create_app


def client():
    return TestClient(create_app(api_key="test-key"))


def test_widgets_json_advertises_the_ta_chart_widget():
    spec = client().get("/widgets.json").json()
    assert spec["ta_chart"]["type"] == "chart"
    assert spec["ta_chart"]["endpoint"] == "ta_chart"


def test_widgets_json_fills_the_macro_dropdown_from_the_macro_directory():
    spec = client().get("/widgets.json").json()
    macro_param = next(p for p in spec["ta_chart"]["params"]
                       if p["paramName"] == "macro")
    values = [o["value"] for o in macro_param["options"]]
    assert "none" in values and "classic-momentum" in values


def test_ta_chart_returns_a_figure_even_with_no_upstream_data():
    response = client().get("/ta_chart", params={"symbol": "AAPL", "macro": "none"})
    assert response.status_code in (200, 502)
    assert "layout" in response.json()


def test_ta_chart_reports_a_bad_macro_in_the_title_not_a_stack_trace():
    response = client().get("/ta_chart", params={"macro": "nope"})
    assert response.status_code == 502
    assert "nope" in response.json()["layout"]["title"]["text"]


def test_the_existing_chart_route_is_untouched():
    assert client().get("/chart", params={"symbol": "AAPL"}).status_code in (200, 502)


def test_health_reports_the_eodhd_call_budget():
    body = client().get("/health").json()
    assert body["ta"]["eodhd_calls"] == 0
    assert body["ta"]["calls_per_indicator"] == 5


def test_health_still_reports_feed_status():
    assert "feeds" in client().get("/health").json()


def test_ta_chart_says_why_it_is_empty_when_bars_are_unavailable():
    response = client().get("/ta_chart", params={"symbol": "AAPL", "macro": "none"})
    assert response.status_code == 200
    assert "bars unavailable" in response.json()["layout"]["title"]["text"]


def test_a_bars_outage_after_rev_0_sends_a_figure_not_a_silently_emptied_delta(monkeypatch):
    """bars_error must force a full figure push -- a delta carries no title,
    so an emptied delta with no explanation is the same silently-blank
    failure /ta_chart was fixed for, one layer down."""
    monkeypatch.setenv("TA_PUSH_INTERVAL_MS", "10")
    calls = {"n": 0}
    bar = {"date": "2024-01-01", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}

    async def fake_build_series(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return [bar], {}
        raise RuntimeError("kdb down")

    monkeypatch.setattr("app.main.build_series", fake_build_series)
    with client().websocket_connect("/ta_chart_ws?symbol=AAPL&macro=none") as ws:
        first = ws.receive_json()
        assert first["type"] == "figure"
        second = ws.receive_json()
        assert second["type"] == "figure"
        assert "bars unavailable" in second["figure"]["layout"]["title"]["text"]
