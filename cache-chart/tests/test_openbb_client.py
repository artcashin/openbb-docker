"""The client: talks to the OpenBB API on loopback, surfaces cache metadata."""

import pytest

from app.openbb_client import fetch_series


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture
def payload():
    return {
        "results": [{"date": "2024-01-02", "open": 1.0, "high": 2.0,
                     "low": 0.5, "close": 1.5, "volume": 10}],
        "extra": {"results_metadata": {"cache": "partial", "rows_from_cache": 100,
                                       "rows_from_upstream": 5, "gaps_fetched": 1,
                                       "upstream_ms": 12.5, "kdb_ms": 0.9}},
    }


async def test_returns_bars_and_cache_metadata(monkeypatch, payload):
    async def fake_get(self, url, **kw):
        return FakeResponse(payload)

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)
    bars, meta = await fetch_series("AAPL", "1d", "2024-01-01", "2024-12-31", "kdb")
    assert len(bars) == 1
    assert meta["cache"] == "partial"


async def test_provider_is_forwarded(monkeypatch, payload):
    seen = {}

    async def fake_get(self, url, **kw):
        seen["params"] = kw.get("params")
        return FakeResponse(payload)

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)
    await fetch_series("AAPL", "1d", "2024-01-01", "2024-12-31", "eodhd")
    assert seen["params"]["provider"] == "eodhd"


async def test_missing_metadata_reports_unknown(monkeypatch):
    """A provider without the cache (eodhd direct) still renders."""

    async def fake_get(self, url, **kw):
        return FakeResponse({"results": [], "extra": {}})

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)
    bars, meta = await fetch_series("AAPL", "1d", "2024-01-01", "2024-12-31", "eodhd")
    assert meta["cache"] == "unknown"
