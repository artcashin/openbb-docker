"""Tests for app.feeds.FeedManager (SDK client mocked via factory)."""

import asyncio
import json

from app.feeds import FeedManager
from app.quotes import QuoteTable


def make_manager(factory) -> FeedManager:
    return FeedManager("test_key", QuoteTable(), client_factory=factory, rebuild_delay=0.0)


class TestSyncFeeds:
    def test_builds_one_client_per_feed(self, fake_ws_client_factory):
        m = make_manager(fake_ws_client_factory)
        m.register("c1", ["AAPL", "BTC-USD", "EURUSD"])
        asyncio.run(m._sync_feeds())
        by_feed = {c.feed: c.symbols for c in fake_ws_client_factory.built}
        assert by_feed == {"us": ["AAPL"], "crypto": ["BTC-USD"], "forex": ["EURUSD"]}
        for c in fake_ws_client_factory.built:
            c.start.assert_called_once()

    def test_no_rebuild_when_union_unchanged(self, fake_ws_client_factory):
        m = make_manager(fake_ws_client_factory)
        m.register("c1", ["AAPL"])
        asyncio.run(m._sync_feeds())
        m.register("c2", ["AAPL"])  # same union
        asyncio.run(m._sync_feeds())
        assert len(fake_ws_client_factory.built) == 1

    def test_union_shrinks_on_unregister(self, fake_ws_client_factory):
        m = make_manager(fake_ws_client_factory)
        m.register("c1", ["AAPL", "MSFT"])
        m.register("c2", ["AAPL"])
        asyncio.run(m._sync_feeds())
        assert fake_ws_client_factory.built[0].symbols == ["AAPL", "MSFT"]
        m.unregister("c1")
        asyncio.run(m._sync_feeds())
        fake_ws_client_factory.built[0].stop.assert_called_once()
        assert fake_ws_client_factory.built[1].symbols == ["AAPL"]

    def test_empty_union_stops_client(self, fake_ws_client_factory):
        m = make_manager(fake_ws_client_factory)
        m.register("c1", ["AAPL"])
        asyncio.run(m._sync_feeds())
        m.unregister("c1")
        asyncio.run(m._sync_feeds())
        fake_ws_client_factory.built[0].stop.assert_called_once()
        assert m.status()["us"]["symbols"] == []

    def test_factory_failure_keeps_manager_alive(self, fake_ws_client_factory):
        def broken(api_key, feed, symbols):
            raise RuntimeError("bad key")

        m = FeedManager("k", QuoteTable(), client_factory=broken, rebuild_delay=0.0)
        m.register("c1", ["AAPL"])
        asyncio.run(m._sync_feeds())  # must not raise
        assert m.status()["us"]["running"] is False


class TestDrain:
    def test_ticks_update_quotes_and_route_dirty(self, fake_ws_client_factory):
        m = make_manager(fake_ws_client_factory)
        m.register("c1", ["AAPL"])
        m.register("c2", ["MSFT"])
        asyncio.run(m._sync_feeds())
        us = next(c for c in fake_ws_client_factory.built if c.feed == "us")
        us.data_list.append(json.dumps({"s": "AAPL", "p": 190.5, "q": 10, "t": 1700000000000}))
        m._drain_all()
        assert m.quotes.rows["AAPL"]["price"] == 190.5
        assert m.pop_dirty("c1") == {"AAPL"}
        assert m.pop_dirty("c2") == set()
        assert us.data_list == []  # consumed prefix trimmed

    def test_pop_dirty_clears(self, fake_ws_client_factory):
        m = make_manager(fake_ws_client_factory)
        m.register("c1", ["AAPL"])
        asyncio.run(m._sync_feeds())
        us = fake_ws_client_factory.built[0]
        us.data_list.append(json.dumps({"s": "AAPL", "p": 1.0}))
        m._drain_all()
        assert m.pop_dirty("c1") == {"AAPL"}
        assert m.pop_dirty("c1") == set()

    def test_garbage_messages_skipped(self, fake_ws_client_factory):
        m = make_manager(fake_ws_client_factory)
        m.register("c1", ["AAPL"])
        asyncio.run(m._sync_feeds())
        us = fake_ws_client_factory.built[0]
        us.data_list.extend(["not json", json.dumps({"status": "ok"})])
        m._drain_all()  # must not raise
        assert m.pop_dirty("c1") == set()


class TestLifecycle:
    def test_stop_all_stops_every_client(self, fake_ws_client_factory):
        m = make_manager(fake_ws_client_factory)
        m.register("c1", ["AAPL", "BTC-USD"])
        asyncio.run(m._sync_feeds())
        asyncio.run(m.stop_all())
        for c in fake_ws_client_factory.built:
            c.stop.assert_called_once()

    def test_status_shape(self, fake_ws_client_factory):
        m = make_manager(fake_ws_client_factory)
        status = m.status()
        assert set(status.keys()) == {"us", "crypto", "forex"}
        assert status["us"] == {"symbols": [], "running": False}
