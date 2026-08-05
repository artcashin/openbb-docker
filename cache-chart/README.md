# cache-chart

The **kdb+ read-through cache**, made visible. Companion code for
*Adventures in OpenBB, Ep. 10*.

- `GET /widgets.json` — the Workspace widget contract
- `GET /chart` — Plotly figure JSON (the widget)
- `GET /series` — `{bars, cache}` for incremental loads
- `GET /demo` — the standalone page
- `GET /health` — cache reachability

## The demo

Open `https://openbb.<your-tailnet>.ts.net:6906/demo`. The chart opens on one
year of daily bars. **Scroll out** and the page requests only the range it does
not already have; the HUD shows rows served from cache versus fetched
upstream.

Scroll back **in** and nothing is requested at all — the window is inside what
is already loaded, and only a gap *outside* it is ever fetched. Scroll out
again over the same range and nothing is fetched from the vendor. The provider
toggle runs the same gesture with the cache off, for comparison.

One honest caveat: any window that reaches today always refetches that day's
still-forming bar, so it reports `partial` rather than `hit` even on a repeat.
A clean `hit` needs a window that ends in the past.

A Workspace plotly widget receives figure JSON once and zooms client-side, so
it benefits from the cache on parameter changes (a new date range, a new
symbol) rather than on scroll — which is why the scroll demo is a page of its
own.

## Test

    pip install -e .[dev] && pytest    # mocked; no key or license needed
