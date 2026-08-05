"""Upstream resolution: any registered provider, resolved by name."""

import pytest

from openbb_kdb.upstream import UpstreamError, fetch_gap


class FakeFetcher:
    last_call = None

    @classmethod
    async def fetch_data(cls, params, credentials=None, **kwargs):
        FakeFetcher.last_call = (params, credentials)
        return [{"date": "2024-01-02", "close": 1.0}]


class FakeProvider:
    def __init__(self, fetchers):
        self.fetcher_dict = fetchers


def install_registry(monkeypatch, providers):
    import openbb_kdb.upstream as up

    monkeypatch.setattr(up, "_load_registry", lambda: providers)
    up._REGISTRY_CACHE = None


@pytest.mark.asyncio
async def test_fetches_through_the_named_provider(monkeypatch):
    install_registry(monkeypatch, {"eodhd": FakeProvider({"EquityHistorical": FakeFetcher})})
    rows = await fetch_gap("eodhd", "EquityHistorical", {"symbol": "AAPL"}, {"k": "v"})
    assert rows == [{"date": "2024-01-02", "close": 1.0}]
    assert FakeFetcher.last_call == ({"symbol": "AAPL"}, {"k": "v"})


@pytest.mark.asyncio
async def test_any_provider_works_not_just_eodhd(monkeypatch):
    """KDB_UPSTREAM must accept any registered provider."""
    install_registry(monkeypatch, {"yfinance": FakeProvider({"EquityHistorical": FakeFetcher})})
    rows = await fetch_gap("yfinance", "EquityHistorical", {"symbol": "AAPL"}, None)
    assert rows


@pytest.mark.asyncio
async def test_unknown_provider_raises_upstream_error(monkeypatch):
    install_registry(monkeypatch, {"eodhd": FakeProvider({})})
    with pytest.raises(UpstreamError, match="nosuch"):
        await fetch_gap("nosuch", "EquityHistorical", {}, None)


@pytest.mark.asyncio
async def test_provider_without_the_model_raises(monkeypatch):
    install_registry(monkeypatch, {"eodhd": FakeProvider({"EquityHistorical": FakeFetcher})})
    with pytest.raises(UpstreamError, match="CryptoHistorical"):
        await fetch_gap("eodhd", "CryptoHistorical", {}, None)


@pytest.mark.asyncio
async def test_kdb_may_not_be_its_own_upstream(monkeypatch):
    """Guards against infinite recursion through the provider registry."""
    install_registry(monkeypatch, {"kdb": FakeProvider({"EquityHistorical": FakeFetcher})})
    with pytest.raises(UpstreamError, match="itself"):
        await fetch_gap("kdb", "EquityHistorical", {}, None)
