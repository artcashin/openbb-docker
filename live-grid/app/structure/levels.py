"""Support/resistance levels: one-dimensional clusters of pivot prices."""
from __future__ import annotations

import polars as pl

from app.structure.atr import adjusted_atr
from app.structure.types import Level, price_tag


def find_levels(pivots, df: pl.DataFrame, scale: str,
                cluster_tol: float = 0.75, atr_period: int = 14,
                cap: int = 8) -> list[Level]:
    if not pivots or df.height == 0:
        return []
    atrs = adjusted_atr(df, atr_period)
    bars = df.height

    # Agglomerate nearest-first: sort by price, start a new cluster whenever the
    # gap to the previous pivot exceeds the tolerance at that bar. For 1D,
    # sorted data this chained adjacent-gap scan is single-linkage clustering.
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
    return sorted(out, key=lambda lvl: lvl.score, reverse=True)[:cap]
