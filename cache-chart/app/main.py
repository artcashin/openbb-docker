"""Routes for the cache demo: a Workspace widget and a standalone page."""

import json
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.figure import build_figure
from app.openbb_client import fetch_series

app = FastAPI(title="cache-chart")

_HERE = Path(__file__).parent
_WIDGETS = _HERE.parent / "widgets.json"
_STATIC = _HERE / "static"

app.mount("/static", StaticFiles(directory=_STATIC), name="static")


def _window(start: str | None, end: str | None) -> tuple[str, str]:
    """Default to one year -- the window the demo opens on."""
    today = date.today()
    return (start or str(today - timedelta(days=365)), end or str(today))


@app.get("/widgets.json")
async def widgets():
    return JSONResponse(json.loads(_WIDGETS.read_text()))


@app.get("/series")
async def series(
    symbol: str = "AAPL",
    interval: str = "1d",
    start: str | None = None,
    end: str | None = None,
    provider: str = "kdb",
):
    """Bars plus the cache telemetry that drives the HUD."""
    s, e = _window(start, end)
    bars, meta = await fetch_series(symbol, interval, s, e, provider)
    return {"symbol": symbol, "interval": interval, "start": s, "end": e,
            "bars": bars, "cache": meta}


@app.get("/chart")
async def chart(
    symbol: str = "AAPL",
    interval: str = "1d",
    start: str | None = None,
    end: str | None = None,
    provider: str = "kdb",
):
    """Plotly figure JSON for the Workspace widget."""
    s, e = _window(start, end)
    bars, _ = await fetch_series(symbol, interval, s, e, provider)
    return JSONResponse(build_figure(symbol, bars))


@app.get("/demo", response_class=HTMLResponse)
async def demo():
    return HTMLResponse((_STATIC / "demo.html").read_text())


@app.get("/health")
async def health():
    try:
        _, meta = await fetch_series(
            "AAPL", "1d", str(date.today() - timedelta(days=5)), str(date.today()), "kdb"
        )
        return {"ok": True, "cache": meta.get("cache")}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)
