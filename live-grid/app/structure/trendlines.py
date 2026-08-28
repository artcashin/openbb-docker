"""Trendlines through same-direction pivots.

The line is defined by its two anchor pivots, not by a regression: that is what
a technician draws, and it keeps the line reproducible and its id stable.
"""
from __future__ import annotations

import polars as pl

from app.structure.atr import adjusted_atr
from app.structure.types import Trendline
from app.ta.exprs import adj

MIN_TOUCHES = 3     # the two anchors plus one confirmation


def find_trendlines(pivots, df: pl.DataFrame, scale: str,
                    touch_tol: float = 0.5, break_tol: float = 0.5,
                    atr_period: int = 14, cap: int = 8) -> list[Trendline]:
    if len(pivots) < MIN_TOUCHES or df.height == 0:
        return []
    atrs = adjusted_atr(df, atr_period)
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

                # Every pair among 3+ colinear pivots describes the same
                # physical line but would otherwise get a distinct id (its
                # own dates), so keeping-best-by-id never collapses them.
                # Only the pair matching the touching set's own earliest two
                # bars is canonical; every other pair re-describing that same
                # line is skipped here.
                earliest = sorted(touching, key=lambda p: p.bar)[:2]
                if (earliest[0].bar, earliest[1].bar) != (a.bar, b.bar):
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
