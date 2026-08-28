# Chart structure detection — design

**Status:** approved in brainstorming, 2026-08-28.

**What this is.** A detection subsystem in `live-grid` that finds the geometry a
technician draws: swing pivots, trendlines, and support/resistance levels. It
emits coordinates and quality measures. It does **not** name formations.

**Scope note — this is project A of two.** Brainstorming surfaced a second,
independent subsystem: exposing bdobb-v2's dashboard state so an agent can
resolve "the chart I'm looking at" to a symbol, interval and visible range.
That is a desktop-app-to-MCP integration with its own threat model, it is
useful without this project, and this project is useful without it (a caller
names the symbol explicitly). It gets its own spec and is out of scope here.

## Why it is not an indicator

Every entry in `app/ta/registry.py` emits **one value per bar, aligned to the
frame** — that is what an `Indicator`'s `build` returns and what `compute`
assembles. Structure is not that shape. A trendline is one object spanning 90
bars with two anchors and a slope; a level is one price with a touch count; a
pivot exists on 6 bars out of 300. The output is **sparse, overlapping, and
variable-length**, and forcing it into columns would either explode the frame
into one boolean column per detection or lose the geometry entirely.

So this is a sibling of the TA registry, not an addition to it: same discipline
(pure polars, no I/O, pinned conventions, unit-tested against synthetic input),
different output contract.

## Two consumers, two shapes

**Rita, over MCP.** Gets the **full multi-scale set**, ranked. An agent can
filter and weigh; it benefits from seeing that a level holds at three different
scales. This is the richer surface.

**The chart, over HTTP.** Gets **one scale**, selected from the bar count of
the requested range, so a card renders a readable set of lines rather than a
hairball:

| bars in range | scale |
|---|---|
| under 120 | `swing` |
| 120 to 600 | `intermediate` |
| over 600 | `primary` |

A caller may override with an explicit `scale`. The thresholds are round
numbers chosen so a quarter of daily bars lands on swing structure and several
years lands on primary; they are not derived, and tuning them is expected.

One engine, two projections. The projections are specified together so they
cannot drift apart.

## The division of labour with Rita

Settled in brainstorming and load-bearing throughout: **we emit honest
arithmetic; Rita supplies judgement.** We never return "head and shoulders".
We return the pivots, and Rita — reading a sequence of swing highs and lows
with magnitudes and dates — names what it sees.

This is deliberate. Formation identification is irreducibly subjective; the
published definitions are qualitative by design because they were written to
train an eye. Encoding one interpretation in Python would bake a judgement into
infrastructure and make it untestable, while the geometry underneath is
arithmetic we can pin exactly. Putting the subjective half in the model and the
objective half in polars puts each where it belongs.

## Detection: ATR-normalised ZigZag

A pivot is confirmed when price retraces from the running extreme by at least
`k * ATR`.

**Why ATR-normalised.** One threshold has to work across instruments and
intervals — a 2% retracement is noise in one symbol and a reversal in another.
Expressing it in ATR units makes `k` portable, which matters because this
serves any symbol the stack can fetch.

**Why not kernel regression.** Lo, Mamaysky & Wang (2000) smooth with
Nadaraya-Watson and take local extrema of the smoothed curve, and it is the
right citation for why multi-scale detection is the honest way to read a chart.
It is rejected as the *engine* for one concrete reason: the smoothed extremum
sits at a different bar from the real high, so the method needs a snap-back
step into a neighbourhood — and that step reintroduces exactly the arbitrariness
the smoothing removed. Both consumers here need **exact coordinates**: the chart
draws them and Rita quotes them. A method that lands on a real bar with a real
price is worth more than a smoother curve. (LMW's actual contribution is the
statistical testing of whether formations predict returns, which is project
territory we are not in.)

**Why not n-bar fractals.** No notion of magnitude: a 0.1% wiggle and a 20%
swing both qualify at the same `n`, so significance has to be bolted on
afterwards.

### The algorithm

Walk bars once, carrying a direction and a running extreme:

1. Seed direction from the first move that exceeds the threshold.
2. While rising, track the highest high. When the close retraces from it by
   `k * ATR`, **confirm that high as a pivot** and flip to falling.
3. Mirror for falling.
4. Threshold uses **ATR at the bar where the running extreme formed**, not at
   the current bar, so the move is judged against the volatility that produced
   it.

`ATR` is Wilder's, on raw OHLC, period 14 — the same definition the registry
already pins for `atr`, imported rather than re-derived.

### The provisional pivot — the part most likely to cause a bug

A pivot is confirmed only once price has retraced far enough, so **the most
recent extreme is always provisional**: it may still extend, and it is not yet
a pivot. Every pivot carries `confirmed: bool`, and the newest one is routinely
`false`.

This is not a detail. An agent told about a completed formation resting on a
provisional pivot will state something that has not happened yet, and a future
alerting path built on this contract would fire early. Consumers must be able
to distinguish, so the flag is part of the contract, not an implementation
note. The chart projection draws provisional pivots in a distinct style; the
MCP projection returns the flag and its description says what it means.

### Scales

Three by default, each detected independently:

| name | `k` | reads |
|---|---:|---|
| `swing` | 1.0 | short-term structure |
| `intermediate` | 3.0 | the moves a swing trader names |
| `primary` | 8.0 | the trend a chartist would draw first |

Scales are **not assumed to nest.** A pivot at `primary` is usually also a
pivot at `swing`, but the algorithm does not rely on it and nothing in the
output implies containment. Callers may pass their own `k` values.

## Derivations

### Trendlines

Candidates are pairs of **same-direction** pivots — two highs give a resistance
line, two lows a support line. For each candidate:

- **Fit** the line through the two anchors. No regression: the anchors define
  it, which is what a technician draws and what keeps the line reproducible.
- **Count touches** — later pivots within `touch_tol * ATR` of the line.
- **Count violations** — bars whose *close* crosses the line by more than
  `break_tol * ATR`. Closes, not wicks: a support line pierced intraday and
  reclaimed is still a support line, and using highs/lows rejects almost every
  real trendline.
- **Keep** lines with at least 3 touches (the two anchors plus one confirmation
  — two points define any line, so two touches prove nothing) and **no
  violations at or before the last touch**. Violations *after* the last touch
  are permitted and reported: that is a broken trendline, which is information,
  not a reason to withhold the line.
- **Score** on touches, span in bars, and recency of the last touch.

Tolerances, all in ATR units at the bar being tested so they scale with
volatility:

| parameter | default | meaning |
|---|---:|---|
| `touch_tol` | 0.5 | a pivot within this of the line counts as touching it |
| `break_tol` | 0.5 | a close this far through the line is a violation |

Both are caller-overridable. The defaults are a starting point to be tuned
against real charts during implementation, and the spec says so rather than
presenting them as derived — a half-ATR tolerance is a reasonable first guess,
not a measured constant.

Pair enumeration is `O(p^2)` in pivots per scale, and `p` is small by
construction — a 300-bar window at `k = 3` yields on the order of ten pivots,
not hundreds. Returned lines are capped at **8 per scale**, highest score
first, so a pathological series cannot produce an unbounded payload; levels are
capped the same way.

### Levels

Pivot prices cluster in one dimension:

- **Cluster** pivots whose prices are within `cluster_tol * ATR` (default
  `0.75`, caller-overridable, same tuning caveat as above), agglomerating
  nearest-first.
- **Price** is the touch-count-weighted mean of members.
- **Sides** records whether the cluster contains highs, lows, or both — a level
  tested from both directions is the classic flip and is worth surfacing.
- **Score** on touch count, span between first and last touch, and recency.

## Output contract

```json
{
  "symbol": "SPY.US",
  "interval": "1d",
  "range": {"start": "2024-01-02", "end": "2026-08-28", "bars": 668},
  "atr_period": 14,
  "scales": [{
    "name": "intermediate",
    "k": 3.0,
    "pivots": [{
      "id": "p:intermediate:high:2026-05-14:512.4000",
      "date": "2026-05-14", "bar": 412,
      "price": 512.40, "kind": "high",
      "swing_atr": 4.7, "swing_pct": 6.1,
      "confirmed": true
    }],
    "trendlines": [{
      "id": "t:intermediate:support:2025-11-03:2026-02-18",
      "kind": "support",
      "from": {"date": "2025-11-03", "price": 441.10},
      "to":   {"date": "2026-02-18", "price": 478.60},
      "slope_per_bar": 0.512,
      "touches": 4, "violations": 0, "span_bars": 74,
      "last_touch": "2026-02-18", "score": 0.81
    }],
    "levels": [{
      "id": "l:intermediate:512.4000",
      "price": 512.40, "touches": 5,
      "first": "2025-09-12", "last": "2026-05-14",
      "sides": ["resistance", "support"], "score": 0.74
    }]
  }]
}
```

### Identity

`id` is derived from content, never from a counter: scale, kind, and the
**origin anchors** — for a trendline, the two pivots that define it, not its
current extent. Two consequences, both intended:

- Detection is stateless. The same bars produce the same ids on any run, in any
  process, which is what makes the future research use reproducible.
- A trendline keeps its id as it extends. Its `to`, `touches` and `score` move
  as new bars arrive; its identity does not. A chart can therefore update a line
  in place rather than re-drawing it, and a future alerting path can say "this
  line, again" rather than treating each refresh as new.

Prices in ids are formatted to 4 decimal places so float representation cannot
produce two ids for one detection.

## Where it lives

```
live-grid/app/structure/
  types.py        Pivot, Trendline, Level, ScaleResult, StructureResult
  pivots.py       ATR-ZigZag
  trendlines.py   pair enumeration, touch/violation counting, scoring
  levels.py       clustering and scoring
  detect.py       orchestration: bars + params -> StructureResult
```

Pure functions over a polars frame. No I/O, no network, no route knowledge —
the same discipline as `app/ta/`, and the reason the whole thing is testable
without a server.

**HTTP.** `GET /structure?symbol=&interval=&start=&end=&scale=` in live-grid,
reusing the existing `/series` data path — the kdb read-through cache and its
seam logic already solve "get me correct bars for this window", and this route
must not grow a second answer to that question. Returns one scale, by the table above.

**MCP.** A tool in `mcp_stores` that calls `http://live-grid:6903/structure`
and returns all scales. It does **not** re-implement detection and does not
read bars itself: `/series` owns the data path. The cross-service call is
possible because both containers now share the `openbb-internal` bridge — under
the previous shared-namespace arrangement this would have been a loopback call
whose meaning depended on which containers happened to be in the namespace.

The tool's description states what the numbers are and what `confirmed: false`
means, because the description is the only documentation the model reads.

## Error handling

- **Too few bars for a scale** — a window that cannot contain a `k = 8` swing
  returns that scale with empty lists and a `note`, not an error. Asking for
  primary structure in a 30-bar window is a reasonable question with the answer
  "there isn't any".
- **No adjustment data** — detection runs on the adjusted series via the
  `adj_close / close` factor (`app/ta/exprs.adj`), which is exactly 1.0 for
  instruments with no adjustment. A raw series would read every split as a real
  swing and invent pivots.
- **Upstream `/series` failure** — surfaced with its status, both on the route
  and through the MCP tool. The tool never fabricates an empty result from a
  failed fetch: an agent cannot distinguish "no structure" from "no data" unless
  we tell it.
- **Unknown scale name** — rejected, listing the valid names.

## Testing

Synthetic series first, because a hand-built series has a knowable right
answer:

- **Pivots.** A triangle wave with known turning points yields exactly those
  pivots, at the exact bars. A sub-threshold wiggle superimposed on it yields
  the same pivots — that is the whole claim of the method.
- **ATR normalisation.** The same shape scaled to a different price level and a
  different volatility yields the same pivot *bars*. If this fails, `k` is not
  portable and the defaults are meaningless.
- **Provisional.** A series ending mid-swing marks the last extreme
  `confirmed: false`; extending it past the retracement flips it to `true` and
  adds no other pivot.
- **Trendlines.** Pivots placed exactly on a line yield one line with the right
  touch count; the same set with one bar closing decisively through it yields
  none. Two pivots alone yield none (two points define any line).
- **Levels.** Pivots at nearly the same price cluster into one level, not
  several; pivots a wide margin apart stay separate.
- **Identity.** Two runs over the same bars produce identical ids. Appending
  bars that extend a trendline preserves its id while `to` and `touches` move.
- **Bounds.** A pathological sawtooth cannot exceed the per-scale caps.
- **Route and tool.** `/structure` returns one scale and the MCP tool returns
  all of them from the same underlying result; a `/series` failure surfaces
  rather than becoming an empty success.

## Out of scope

- **Named formations.** Rita's job, by the design's central decision.
- **The workspace context bridge** — project B, its own spec.
- **Alerting.** The contract is built to support it later (stable ids, explicit
  `confirmed`), but nothing fires here.
- **Client-side detection.** The server owns every transform, matching the
  `vega_chart` decision in the bdobb-v2 widget spec.
- **Fibonacci retracements, channels, cycles.** All derive from pivots and can
  follow once the pivot layer is proven. Adding them now would triple the
  surface before anything is validated.
- **Any change to `app/ta/`** beyond importing `atr` and `adj`.
