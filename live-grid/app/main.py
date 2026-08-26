"""FastAPI app tying the pieces together: widgets.json, REST seeding, the
websocket stream, and health. One service, loopback-only; Tailscale Serve is
the ingress (see the repo compose file)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.feeds import FeedManager
from app.figure import build_figure
from app.openbb_client import fetch_series
from app.quotes import QuoteTable
from app.ta.figure import delta as ta_delta
from app.ta.macros import load_all as load_macros_all
from app.ta.payload import (
    ChartParams,
    any_repaints,
    bars_to_frame,
    build_payload,
    revised_from,
)
from app.ta.sources import EodhdSource

log = logging.getLogger("live-grid")

WIDGETS_PATH = Path(__file__).resolve().parent.parent / "widgets.json"
FLUSH_INTERVAL = 0.25  # seconds between coalesced row flushes per connection


def _api_key() -> str | None:
    return os.environ.get("EODHD_API_KEY") or None


def _rest_client(api_key: str):
    """Official SDK REST client, one per app. Import deferred so unit tests
    that inject their own seed client never import the SDK at all."""
    from eodhd import APIClient  # pylint: disable=import-outside-toplevel

    return APIClient(api_key)


def _parse_symbols(raw: str) -> list[str]:
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


async def build_series(symbol, interval, start, end, recorder, window, provider="kdb"):
    """History joined to tick-derived bars at the first fully-covered bar."""
    from app.series import seam_boundary, stitch, tick_capable, window_end

    history, meta = await fetch_series(symbol, interval, start, end, provider)
    meta = dict(meta)
    meta["rows_from_ticks"] = 0
    meta["seam"] = None

    if recorder is None or not tick_capable(interval, window):
        return history, meta

    try:
        from kdb_store.aggregate import aggregate_ticks

        # tick_span is a blocking IPC round-trip; off the loop like the
        # aggregation below it, or a chart request stalls the grid's
        # websocket flush.
        span = await asyncio.to_thread(recorder.store.tick_span, symbol)
        if span is None:
            return history, meta
        boundary = seam_boundary(span[0], interval)
        # Ticks are only ever as recent as *now*, so a window that ended in
        # the past must not have them tacked on. Clip to whichever of the two
        # ends first; if that leaves nothing at or after the seam, the request
        # is purely historical.
        hi = min(span[1], window_end(end))
        if hi < boundary:
            return history, meta
        ticks = await asyncio.to_thread(
            aggregate_ticks, recorder.store, symbol, interval, boundary, hi
        )
    except Exception as exc:  # noqa: BLE001 - the chart still works without ticks
        log.warning("tick aggregation unavailable for %s: %s", symbol, exc)
        return history, meta

    if not ticks:
        return history, meta
    meta["rows_from_ticks"] = len(ticks)
    meta["seam"] = boundary.isoformat()
    return stitch(history, ticks, boundary), meta


def create_app(*, api_key: str | None = None, seed_client=None, client_factory=None) -> FastAPI:
    """Build the app. Test seams: `seed_client` replaces the SDK REST client,
    `client_factory` is passed through to FeedManager (mock websockets)."""
    key = api_key if api_key is not None else _api_key()

    recorder = None
    if os.getenv("LIVE_GRID_CHART", "true").strip().lower() in ("1", "true", "yes", "on"):
        try:
            from kdb_store.config import resolve_config
            from kdb_store.session import KdbSession
            from kdb_store.store import KdbStore

            from app.recorder import TickRecorder

            config = resolve_config()
            session = KdbSession(config)
            window = timedelta(seconds=int(os.getenv("LIVE_TICK_WINDOW_SECONDS", "86400")))
            recorder = TickRecorder(KdbStore(session), window=window)
        except Exception as exc:  # noqa: BLE001 - the grid works without kdb
            log.warning("tick recording disabled: %s", exc)
            recorder = None

    quotes = QuoteTable(
        on_tick=recorder.record if recorder else None,
        snapshots=recorder.store if recorder else None,
    )
    manager_kwargs = {} if client_factory is None else {"client_factory": client_factory}
    manager = FeedManager(key or "", quotes, recorder=recorder, **manager_kwargs)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = asyncio.create_task(manager.run())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(lifespan=lifespan)
    app.state.quotes = quotes
    app.state.manager = manager

    # The Workspace origin (pro.openbb.co) fetches cross-origin from the
    # browser; BDOBB's non-streaming calls come via plugin-http (no CORS).
    cors_kwargs = dict(allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    try:
        app.add_middleware(CORSMiddleware, allow_private_network=True, **cors_kwargs)
    except TypeError:  # older starlette without PNA support
        app.add_middleware(CORSMiddleware, **cors_kwargs)

    def _seed_client():
        nonlocal seed_client
        if seed_client is None:
            if not key:
                raise HTTPException(
                    status_code=500,
                    detail="EODHD_API_KEY is not set. Put it in credentials.env "
                    "(the public demo websocket key covers the default watchlist).",
                )
            seed_client = _rest_client(key)
        return seed_client

    _STATIC = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=_STATIC), name="static")

    def _window(start: str | None, end: str | None) -> tuple[str, str]:
        today = date.today()
        return (start or str(today - timedelta(days=365)), end or str(today))

    def _tick_window() -> timedelta:
        return recorder.window if recorder is not None else timedelta(0)

    @app.get("/widgets.json")
    def widgets() -> JSONResponse:
        spec = json.loads(WIDGETS_PATH.read_text())
        try:
            macros = load_macros_all()
        except Exception as exc:  # noqa: BLE001 - a bad macro must not blank the grid
            log.warning("macro discovery failed: %s", exc)
            macros = {}
        for param in spec.get("ta_chart", {}).get("params", []):
            if param.get("paramName") == "macro":
                param["options"] = [{"label": "None", "value": "none"}] + [
                    {"label": m.label, "value": name} for name, m in sorted(macros.items())
                ]
        return JSONResponse(spec)

    @app.get("/live_grid")
    def live_grid(symbol: str = Query(default="")):
        symbols = _parse_symbols(symbol)
        if not symbols:
            return []
        client = _seed_client()
        return quotes.seed(symbols, client)

    @app.get("/series")
    async def series(symbol: str = "AAPL", interval: str = "1d",
                     start: str | None = None, end: str | None = None,
                     provider: str = "kdb"):
        s, e = _window(start, end)
        try:
            bars, meta = await build_series(
                symbol, interval, s, e, recorder, _tick_window(), provider
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("series failed for %s: %s", symbol, exc)
            return JSONResponse(
                {"symbol": symbol, "interval": interval, "start": s, "end": e, "bars": [],
                 "cache": {"cache": "error", "error": str(exc), "rows_from_cache": 0,
                           "rows_from_upstream": 0, "rows_from_ticks": 0,
                           "gaps_fetched": 0, "upstream_ms": 0.0, "kdb_ms": 0.0,
                           "seam": None}},
                status_code=502,
            )
        return {"symbol": symbol, "interval": interval, "start": s, "end": e,
                "bars": bars, "cache": meta}

    @app.get("/chart")
    async def chart(symbol: str = "AAPL", interval: str = "1d",
                    start: str | None = None, end: str | None = None,
                    provider: str = "kdb"):
        s, e = _window(start, end)
        try:
            bars, _ = await build_series(
                symbol, interval, s, e, recorder, _tick_window(), provider
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("chart failed for %s: %s", symbol, exc)
            return JSONResponse({"data": [], "layout": {"title": {"text": f"{symbol}: {exc}"}}},
                                status_code=502)
        return JSONResponse(build_figure(symbol, bars))

    _eodhd = EodhdSource(
        key or "",
        min_refetch_s=float(os.getenv("TA_EODHD_MIN_REFETCH_S", "60")),
    )

    @app.get("/ta_chart")
    async def ta_chart(symbol: str = "AAPL", interval: str = "1d",
                       source: str = "local", macro: str = "none",
                       indicators: str = "", start: str | None = None,
                       end: str | None = None, provider: str = "kdb"):
        s, e = _window(start, end)
        params = ChartParams(symbol, interval, source, macro, indicators, s, e, provider)
        bars_error = None
        try:
            bars, _ = await build_series(
                symbol, interval, s, e, recorder, _tick_window(), provider
            )
        except Exception as exc:  # noqa: BLE001 - a bad macro must still be reported below
            log.warning("ta_chart bars unavailable for %s: %s", symbol, exc)
            bars, bars_error = [], exc
        try:
            figure, _, _ = await build_payload(
                params, bars_to_frame(bars), eodhd_source=_eodhd
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("ta_chart failed for %s: %s", symbol, exc)
            return JSONResponse(
                {"data": [], "layout": {"title": {"text": f"{symbol}: {exc}"}}},
                status_code=502,
            )
        if bars_error is not None:
            # An empty chart that does not say why it is empty is the failure
            # mode this design treats as a defect everywhere else -- an EODHD
            # fallback annotates the legend, and an indicator that nulled itself
            # was a Critical bug. Degrading is right; degrading silently is not.
            figure["layout"]["title"]["text"] += f"  ·  bars unavailable: {bars_error}"
        return JSONResponse(figure)

    @app.websocket("/ta_chart_ws")
    async def ta_chart_ws(ws: WebSocket) -> None:
        await ws.accept()
        query = ws.query_params
        s, e = _window(query.get("start"), query.get("end"))
        params = ChartParams(
            symbol=query.get("symbol", "AAPL"),
            interval=query.get("interval", "1d"),
            source=query.get("source", "local"),
            macro=query.get("macro", "none"),
            indicators=query.get("indicators", ""),
            start=s, end=e, provider=query.get("provider", "kdb"),
        )
        interval_s = float(os.getenv("TA_PUSH_INTERVAL_MS", "1000")) / 1000.0
        previous: list[str] = []
        rev = 0
        try:
            while True:
                started = asyncio.get_running_loop().time()
                # NOTE: this re-fetches history over HTTP every push, not just
                # the ticks. Spec D9 costed the indicator recompute (0.66 ms)
                # but not this round-trip, so the real per-push cost is
                # dominated by I/O, not arithmetic. Acceptable for v1 because
                # the kdb read-through cache makes it a local hit and
                # TA_PUSH_INTERVAL_MS is tunable -- but the cheap win, if this
                # ever hurts, is to refetch history only on bar close and
                # re-aggregate ticks in between.
                bars_error = None
                try:
                    bars, _ = await build_series(
                        params.symbol, params.interval, s, e, recorder,
                        _tick_window(), params.provider
                    )
                # Degrade like /ta_chart: keep the connection open with a
                # blank-and-say-why chart instead of going silently dark
                # (Task 11 review).
                except Exception as exc:  # noqa: BLE001
                    log.warning("ta_chart_ws bars unavailable for %s: %s", params.symbol, exc)
                    bars, bars_error = [], exc
                try:
                    figure, panes, frame = await build_payload(
                        params, bars_to_frame(bars), eodhd_source=_eodhd
                    )
                # Mirrors /ta_chart's 502: not a missing-bars degradation, this
                # is unrecoverable (bad macro, bad indicator params), so end
                # the stream rather than looping on a permanent failure.
                except Exception as exc:  # noqa: BLE001
                    log.warning("ta_chart_ws failed for %s: %s", params.symbol, exc)
                    await ws.close(code=1011, reason=str(exc)[:120])
                    return
                if bars_error is not None:
                    figure["layout"]["title"]["text"] += f"  ·  bars unavailable: {bars_error}"
                dates = [str(d) for d in frame["date"].to_list()] if frame.height else []
                # `bars_error` forces a FIGURE push, not a delta. The
                # "bars unavailable" note lives in the figure's title, and a
                # delta carries no title -- so on a delta push the client would
                # receive an emptied chart with no explanation at all. That is
                # the same silently-blank failure the REST route was fixed for,
                # one layer down.
                if rev == 0 or any_repaints(panes) or bars_error is not None:
                    await ws.send_json({"type": "figure", "rev": rev, "figure": figure})
                else:
                    payload = ta_delta(frame, panes, revised_from(previous, dates))
                    await ws.send_json({"type": "delta", "rev": rev, **payload})
                previous, rev = dates, rev + 1
                # Drop rather than queue: a recompute that overran its slot must
                # not build a backlog that never drains.
                elapsed = asyncio.get_running_loop().time() - started
                await asyncio.sleep(max(0.0, interval_s - elapsed))
        except WebSocketDisconnect:
            return
        except Exception as exc:  # noqa: BLE001
            log.warning("ta_chart_ws ended for %s: %s", params.symbol, exc)
            return

    @app.get("/demo", response_class=HTMLResponse)
    async def demo():
        return HTMLResponse((_STATIC / "demo.html").read_text())

    @app.get("/health")
    def health():
        body = {"status": "ok", "feeds": manager.status()}
        if recorder is not None:
            body["ticks"] = recorder.stats()
            body["ticks"]["endpoint"] = getattr(recorder.store.session, "endpoint", None)
        body["ta"] = {
            "eodhd_calls": _eodhd.total_calls,
            "calls_per_indicator": 5,
            "min_refetch_s": float(os.getenv("TA_EODHD_MIN_REFETCH_S", "60")),
        }
        return body

    @app.websocket("/live_grid_ws")
    async def live_grid_ws(ws: WebSocket) -> None:
        await ws.accept()
        conn_id = uuid.uuid4().hex
        stop = asyncio.Event()

        async def consume() -> None:
            """Workspace sends {"params": {"symbol": "A,B"}} on connect and on
            every param change."""
            try:
                while True:
                    msg = await ws.receive_json()
                    params = msg.get("params") if isinstance(msg, dict) else None
                    raw = params.get("symbol", "") if isinstance(params, dict) else ""
                    symbols = _parse_symbols(str(raw))
                    manager.register(conn_id, symbols)
                    # Seed baselines for symbols the GET never saw (a param
                    # change adds symbols without a fresh GET) — off-loop, the
                    # SDK REST client is blocking.
                    missing = [s for s in symbols if s not in quotes._prev_close]
                    if missing and key:
                        try:
                            client = _seed_client()
                            await asyncio.to_thread(quotes.seed, missing, client)
                        except Exception as exc:  # noqa: BLE001
                            log.warning("ws baseline seed failed: %s", exc)
            except (WebSocketDisconnect, RuntimeError):
                stop.set()

        async def produce() -> None:
            """Coalesced flush: EODHD trades can tick hundreds of times a
            second; the latest row per dirty symbol every ~250ms is plenty."""
            try:
                while not stop.is_set():
                    await asyncio.sleep(FLUSH_INTERVAL)
                    for sym in sorted(manager.pop_dirty(conn_id)):
                        row = quotes.rows.get(sym)
                        if row is not None:
                            await ws.send_json(row)
            except (WebSocketDisconnect, RuntimeError):
                stop.set()

        if key is None and seed_client is None:
            await ws.close(code=1011, reason="EODHD_API_KEY is not set")
            return
        consumer = asyncio.create_task(consume())
        producer = asyncio.create_task(produce())
        await stop.wait()
        for t in (consumer, producer):
            t.cancel()
        manager.unregister(conn_id)

    return app


app = create_app()
