"""Orchestration: bars in, StructureResult out. No I/O, no route knowledge."""
from __future__ import annotations

import polars as pl

from app.structure.levels import find_levels
from app.structure.pivots import find_pivots
from app.structure.trendlines import find_trendlines
from app.structure.types import ScaleResult, StructureResult, format_dates

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
    dates = format_dates(df) if df.height else []
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
