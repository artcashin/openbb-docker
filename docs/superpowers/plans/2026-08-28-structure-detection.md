# Chart structure detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect swing pivots, trendlines and support/resistance levels from OHLCV bars, and expose them to the chart over HTTP and to Rita over MCP.

**Architecture:** A new `live-grid/app/structure/` package of pure polars functions — no I/O, no route knowledge — mirroring the discipline of `app/ta/`. One engine, two projections: an HTTP route returns one scale for drawing, an MCP tool returns all scales for an agent to filter. Detection is ATR-normalised ZigZag; trendlines and levels derive from its pivots.

**Tech Stack:** Python 3.12, polars, FastAPI (live-grid), FastMCP (mcp_stores), pytest.

**Spec:** `docs/superpowers/specs/2026-08-28-structure-detection-design.md`

## Global Constraints

- **HARD DEPENDENCY, resolve before Task 1.** The spec cites
  `app/ta/exprs.adj()` for adjusted OHLC. That helper does **not exist on
  `main`** — it lives on branch `feat/ta-murphy-ohlcv-indicators` (commit
  `35b80d0`). Either merge that branch first, or this plan's Task 1 cannot
  import it. Verify with
  `grep -c '^def adj' live-grid/app/ta/exprs.py` — expect `1`, not `0`. Do not
  re-implement it; two definitions of the adjustment factor is exactly the
  duplication the spec warns about.
- **Detection runs on the ADJUSTED series.** A raw series reads every split as
  a real swing and invents pivots. Use `adj("high")`, `adj("low")`,
  `adj("close")`, never the bare columns.
- **ATR is Wilder's, raw OHLC, period 14** — the definition already pinned by
  the `atr` registry entry. Import `true_range()` from `app/ta/exprs.py`; do
  not re-derive it.
- **Every pivot carries `confirmed: bool` and the newest is routinely `false`.**
  Never emit a provisional pivot as confirmed, and never omit the flag. An
  agent told a formation completed on a provisional pivot states something that
  has not happened.
- **`id` is content-derived from ORIGIN anchors, never a counter.** Prices in
  ids format to exactly 4 decimals (`f"{price:.4f}"`) so float representation
  cannot produce two ids for one detection.
- **Tolerance defaults**, all in ATR units, all caller-overridable:
  `touch_tol=0.5`, `break_tol=0.5`, `cluster_tol=0.75`.
- **Output caps: 8 trendlines and 8 levels per scale**, highest score first.
- **Default scales:** `swing` k=1.0, `intermediate` k=3.0, `primary` k=8.0.
  Scales are detected independently and MUST NOT assume nesting.
- Run live-grid tests with `cd live-grid && python3 -m pytest tests/ -q`.
  Lint with `python3 -m ruff check app/ tests/` (ruff is pinned `0.15.22`).

## File structure

| file | responsibility |
|---|---|
| `live-grid/app/structure/types.py` | frozen dataclasses: `Pivot`, `Trendline`, `Level`, `ScaleResult`, `StructureResult`, and `to_dict` serialisation |
| `live-grid/app/structure/pivots.py` | ATR-ZigZag only |
| `live-grid/app/structure/trendlines.py` | pair enumeration, touch/violation counting, scoring |
| `live-grid/app/structure/levels.py` | 1-D clustering and scoring |
| `live-grid/app/structure/detect.py` | orchestration: bars + params → `StructureResult` |
| `live-grid/app/main.py` | the `/structure` route |
| `mcp_stores/server.py` | the `structure_detect` MCP tool |
| `mcp_stores/pyproject.toml` | gains `httpx` |

---

### Task 1: Types and ATR-ZigZag pivots

The foundation. Everything else consumes `Pivot`.

**Files:**
- Create: `live-grid/app/structure/__init__.py` (empty), `live-grid/app/structure/types.py`, `live-grid/app/structure/pivots.py`
- Test: `live-grid/tests/test_structure_pivots.py`

**Interfaces:**
- Consumes: `true_range()` and `adj()` from `app/ta/exprs.py`.
- Produces:
  - `Pivot(id: str, date: str, bar: int, price: float, kind: str, swing_atr: float, swing_pct: float, confirmed: bool)` — `kind` is `"high"` or `"low"`.
  - `find_pivots(df: pl.DataFrame, k: float, scale: str, atr_period: int = 14) -> list[Pivot]`

- [ ] **Step 1: Write the failing tests**

Create `live-grid/tests/test_structure_pivots.py`:

```python
import polars as pl
import pytest

from app.structure.pivots import find_pivots


def make_bars(closes: list[float], atr_hint: float = 1.0) -> pl.DataFrame:
    """A frame with a known, near-constant ATR so `k` is easy to reason about.

    high/low sit atr_hint/2 either side of close, which makes true range about
    atr_hint on every bar. adj_close == close, so the adjustment factor is 1.0
    and these tests isolate the ZigZag from the adjustment.
    """
    n = len(closes)
    return pl.DataFrame({
        "date": [f"2026-01-{i + 1:02d}" if i < 31 else f"2026-02-{i - 30:02d}"
                 for i in range(n)],
        "open": closes,
        "high": [c + atr_hint / 2 for c in closes],
        "low": [c - atr_hint / 2 for c in closes],
        "close": closes,
        "adj_close": closes,
        "volume": [1000.0] * n,
    })


def triangle(peaks: list[float], run: int = 10) -> list[float]:
    """Linear ramps between successive levels — turning points exactly at the
    junctions, so the right answer is knowable by construction."""
    out: list[float] = [peaks[0]]
    for a, b in zip(peaks, peaks[1:]):
        out += [a + (b - a) * (i + 1) / run for i in range(run)]
    return out


class TestFindPivots:
    def test_turning_points_land_on_the_exact_bars(self):
        closes = triangle([100.0, 120.0, 105.0, 130.0])
        pivots = find_pivots(make_bars(closes), k=3.0, scale="test")
        # Junctions of the ramps: index 10 (120), 20 (105).
        confirmed = [p for p in pivots if p.confirmed]
        assert [p.bar for p in confirmed] == [10, 20]
        assert [p.kind for p in confirmed] == ["high", "low"]
        assert confirmed[0].price == pytest.approx(120.0, abs=1e-9)

    def test_a_sub_threshold_wiggle_produces_no_extra_pivots(self):
        """The whole claim of the method: noise below k*ATR is not structure."""
        clean = triangle([100.0, 120.0, 105.0, 130.0])
        noisy = [c + (0.4 if i % 2 else -0.4) for i, c in enumerate(clean)]
        a = [p.bar for p in find_pivots(make_bars(clean), k=3.0, scale="t")
             if p.confirmed]
        b = [p.bar for p in find_pivots(make_bars(noisy), k=3.0, scale="t")
             if p.confirmed]
        assert a == b

    def test_atr_normalisation_makes_k_portable(self):
        """Same shape at a different price and volatility gives the same BARS.
        If this fails, k is not portable and the default scales are meaningless."""
        closes = triangle([100.0, 120.0, 105.0, 130.0])
        small = find_pivots(make_bars(closes, atr_hint=1.0), k=3.0, scale="t")
        scaled = [c * 10 for c in closes]
        big = find_pivots(make_bars(scaled, atr_hint=10.0), k=3.0, scale="t")
        assert [p.bar for p in small] == [p.bar for p in big]

    def test_the_last_extreme_is_provisional(self):
        """A series ending mid-swing has an unconfirmed final pivot."""
        closes = triangle([100.0, 120.0, 105.0]) + [106.0, 107.0]
        pivots = find_pivots(make_bars(closes), k=3.0, scale="t")
        assert pivots[-1].confirmed is False

    def test_extending_past_the_retracement_confirms_it(self):
        closes = triangle([100.0, 120.0, 105.0])
        short = find_pivots(make_bars(closes), k=3.0, scale="t")
        long = find_pivots(make_bars(closes + triangle([105.0, 130.0])), k=3.0,
                           scale="t")
        provisional = [p for p in short if not p.confirmed]
        assert provisional, "expected a provisional pivot to exist"
        # The same bar is now confirmed, and no pivot was invented before it.
        same = [p for p in long if p.bar == provisional[0].bar]
        assert len(same) == 1 and same[0].confirmed is True

    def test_a_bigger_k_yields_fewer_pivots(self):
        closes = triangle([100.0, 112.0, 104.0, 118.0, 108.0, 126.0])
        fine = find_pivots(make_bars(closes), k=1.0, scale="t")
        coarse = find_pivots(make_bars(closes), k=8.0, scale="t")
        assert len(coarse) < len(fine)

    def test_ids_are_deterministic_and_encode_the_anchor(self):
        closes = triangle([100.0, 120.0, 105.0, 130.0])
        a = find_pivots(make_bars(closes), k=3.0, scale="intermediate")
        b = find_pivots(make_bars(closes), k=3.0, scale="intermediate")
        assert [p.id for p in a] == [p.id for p in b]
        assert a[0].id == f"p:intermediate:high:{a[0].date}:120.0000"

    def test_a_frame_too_short_to_hold_a_swing_returns_nothing(self):
        pivots = find_pivots(make_bars([100.0, 101.0, 102.0]), k=8.0, scale="t")
        assert pivots == []

    def test_a_split_does_not_invent_a_pivot(self):
        """Detection runs on the ADJUSTED series. On raw prices a 2:1 split is a
        50% crash and the ZigZag confirms a pivot that never happened.

        Built the way a vendor actually reports one: raw OHLC halves at the
        split bar while adj_close stays continuous. The measured cost of getting
        this wrong elsewhere in this codebase was 14 MFI points, so it is worth
        a test rather than a comment."""
        closes = triangle([100.0, 120.0, 105.0, 130.0])
        clean = make_bars(closes)
        split_at = 15
        split = clean.with_columns([
            pl.when(pl.int_range(pl.len()) >= split_at)
              .then(pl.col(c) / 2).otherwise(pl.col(c)).alias(c)
            for c in ("open", "high", "low", "close")
        ])
        # adj_close is left continuous, so adj_close/close is 2.0 after the
        # split and the adjusted series is identical to the unsplit one.
        assert [p.bar for p in find_pivots(split, k=3.0, scale="t")] == \
               [p.bar for p in find_pivots(clean, k=3.0, scale="t")]
```

- [ ] **Step 2: Run them and verify they fail**

Run: `cd live-grid && python3 -m pytest tests/test_structure_pivots.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.structure'`

- [ ] **Step 3: Write the types**

Create `live-grid/app/structure/__init__.py` as an empty file, then
`live-grid/app/structure/types.py`:

```python
"""Structure detection output types.

These are NOT indicator columns. An Indicator emits one value per bar aligned
to the frame; a trendline is one object spanning many bars and a pivot exists
on a handful of bars out of hundreds. The output is sparse, overlapping and
variable-length, which is why this package exists beside app/ta rather than
inside it.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


def price_tag(price: float) -> str:
    """Prices inside ids, to exactly 4 decimals.

    Float repr is not stable enough to key on: 512.4 and 512.40000000000003 are
    the same detection and must not produce two ids.
    """
    return f"{price:.4f}"


@dataclass(frozen=True)
class Pivot:
    id: str
    date: str
    bar: int
    price: float
    kind: str            # "high" | "low"
    swing_atr: float     # magnitude of the move into this pivot, in ATR units
    swing_pct: float
    confirmed: bool      # False for the newest extreme -- see the spec


@dataclass(frozen=True)
class Trendline:
    id: str
    kind: str            # "support" | "resistance"
    from_date: str
    from_price: float
    to_date: str
    to_price: float
    slope_per_bar: float
    touches: int
    violations: int
    span_bars: int
    last_touch: str
    score: float


@dataclass(frozen=True)
class Level:
    id: str
    price: float
    touches: int
    first: str
    last: str
    sides: list[str]     # ["support"], ["resistance"], or both
    score: float


@dataclass(frozen=True)
class ScaleResult:
    name: str
    k: float
    pivots: list[Pivot] = field(default_factory=list)
    trendlines: list[Trendline] = field(default_factory=list)
    levels: list[Level] = field(default_factory=list)
    note: str = ""


@dataclass(frozen=True)
class StructureResult:
    symbol: str
    interval: str
    range: dict
    atr_period: int
    scales: list[ScaleResult]

    def to_dict(self) -> dict:
        """JSON shape. Trendline anchors nest, matching the spec's contract."""
        out = asdict(self)
        for scale in out["scales"]:
            scale["trendlines"] = [{
                "id": t["id"], "kind": t["kind"],
                "from": {"date": t["from_date"], "price": t["from_price"]},
                "to": {"date": t["to_date"], "price": t["to_price"]},
                "slope_per_bar": t["slope_per_bar"], "touches": t["touches"],
                "violations": t["violations"], "span_bars": t["span_bars"],
                "last_touch": t["last_touch"], "score": t["score"],
            } for t in scale["trendlines"]]
        return out
```

- [ ] **Step 4: Write the ZigZag**

Create `live-grid/app/structure/pivots.py`:

```python
"""ATR-normalised ZigZag pivot detection.

A pivot confirms when price retraces from the running extreme by k * ATR, with
the threshold read at the bar where the extreme formed -- so the move is judged
against the volatility that produced it, not today's.

Kernel-regression smoothing (Lo/Mamaysky/Wang) was rejected as the engine: its
extremum lands on a different bar than the real high, and the snap-back step
that fixes that reintroduces the arbitrariness the smoothing removed. Both
consumers need exact coordinates.
"""
from __future__ import annotations

import polars as pl

from app.structure.types import Pivot, price_tag
from app.ta.exprs import adj, true_range


def _series(df: pl.DataFrame, atr_period: int):
    """Adjusted high/low/close plus Wilder ATR, as plain Python lists."""
    frame = df.with_columns([
        adj("high").alias("_h"), adj("low").alias("_l"), adj("close").alias("_c"),
        true_range().ewm_mean(alpha=1 / atr_period, adjust=False,
                              ignore_nulls=True).alias("_atr"),
    ])
    return (frame["_h"].to_list(), frame["_l"].to_list(), frame["_c"].to_list(),
            frame["_atr"].to_list(), frame["date"].cast(pl.Utf8).to_list())


def find_pivots(df: pl.DataFrame, k: float, scale: str,
                atr_period: int = 14) -> list[Pivot]:
    if df.height < 2:
        return []
    highs, lows, closes, atrs, dates = _series(df, atr_period)

    def emit(bar: int, kind: str, prev_price: float | None,
             confirmed: bool) -> Pivot:
        price = highs[bar] if kind == "high" else lows[bar]
        atr = atrs[bar] or 0.0
        move = 0.0 if prev_price is None else abs(price - prev_price)
        return Pivot(
            id=f"p:{scale}:{kind}:{dates[bar]}:{price_tag(price)}",
            date=dates[bar], bar=bar, price=price, kind=kind,
            swing_atr=round(move / atr, 4) if atr else 0.0,
            swing_pct=round(100 * move / prev_price, 4) if prev_price else 0.0,
            confirmed=confirmed,
        )

    pivots: list[Pivot] = []
    direction: str | None = None      # "up" while tracking a high
    ext_bar = 0
    prev_price: float | None = None

    for i in range(1, len(closes)):
        threshold = k * (atrs[ext_bar] or 0.0)
        if threshold <= 0:
            continue
        if direction is None:
            if highs[i] - lows[ext_bar] >= threshold:
                direction, ext_bar = "up", i
            elif highs[ext_bar] - lows[i] >= threshold:
                direction, ext_bar = "down", i
            continue
        if direction == "up":
            if highs[i] >= highs[ext_bar]:
                ext_bar = i
            elif highs[ext_bar] - closes[i] >= threshold:
                pivots.append(emit(ext_bar, "high", prev_price, True))
                prev_price, direction, ext_bar = highs[ext_bar], "down", i
        else:
            if lows[i] <= lows[ext_bar]:
                ext_bar = i
            elif closes[i] - lows[ext_bar] >= threshold:
                pivots.append(emit(ext_bar, "low", prev_price, True))
                prev_price, direction, ext_bar = lows[ext_bar], "up", i

    # The running extreme has not retraced far enough to be a pivot yet. It is
    # reported so a caller can see the developing swing, and flagged so nobody
    # treats it as settled.
    if direction is not None:
        pivots.append(emit(ext_bar, "high" if direction == "up" else "low",
                           prev_price, False))
    return pivots
```

- [ ] **Step 5: Run them and verify they pass**

Run: `cd live-grid && python3 -m pytest tests/test_structure_pivots.py -q`
Expected: PASS (9 tests). If `test_atr_normalisation_makes_k_portable` fails,
the threshold is being read at the wrong bar — re-check that it uses
`atrs[ext_bar]`, not `atrs[i]`.

- [ ] **Step 6: Prove the provisional flag is load-bearing**

Temporarily change the final `emit(..., False)` to `True` and re-run. Expected:
`test_the_last_extreme_is_provisional` FAILS. Restore it and confirm it passes.
**Report both results** — the flag is the spec's most safety-relevant field and
a test that cannot detect its removal is not a test.

- [ ] **Step 7: Lint and commit**

```bash
cd live-grid && python3 -m ruff check app/structure tests/test_structure_pivots.py
git add live-grid/app/structure live-grid/tests/test_structure_pivots.py
git commit -m "feat(structure): ATR-normalised ZigZag pivots"
```

---

### Task 2: Trendlines

**Files:**
- Create: `live-grid/app/structure/trendlines.py`
- Test: `live-grid/tests/test_structure_trendlines.py`

**Interfaces:**
- Consumes: `Pivot`, `Trendline`, `price_tag` from Task 1.
- Produces: `find_trendlines(pivots: list[Pivot], df: pl.DataFrame, scale: str, touch_tol: float = 0.5, break_tol: float = 0.5, atr_period: int = 14, cap: int = 8) -> list[Trendline]`

- [ ] **Step 1: Write the failing tests**

Create `live-grid/tests/test_structure_trendlines.py`:

```python
import polars as pl
import pytest

from app.structure.trendlines import find_trendlines
from app.structure.types import Pivot
from tests.test_structure_pivots import make_bars


def pivot(bar, price, kind, date=None):
    return Pivot(id=f"p:t:{kind}:{bar}", date=date or f"2026-01-{bar + 1:02d}",
                 bar=bar, price=price, kind=kind, swing_atr=3.0,
                 swing_pct=3.0, confirmed=True)


class TestFindTrendlines:
    def test_three_pivots_on_a_line_yield_one_trendline(self):
        # Rising support: 100 at bar 0, 110 at 10, 120 at 20 -- slope 1.0/bar.
        closes = [100.0 + i for i in range(30)]
        pivots = [pivot(0, 100.0, "low"), pivot(10, 110.0, "low"),
                  pivot(20, 120.0, "low")]
        lines = find_trendlines(pivots, make_bars(closes), scale="t")
        assert len(lines) == 1
        assert lines[0].kind == "support"
        assert lines[0].touches == 3
        assert lines[0].slope_per_bar == pytest.approx(1.0, abs=1e-9)

    def test_two_pivots_alone_yield_nothing(self):
        """Two points define ANY line, so two touches prove nothing."""
        closes = [100.0 + i for i in range(30)]
        pivots = [pivot(0, 100.0, "low"), pivot(10, 110.0, "low")]
        assert find_trendlines(pivots, make_bars(closes), scale="t") == []

    def test_a_line_broken_before_its_last_touch_is_rejected(self):
        closes = [100.0 + i for i in range(30)]
        closes[15] = 80.0                      # decisive close through support
        pivots = [pivot(0, 100.0, "low"), pivot(10, 110.0, "low"),
                  pivot(20, 120.0, "low")]
        assert find_trendlines(pivots, make_bars(closes), scale="t") == []

    def test_a_break_after_the_last_touch_is_reported_not_withheld(self):
        """A broken trendline is information. Keep it, count the violation."""
        closes = [100.0 + i for i in range(30)]
        closes[25] = 80.0
        pivots = [pivot(0, 100.0, "low"), pivot(10, 110.0, "low"),
                  pivot(20, 120.0, "low")]
        lines = find_trendlines(pivots, make_bars(closes), scale="t")
        assert len(lines) == 1 and lines[0].violations >= 1

    def test_highs_make_resistance_and_lows_make_support(self):
        closes = [100.0 + i for i in range(30)]
        highs = [pivot(0, 100.0, "high"), pivot(10, 110.0, "high"),
                 pivot(20, 120.0, "high")]
        lines = find_trendlines(highs, make_bars(closes), scale="t")
        assert lines and all(l.kind == "resistance" for l in lines)

    def test_mixed_direction_pivots_never_form_one_line(self):
        closes = [100.0 + i for i in range(30)]
        mixed = [pivot(0, 100.0, "low"), pivot(10, 110.0, "high"),
                 pivot(20, 120.0, "low")]
        assert find_trendlines(mixed, make_bars(closes), scale="t") == []

    def test_output_is_capped(self):
        closes = [100.0] * 60
        pivots = [pivot(i, 100.0, "low") for i in range(0, 60, 2)]
        lines = find_trendlines(pivots, make_bars(closes), scale="t", cap=8)
        assert len(lines) <= 8

    def test_id_comes_from_the_origin_anchors_not_the_extent(self):
        closes = [100.0 + i for i in range(40)]
        three = [pivot(0, 100.0, "low"), pivot(10, 110.0, "low"),
                 pivot(20, 120.0, "low")]
        four = three + [pivot(30, 130.0, "low")]
        a = find_trendlines(three, make_bars(closes), scale="t")[0]
        b = find_trendlines(four, make_bars(closes), scale="t")[0]
        assert a.id == b.id            # identity survives the extension
        assert b.touches > a.touches   # extent does not
```

- [ ] **Step 2: Run them and verify they fail**

Run: `cd live-grid && python3 -m pytest tests/test_structure_trendlines.py -q`
Expected: FAIL — no module `app.structure.trendlines`

- [ ] **Step 3: Implement**

Create `live-grid/app/structure/trendlines.py`:

```python
"""Trendlines through same-direction pivots.

The line is defined by its two anchor pivots, not by a regression: that is what
a technician draws, and it keeps the line reproducible and its id stable.
"""
from __future__ import annotations

import polars as pl

from app.structure.types import Trendline, price_tag
from app.ta.exprs import adj, true_range

MIN_TOUCHES = 3     # the two anchors plus one confirmation


def _atr(df: pl.DataFrame, atr_period: int) -> list[float]:
    return df.select(
        true_range().ewm_mean(alpha=1 / atr_period, adjust=False,
                              ignore_nulls=True).alias("a")
    )["a"].to_list()


def find_trendlines(pivots, df: pl.DataFrame, scale: str,
                    touch_tol: float = 0.5, break_tol: float = 0.5,
                    atr_period: int = 14, cap: int = 8) -> list[Trendline]:
    if len(pivots) < MIN_TOUCHES or df.height == 0:
        return []
    atrs = _atr(df, atr_period)
    closes = df.select(adj("close").alias("c"))["c"].to_list()
    dates = df["date"].cast(pl.Utf8).to_list()
    out: list[Trendline] = []

    for kind, want in (("support", "low"), ("resistance", "high")):
        same = [p for p in pivots if p.kind == want]
        for i in range(len(same)):
            for j in range(i + 1, len(same)):
                a, b = same[i], same[j]
                if b.bar == a.bar:
                    continue
                slope = (b.price - a.price) / (b.bar - a.bar)
                at = lambda x: a.price + slope * (x - a.bar)  # noqa: E731

                touching = [p for p in same
                            if abs(p.price - at(p.bar)) <= touch_tol * (atrs[p.bar] or 0.0)]
                if len(touching) < MIN_TOUCHES:
                    continue
                last_touch_bar = max(p.bar for p in touching)

                # Closes, not wicks: a line pierced intraday and reclaimed is
                # still a line, and using highs/lows rejects almost every real
                # trendline.
                def broken(x: int) -> bool:
                    tol = break_tol * (atrs[x] or 0.0)
                    return (closes[x] < at(x) - tol if kind == "support"
                            else closes[x] > at(x) + tol)

                if any(broken(x) for x in range(a.bar, last_touch_bar + 1)):
                    continue
                violations = sum(broken(x) for x in range(last_touch_bar + 1, len(closes)))

                span = last_touch_bar - a.bar
                recency = 1.0 - (len(closes) - 1 - last_touch_bar) / max(len(closes) - 1, 1)
                out.append(Trendline(
                    id=f"t:{scale}:{kind}:{a.date}:{b.date}",
                    kind=kind, from_date=a.date, from_price=a.price,
                    to_date=dates[last_touch_bar], to_price=at(last_touch_bar),
                    slope_per_bar=round(slope, 6), touches=len(touching),
                    violations=violations, span_bars=span,
                    last_touch=dates[last_touch_bar],
                    score=round(len(touching) + span / 100.0 + recency, 4),
                ))

    # Dedup: several anchor pairs describe one line. Keep the best per id.
    best: dict[str, Trendline] = {}
    for line in out:
        if line.id not in best or line.score > best[line.id].score:
            best[line.id] = line
    return sorted(best.values(), key=lambda t: t.score, reverse=True)[:cap]
```

- [ ] **Step 4: Run them and verify they pass**

Run: `cd live-grid && python3 -m pytest tests/test_structure_trendlines.py -q`
Expected: PASS (8 tests).

- [ ] **Step 5: Prove the break rule bites**

Temporarily remove the `if any(broken(x) ...): continue` guard and re-run.
Expected: `test_a_line_broken_before_its_last_touch_is_rejected` FAILS. Restore
and confirm. **Report both results.**

- [ ] **Step 6: Lint and commit**

```bash
cd live-grid && python3 -m ruff check app/structure tests/test_structure_trendlines.py
git add live-grid/app/structure/trendlines.py live-grid/tests/test_structure_trendlines.py
git commit -m "feat(structure): trendlines through same-direction pivots"
```

---

### Task 3: Support/resistance levels

**Files:**
- Create: `live-grid/app/structure/levels.py`
- Test: `live-grid/tests/test_structure_levels.py`

**Interfaces:**
- Consumes: `Pivot`, `Level`, `price_tag` from Task 1.
- Produces: `find_levels(pivots: list[Pivot], df: pl.DataFrame, scale: str, cluster_tol: float = 0.75, atr_period: int = 14, cap: int = 8) -> list[Level]`

- [ ] **Step 1: Write the failing tests**

Create `live-grid/tests/test_structure_levels.py`:

```python
import pytest

from app.structure.levels import find_levels
from tests.test_structure_pivots import make_bars
from tests.test_structure_trendlines import pivot


class TestFindLevels:
    def test_pivots_at_nearly_one_price_make_one_level(self):
        closes = [100.0] * 40
        pivots = [pivot(5, 120.0, "high"), pivot(15, 120.2, "high"),
                  pivot(25, 119.9, "high")]
        levels = find_levels(pivots, make_bars(closes), scale="t")
        assert len(levels) == 1
        assert levels[0].touches == 3
        assert levels[0].price == pytest.approx(120.0, abs=0.3)

    def test_pivots_far_apart_stay_separate(self):
        closes = [100.0] * 40
        pivots = [pivot(5, 120.0, "high"), pivot(15, 150.0, "high")]
        assert len(find_levels(pivots, make_bars(closes), scale="t")) == 2

    def test_a_level_tested_from_both_sides_records_both(self):
        """The classic flip -- resistance that later held as support."""
        closes = [100.0] * 40
        pivots = [pivot(5, 120.0, "high"), pivot(25, 120.1, "low")]
        level = find_levels(pivots, make_bars(closes), scale="t")[0]
        assert sorted(level.sides) == ["resistance", "support"]

    def test_first_and_last_span_the_members(self):
        closes = [100.0] * 40
        pivots = [pivot(5, 120.0, "high"), pivot(25, 120.1, "high")]
        level = find_levels(pivots, make_bars(closes), scale="t")[0]
        assert level.first == pivots[0].date and level.last == pivots[1].date

    def test_more_touches_scores_higher(self):
        closes = [100.0] * 60
        few = [pivot(5, 120.0, "high"), pivot(15, 120.1, "high")]
        many = few + [pivot(25, 119.9, "high"), pivot(35, 120.05, "high")]
        a = find_levels(few, make_bars(closes), scale="t")[0]
        b = find_levels(many, make_bars(closes), scale="t")[0]
        assert b.score > a.score

    def test_output_is_capped(self):
        closes = [100.0] * 100
        pivots = [pivot(i, 100.0 + i * 10, "high") for i in range(9)]
        assert len(find_levels(pivots, make_bars(closes), scale="t", cap=8)) <= 8

    def test_ids_are_deterministic(self):
        closes = [100.0] * 40
        pivots = [pivot(5, 120.0, "high"), pivot(15, 120.1, "high")]
        a = find_levels(pivots, make_bars(closes), scale="s")
        b = find_levels(pivots, make_bars(closes), scale="s")
        assert [x.id for x in a] == [x.id for x in b]
```

- [ ] **Step 2: Run them and verify they fail**

Run: `cd live-grid && python3 -m pytest tests/test_structure_levels.py -q`
Expected: FAIL — no module `app.structure.levels`

- [ ] **Step 3: Implement**

Create `live-grid/app/structure/levels.py`:

```python
"""Support/resistance levels: one-dimensional clusters of pivot prices."""
from __future__ import annotations

import polars as pl

from app.structure.types import Level, price_tag
from app.ta.exprs import true_range


def find_levels(pivots, df: pl.DataFrame, scale: str,
                cluster_tol: float = 0.75, atr_period: int = 14,
                cap: int = 8) -> list[Level]:
    if not pivots or df.height == 0:
        return []
    atrs = df.select(
        true_range().ewm_mean(alpha=1 / atr_period, adjust=False,
                              ignore_nulls=True).alias("a")
    )["a"].to_list()
    bars = df.height

    # Agglomerate nearest-first: sort by price, start a new cluster whenever the
    # gap to the previous pivot exceeds the tolerance at that bar.
    ordered = sorted(pivots, key=lambda p: p.price)
    clusters: list[list] = [[ordered[0]]]
    for p in ordered[1:]:
        tol = cluster_tol * (atrs[p.bar] or 0.0)
        if abs(p.price - clusters[-1][-1].price) <= tol:
            clusters[-1].append(p)
        else:
            clusters.append([p])

    out: list[Level] = []
    for members in clusters:
        price = sum(m.price for m in members) / len(members)
        dates = sorted(m.date for m in members)
        last_bar = max(m.bar for m in members)
        recency = 1.0 - (bars - 1 - last_bar) / max(bars - 1, 1)
        sides = sorted({"resistance" if m.kind == "high" else "support"
                        for m in members})
        span = (max(m.bar for m in members) - min(m.bar for m in members)) / max(bars - 1, 1)
        out.append(Level(
            id=f"l:{scale}:{price_tag(price)}",
            price=round(price, 4), touches=len(members),
            first=dates[0], last=dates[-1], sides=sides,
            score=round(len(members) + span + recency, 4),
        ))
    return sorted(out, key=lambda l: l.score, reverse=True)[:cap]
```

- [ ] **Step 4: Run them and verify they pass**

Run: `cd live-grid && python3 -m pytest tests/test_structure_levels.py -q`
Expected: PASS (7 tests).

- [ ] **Step 5: Lint and commit**

```bash
cd live-grid && python3 -m ruff check app/structure tests/test_structure_levels.py
git add live-grid/app/structure/levels.py live-grid/tests/test_structure_levels.py
git commit -m "feat(structure): support/resistance level clustering"
```

---

### Task 4: Orchestration and the `/structure` route

**Files:**
- Create: `live-grid/app/structure/detect.py`
- Modify: `live-grid/app/main.py` (add a route beside `@app.get("/ta_chart")`, around line 297)
- Test: `live-grid/tests/test_structure_detect.py`

**Interfaces:**
- Consumes: `find_pivots`, `find_trendlines`, `find_levels`, all types.
- Produces:
  - `DEFAULT_SCALES: dict[str, float]` = `{"swing": 1.0, "intermediate": 3.0, "primary": 8.0}`
  - `scale_for_bars(n: int) -> str`
  - `detect(df, symbol, interval, scales=None, atr_period=14, **tols) -> StructureResult`

- [ ] **Step 1: Write the failing tests**

Create `live-grid/tests/test_structure_detect.py`:

```python
import pytest

from app.structure.detect import DEFAULT_SCALES, detect, scale_for_bars
from tests.test_structure_pivots import make_bars, triangle


class TestScaleSelection:
    @pytest.mark.parametrize("bars,expected", [
        (30, "swing"), (119, "swing"), (120, "intermediate"),
        (600, "intermediate"), (601, "primary"), (5000, "primary"),
    ])
    def test_scale_is_chosen_by_bar_count(self, bars, expected):
        assert scale_for_bars(bars) == expected


class TestDetect:
    def test_all_three_default_scales_are_returned(self):
        closes = triangle([100.0, 130.0, 105.0, 140.0, 110.0], run=25)
        result = detect(make_bars(closes), "TEST.US", "1d")
        assert [s.name for s in result.scales] == list(DEFAULT_SCALES)

    def test_scales_are_independent_not_nested(self):
        """The spec forbids assuming containment. A coarse scale may legitimately
        pick a pivot the fine scale skipped, so nothing may filter by subset."""
        closes = triangle([100.0, 130.0, 105.0, 140.0, 110.0], run=25)
        result = detect(make_bars(closes), "TEST.US", "1d")
        by_name = {s.name: s for s in result.scales}
        assert len(by_name["swing"].pivots) >= len(by_name["primary"].pivots)

    def test_a_window_too_short_for_a_scale_returns_a_note_not_an_error(self):
        result = detect(make_bars([100.0 + i for i in range(20)]), "T", "1d")
        primary = [s for s in result.scales if s.name == "primary"][0]
        assert primary.pivots == [] and primary.note

    def test_the_range_reports_what_was_actually_analysed(self):
        closes = triangle([100.0, 130.0, 105.0], run=25)
        result = detect(make_bars(closes), "TEST.US", "1d")
        assert result.range["bars"] == len(closes)
        assert result.symbol == "TEST.US" and result.interval == "1d"

    def test_to_dict_nests_trendline_anchors(self):
        closes = triangle([100.0, 130.0, 105.0, 140.0], run=25)
        payload = detect(make_bars(closes), "T", "1d").to_dict()
        for scale in payload["scales"]:
            for line in scale["trendlines"]:
                assert set(line["from"]) == {"date", "price"}
                assert "from_date" not in line

    def test_detection_is_deterministic(self):
        closes = triangle([100.0, 130.0, 105.0, 140.0], run=25)
        a = detect(make_bars(closes), "T", "1d").to_dict()
        b = detect(make_bars(closes), "T", "1d").to_dict()
        assert a == b
```

- [ ] **Step 2: Run them and verify they fail**

Run: `cd live-grid && python3 -m pytest tests/test_structure_detect.py -q`
Expected: FAIL — no module `app.structure.detect`

- [ ] **Step 3: Implement the orchestrator**

Create `live-grid/app/structure/detect.py`:

```python
"""Orchestration: bars in, StructureResult out. No I/O, no route knowledge."""
from __future__ import annotations

import polars as pl

from app.structure.levels import find_levels
from app.structure.pivots import find_pivots
from app.structure.trendlines import find_trendlines
from app.structure.types import ScaleResult, StructureResult

DEFAULT_SCALES: dict[str, float] = {
    "swing": 1.0, "intermediate": 3.0, "primary": 8.0,
}

# Round numbers, not derived: a quarter of daily bars lands on swing structure
# and several years lands on primary. Tuning these is expected.
def scale_for_bars(n: int) -> str:
    if n < 120:
        return "swing"
    if n <= 600:
        return "intermediate"
    return "primary"


def detect(df: pl.DataFrame, symbol: str, interval: str,
           scales: dict[str, float] | None = None, atr_period: int = 14,
           touch_tol: float = 0.5, break_tol: float = 0.5,
           cluster_tol: float = 0.75, cap: int = 8) -> StructureResult:
    scales = scales or DEFAULT_SCALES
    dates = df["date"].cast(pl.Utf8).to_list() if df.height else []
    results: list[ScaleResult] = []

    for name, k in scales.items():
        pivots = find_pivots(df, k=k, scale=name, atr_period=atr_period)
        if not pivots:
            # Asking for primary structure in a 30-bar window is a reasonable
            # question whose answer is "there isn't any" -- not an error.
            results.append(ScaleResult(
                name=name, k=k,
                note=f"no {name} structure in {df.height} bars at k={k}",
            ))
            continue
        results.append(ScaleResult(
            name=name, k=k, pivots=pivots,
            trendlines=find_trendlines(pivots, df, scale=name,
                                       touch_tol=touch_tol, break_tol=break_tol,
                                       atr_period=atr_period, cap=cap),
            levels=find_levels(pivots, df, scale=name, cluster_tol=cluster_tol,
                               atr_period=atr_period, cap=cap),
        ))

    return StructureResult(
        symbol=symbol, interval=interval, atr_period=atr_period,
        range={"start": dates[0] if dates else None,
               "end": dates[-1] if dates else None, "bars": df.height},
        scales=results,
    )
```

- [ ] **Step 4: Run them and verify they pass**

Run: `cd live-grid && python3 -m pytest tests/test_structure_detect.py -q`
Expected: PASS (11 tests, counting the parametrised cases).

- [ ] **Step 5: Add the route**

In `live-grid/app/main.py`, immediately after the `@app.get("/ta_chart")`
handler (around line 297), add — matching the surrounding handlers' style:

```python
    @app.get("/structure")
    async def structure(symbol: str = "AAPL", interval: str = "1d",
                        start: str | None = None, end: str | None = None,
                        scale: str | None = None, provider: str = "kdb"):
        """Chart structure for one scale. The MCP tool returns all of them.

        Reuses build_series so the kdb read-through cache and its seam logic
        remain the single answer to "get me correct bars for this window".
        """
        from app.structure.detect import DEFAULT_SCALES, detect, scale_for_bars

        s, e = _window(start, end)
        bars, meta = await build_series(symbol, interval, s, e, recorder,
                                        _tick_window(), provider)
        frame = pl.DataFrame(bars) if bars else pl.DataFrame()
        chosen = scale or scale_for_bars(frame.height)
        if chosen not in DEFAULT_SCALES:
            raise HTTPException(
                status_code=400,
                detail=f"unknown scale {chosen!r}; expected one of "
                       f"{sorted(DEFAULT_SCALES)}",
            )
        result = detect(frame, symbol, interval,
                        scales={chosen: DEFAULT_SCALES[chosen]})
        return {**result.to_dict(), "cache": meta}
```

Confirm `HTTPException` and `pl` are already imported in `main.py`; add them to
the existing import block if not.

- [ ] **Step 6: Test the route**

Append to `live-grid/tests/test_structure_detect.py`:

```python
class TestStructureRoute:
    def test_the_route_returns_one_scale_chosen_by_range(self, client):
        body = client.get("/structure?symbol=AAPL&interval=1d").json()
        assert len(body["scales"]) == 1

    def test_an_unknown_scale_is_rejected_with_the_valid_names(self, client):
        r = client.get("/structure?symbol=AAPL&scale=hourly")
        assert r.status_code == 400
        assert "primary" in r.json()["detail"]
```

Use the same `client` fixture the existing route tests use — see
`tests/test_chart_routes.py` for how it is constructed.

Run: `cd live-grid && python3 -m pytest tests/test_structure_detect.py -q`
Expected: PASS.

- [ ] **Step 7: Full suite, lint, commit**

```bash
cd live-grid && python3 -m pytest tests/ -q && python3 -m ruff check app/ tests/
git add live-grid/app/structure/detect.py live-grid/app/main.py live-grid/tests/test_structure_detect.py
git commit -m "feat(structure): orchestration and the /structure route"
```

---

### Task 5: The MCP tool

**Files:**
- Modify: `mcp_stores/server.py` (add the tool, and add it to the registration tuple at ~line 325)
- Modify: `mcp_stores/pyproject.toml` (add `httpx`)
- Test: `mcp_stores/test_server.py`

**Interfaces:**
- Consumes: the `/structure` route from Task 4.
- Produces: MCP tool `structure_detect(symbol, interval="1d", start=None, end=None) -> dict`

- [ ] **Step 1: Add the dependency**

In `mcp_stores/pyproject.toml`, add `"httpx"` to the `dependencies` list (it
currently holds fastmcp, pandas, uvicorn, starlette, arcticdb, pykx).

- [ ] **Step 2: Write the failing tests**

Append to `mcp_stores/test_server.py`:

```python
class TestStructureDetect:
    def test_it_returns_every_scale(self, monkeypatch):
        """The chart route returns one scale; this tool returns all of them, so
        an agent can weigh structure that holds across scales."""
        import server

        payload = {"symbol": "SPY.US", "scales": [
            {"name": "swing"}, {"name": "intermediate"}, {"name": "primary"}]}
        monkeypatch.setattr(server, "_structure_get", lambda **kw: payload)
        out = server.structure_detect("SPY.US")
        assert [s["name"] for s in out["scales"]] == [
            "swing", "intermediate", "primary"]

    def test_an_upstream_failure_raises_rather_than_returning_empty(self, monkeypatch):
        """An agent cannot tell 'no structure' from 'no data' unless we say so."""
        import server

        def boom(**kw):
            raise RuntimeError("live-grid unreachable")

        monkeypatch.setattr(server, "_structure_get", boom)
        with pytest.raises(RuntimeError):
            server.structure_detect("SPY.US")

    def test_it_is_registered_as_a_tool(self):
        import server
        assert server.structure_detect in server.REGISTERED_TOOLS
```

- [ ] **Step 3: Run them and verify they fail**

Run: `cd mcp_stores && python3 -m pytest test_server.py -q -k Structure`
Expected: FAIL — `AttributeError: module 'server' has no attribute 'structure_detect'`

- [ ] **Step 4: Implement**

In `mcp_stores/server.py`, add near the other helpers:

```python
LIVE_GRID_URL = os.environ.get("LIVE_GRID_URL", "http://live-grid:6903")


def _structure_get(**params) -> dict:
    """One call to live-grid's /structure, one scale per call.

    live-grid owns the data path: /series holds the kdb read-through cache and
    its seam logic, and this tool must not grow a second answer to "get me
    correct bars". Reachable by service name because both containers share the
    openbb-internal bridge.
    """
    import httpx

    r = httpx.get(f"{LIVE_GRID_URL}/structure", params=params, timeout=30.0)
    r.raise_for_status()
    return r.json()


def structure_detect(symbol: str, interval: str = "1d",
                     start: str | None = None, end: str | None = None) -> dict:
    """Detect chart structure: swing pivots, trendlines and support/resistance.

    Returns geometry, NOT named formations -- no "head and shoulders". Read the
    pivots and name what you see.

    Each pivot carries `confirmed`. The most recent one is routinely
    `confirmed: false`: it is the developing swing and may still extend. Never
    describe a formation as complete when it rests on an unconfirmed pivot.

    Results come at three scales -- swing, intermediate, primary -- detected
    independently. Structure appearing at more than one scale is stronger.
    `swing_atr` is the size of the move into a pivot in units of average true
    range, so it is comparable across symbols and intervals.
    """
    scales = ["swing", "intermediate", "primary"]
    merged: dict | None = None
    for name in scales:
        params = {"symbol": symbol, "interval": interval, "scale": name}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        page = _structure_get(**params)
        if merged is None:
            merged = {k: v for k, v in page.items() if k != "scales"}
            merged["scales"] = []
        merged["scales"] += page.get("scales", [])
    return merged or {"symbol": symbol, "scales": []}
```

Then add `structure_detect` to the registration tuple (~line 325) and expose
the tuple so the test can assert registration:

```python
REGISTERED_TOOLS = (
    arctic_list_libraries,
    arctic_list_symbols,
    arctic_read,
    kdb_tables,
    kdb_table_schema,
    kdb_select,
    structure_detect,
)
for _fn in REGISTERED_TOOLS:
    mcp.tool(_fn)
```

Confirm `os` is imported at the top of `server.py`; add it if not.

- [ ] **Step 5: Run them and verify they pass**

Run: `cd mcp_stores && python3 -m pytest test_server.py -q`
Expected: PASS.

- [ ] **Step 6: Lint and commit**

```bash
cd mcp_stores && python3 -m ruff check server.py test_server.py
git add mcp_stores/server.py mcp_stores/test_server.py mcp_stores/pyproject.toml
git commit -m "feat(mcp): structure_detect tool over the openbb-internal bridge"
```

- [ ] **Step 7: Verify end to end against the running stack**

The unit tests mock the HTTP call, so nothing so far proves the two services
actually talk. From a container on `openbb-internal`:

```bash
docker exec openbb-stores-mcp python3 -c "import server; print(len(server.structure_detect('AAPL.US')['scales']))"
```

Expected: `3`. A connection error here means the bridge wiring, not the code —
check that both services list `networks: [openbb-internal]`.
