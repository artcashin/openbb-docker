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
