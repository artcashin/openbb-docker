"""Compare two bar sets and render the result.

Three outcomes are kept apart on purpose, because conflating them is how
comparisons end up lying:

  bars compared      -- timestamps present on BOTH sides
  price discrepancies-- values that disagree beyond the tolerance
  coverage gaps      -- bars only one side has at all

A bar the reference simply does not have is not a pricing disagreement, and
counting it as one would inflate every number in the report.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from tick_lab.rollup import BAR_COLUMNS


@dataclass
class Discrepancy:
    timestamp: pd.Timestamp
    field: str
    ours: float
    theirs: float
    diff: float
    bps: float

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "field": self.field,
            "ours": self.ours,
            "theirs": self.theirs,
            "diff": self.diff,
            "bps": self.bps,
        }


@dataclass
class ComparisonReport:
    symbol: str
    interval: str
    tolerance: float
    bars_compared: int
    discrepancies: list[Discrepancy] = field(default_factory=list)
    ours_only: list[pd.Timestamp] = field(default_factory=list)
    theirs_only: list[pd.Timestamp] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def largest_close(self) -> Discrepancy | None:
        closes = [d for d in self.discrepancies if d.field == "close"]
        return max(closes, key=lambda d: abs(d.diff)) if closes else None

    def to_text(self) -> str:
        lines = [
            f"{self.symbol} @ {self.interval} (tolerance {self.tolerance})",
            "",
            f"  bars compared          : {self.bars_compared}",
            f"  price discrepancies    : {len(self.discrepancies)}",
            (
                f"  coverage gaps          : {len(self.ours_only)} ours-only, "
                f"{len(self.theirs_only)} reference-only"
            ),
        ]

        by_field = {c: 0 for c in BAR_COLUMNS}
        for d in self.discrepancies:
            by_field[d.field] = by_field.get(d.field, 0) + 1
        breakdown = ", ".join(f"{k}={v}" for k, v in by_field.items() if v)
        if breakdown:
            lines.append(f"  by field               : {breakdown}")

        worst = self.largest_close()
        if worst is not None:
            lines += [
                "",
                "  largest close discrepancy:",
                f"    at    {worst.timestamp.isoformat()}",
                f"    ours  {worst.ours}",
                f"    ref   {worst.theirs}",
                f"    diff  {worst.diff:+.4f} ({worst.bps:+.1f} bps)",
            ]
        else:
            lines += ["", "  largest close discrepancy: none"]

        if self.notes:
            lines += [""] + [f"  note: {n}" for n in self.notes]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        worst = self.largest_close()
        return {
            "symbol": self.symbol,
            "interval": self.interval,
            "tolerance": self.tolerance,
            "bars_compared": self.bars_compared,
            "price_discrepancies": [d.to_dict() for d in self.discrepancies],
            "coverage_gaps": {
                "ours_only": [t.isoformat() for t in self.ours_only],
                "reference_only": [t.isoformat() for t in self.theirs_only],
            },
            "largest_close_discrepancy": worst.to_dict() if worst else None,
            "notes": self.notes,
        }


def compare(
    ours: pd.DataFrame,
    theirs: pd.DataFrame,
    symbol: str,
    interval: str,
    tolerance: float = 0.01,
    notes: list[str] | None = None,
) -> ComparisonReport:
    """Diff two OHLCV frames sharing a UTC DatetimeIndex."""
    shared = ours.index.intersection(theirs.index).sort_values()

    discrepancies: list[Discrepancy] = []
    for ts in shared:
        for column in BAR_COLUMNS:
            a = float(ours.at[ts, column])
            b = float(theirs.at[ts, column])
            diff = a - b
            if abs(diff) <= tolerance:
                continue
            discrepancies.append(
                Discrepancy(
                    timestamp=ts,
                    field=column,
                    ours=a,
                    theirs=b,
                    diff=diff,
                    bps=(diff / b * 10_000.0) if b else float("nan"),
                )
            )

    return ComparisonReport(
        symbol=symbol,
        interval=interval,
        tolerance=tolerance,
        bars_compared=len(shared),
        discrepancies=discrepancies,
        ours_only=list(ours.index.difference(theirs.index).sort_values()),
        theirs_only=list(theirs.index.difference(ours.index).sort_values()),
        notes=list(notes or []),
    )
