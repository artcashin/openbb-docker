"""The reference-source contract.

Two rules make the comparison honest:

1. Adapters CLASSIFY failures. "No data" is not one thing: a retention limit,
   an unentitled symbol, a bad key and a genuinely empty window are four
   different facts, and only some of them mean "try a coarser bar".
2. Stepping down the interval ladder is explicit and recorded, so the report
   can show what was asked for, what came back, and why.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import pandas as pd

INTERVAL_LADDER = ("1m", "5m", "15m", "30m", "1h", "1d")

# Only these mean "this source cannot serve this window at this resolution".
# Anything else is a real problem and must surface, not be stepped past.
_STEPPABLE = ("retention", "empty")

# The documented closed set a ReferenceError.kind may take:
# - retention: data retention limit; try a coarser interval
# - empty: no rows in the window; try a coarser interval
# - auth: authentication failure (bad key); permanent
# - entitlement: unauthorized for this plan; permanent
# - transport: network or server failure; permanent
# - not_covered: symbol/route not available from this source; permanent
_KINDS = _STEPPABLE + ("auth", "entitlement", "transport", "not_covered")


class ReferenceError(Exception):
    """A classified failure from a reference source."""

    def __init__(self, kind: str, detail: str):
        if kind not in _KINDS:
            raise ValueError(f"unknown ReferenceError kind {kind!r}; expected one of {_KINDS}")
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


@dataclass
class Attempt:
    interval: str
    error: ReferenceError | None = None


@dataclass
class ReferenceResult:
    frame: pd.DataFrame
    interval: str
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def stepped_down(self) -> bool:
        return len(self.attempts) > 1


class ReferenceAdapter(Protocol):
    name: str
    supported_intervals: tuple[str, ...]

    def fetch(
        self, symbol: str, start: Any, end: Any, interval: str
    ) -> pd.DataFrame: ...


def fetch_finest(
    adapter: ReferenceAdapter,
    symbol: str,
    start: Any,
    end: Any,
    wanted: str = "1m",
) -> ReferenceResult:
    """Fetch at the finest interval this source can actually serve.

    Walks the ladder from `wanted` downward in resolution, recording each
    attempt. Retention and empty-window failures step down; everything else
    (auth, entitlement, transport) is re-raised immediately.
    """
    if wanted not in INTERVAL_LADDER:
        raise ValueError(
            f"unsupported interval {wanted!r}; expected one of {', '.join(INTERVAL_LADDER)}"
        )

    attempts: list[Attempt] = []
    for interval in INTERVAL_LADDER[INTERVAL_LADDER.index(wanted) :]:
        if interval not in adapter.supported_intervals:
            continue
        try:
            frame = adapter.fetch(symbol, start, end, interval)
        except ReferenceError as err:
            attempts.append(Attempt(interval, err))
            if err.kind in _STEPPABLE:
                continue
            raise
        attempts.append(Attempt(interval))
        return ReferenceResult(frame=frame, interval=interval, attempts=attempts)

    raise ReferenceError(
        "empty",
        f"no interval from {adapter.name} could serve {symbol} over {start}..{end}",
    )
