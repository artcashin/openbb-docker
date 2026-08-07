"""Tests for app.feeds.FeedManager (SDK client mocked via factory)."""

import asyncio
import json
from unittest.mock import MagicMock

import app.feeds as feeds_mod
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


async def _wait_until(condition, timeout: float = 5.0, poll_interval: float = 0.005) -> None:
    """Poll `condition` until it's true; raise (never silently pass) if `timeout` expires.

    `timeout` is a backstop against a genuine hang, not the success signal --
    it exists so a broken condition fails loudly instead of hanging the suite.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        if loop.time() >= deadline:
            raise asyncio.TimeoutError(f"condition not met within {timeout}s: {condition!r}")
        await asyncio.sleep(poll_interval)


async def _run_until(manager: FeedManager, condition, timeout: float = 5.0) -> None:
    """Run manager.run() until `condition()` holds, then cancel it cleanly.

    Bounds progress by an observable condition instead of wall clock: real
    cycle duration is unbounded under thread-pool contention (each cycle can
    do up to two asyncio.to_thread round-trips for the recorder), so a fixed
    sleep can't reliably guarantee even one cycle completed.
    """
    task = asyncio.create_task(manager.run())
    try:
        await _wait_until(condition, timeout=timeout)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


class TestRecorderIntegration:
    def test_flush_runs_every_drain_cycle_when_a_recorder_is_present(self, fake_ws_client_factory):
        recorder = MagicMock()
        m = FeedManager(
            "k", QuoteTable(), client_factory=fake_ws_client_factory,
            drain_interval=0.01, rebuild_delay=0.0, recorder=recorder,
        )
        asyncio.run(_run_until(m, lambda: recorder.flush.called))
        assert recorder.flush.called

    def test_no_recorder_means_no_flush_calls(self, fake_ws_client_factory):
        m = make_manager(fake_ws_client_factory)  # recorder=None by default
        # No recorder means nothing to observe recorder-side, so watch the
        # drain cycle itself: wait for a couple of cycles to prove the manager
        # actually ran for a while with no recorder attached, without raising.
        calls = {"n": 0}
        real_drain_all = m._drain_all

        def counting_drain_all():
            calls["n"] += 1
            real_drain_all()

        m._drain_all = counting_drain_all
        asyncio.run(_run_until(m, lambda: calls["n"] >= 2))  # must not raise

    def test_prune_only_fires_once_per_interval(self, fake_ws_client_factory):
        # loop.time() is a monotonic clock with an arbitrary (non-zero) origin,
        # so with _last_prune initialised to 0.0 the very first drain cycle
        # always clears "now - 0.0 >= PRUNE_INTERVAL" -- the first prune is
        # effectively "on startup". What the interval must still guarantee is
        # that it does NOT re-fire on every one of the several cycles that fit
        # inside this test's short run (default PRUNE_INTERVAL is 60s).
        recorder = MagicMock()
        m = FeedManager(
            "k", QuoteTable(), client_factory=fake_ws_client_factory,
            drain_interval=0.01, rebuild_delay=0.0, recorder=recorder,
        )
        # Wait for positive evidence that SEVERAL drain cycles elapsed (flush
        # is called every cycle) before checking prune only fired once --
        # asserting after just one cycle would prove nothing about the interval.
        asyncio.run(_run_until(m, lambda: recorder.flush.call_count >= 2))
        assert recorder.flush.called
        assert recorder.prune.call_count == 1

    def test_prune_runs_once_the_interval_elapses(self, fake_ws_client_factory, monkeypatch):
        monkeypatch.setattr(feeds_mod, "PRUNE_INTERVAL", 0.0)
        recorder = MagicMock()
        m = FeedManager(
            "k", QuoteTable(), client_factory=fake_ws_client_factory,
            drain_interval=0.01, rebuild_delay=0.0, recorder=recorder,
        )
        asyncio.run(_run_until(m, lambda: recorder.prune.called))
        assert recorder.prune.called

    def test_a_failing_recorder_never_breaks_the_feed_loop(self, fake_ws_client_factory):
        """Episode 8's feature must survive anything the cache does."""
        recorder = MagicMock()
        recorder.flush.side_effect = RuntimeError("kdb exploded")
        m = FeedManager(
            "k", QuoteTable(), client_factory=fake_ws_client_factory,
            drain_interval=0.01, rebuild_delay=0.0, recorder=recorder,
        )
        m.register("c1", ["AAPL"])

        async def go():
            task = asyncio.create_task(m.run())
            try:
                await _wait_until(
                    lambda: any(c.feed == "us" for c in fake_ws_client_factory.built)
                )
                us = next(c for c in fake_ws_client_factory.built if c.feed == "us")
                us.data_list.append(json.dumps({"s": "AAPL", "p": 190.5, "q": 10}))
                await _wait_until(lambda: m.quotes.rows.get("AAPL", {}).get("price") == 190.5)
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        asyncio.run(go())
        assert m.quotes.rows["AAPL"]["price"] == 190.5


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
