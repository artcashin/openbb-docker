"""Parse FirstRate Data tick files into UTC-indexed frames.

Two facts drive this module:

1. File shape is detected by COLUMN COUNT, not by the bundled format document.
   That document lists the quote line with eight fields and names "offer price"
   twice, so it cannot be trusted literally.
2. Timestamps in the files are naive US Eastern. They are localized and
   converted to UTC here, once, at the boundary. Everything downstream is UTC.
   A timezone slip does not fail loudly -- it shows up as a 100% discrepancy
   rate much later, which is far more expensive to debug.
"""

from __future__ import annotations

import io

import pandas as pd

EASTERN = "America/New_York"

TRADE_COLUMNS = ["timestamp", "price", "volume", "exchange", "conditions"]
QUOTE_COLUMNS = [
    "timestamp",
    "bid_price",
    "bid_size",
    "bid_exchange",
    "ask_price",
    "ask_size",
    "ask_exchange",
]


class FirstRateFormatError(ValueError):
    """The input does not look like a FirstRate trade or quote file."""


def detect_kind(first_line: str) -> str:
    """Classify a data line as 'trade' or 'quote' by its field count."""
    n = len(first_line.split(","))
    if n == len(TRADE_COLUMNS):
        return "trade"
    if n in (len(QUOTE_COLUMNS), len(QUOTE_COLUMNS) + 1):
        return "quote"
    raise FirstRateFormatError(
        f"unrecognised FirstRate line with {n} field(s): {first_line[:80]!r}"
    )


def parse(text: str) -> tuple[str, pd.DataFrame]:
    """Parse a FirstRate trade or quote file into (kind, UTC-indexed frame)."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise FirstRateFormatError("no data rows found")

    kind = detect_kind(lines[0])
    columns = TRADE_COLUMNS if kind == "trade" else QUOTE_COLUMNS

    expected = len(columns)
    for offset, line in enumerate(lines, start=1):
        if len(line.split(",")) < expected:
            raise FirstRateFormatError(
                f"line {offset}: expected {expected} fields for a {kind} file, "
                f"got {len(line.split(','))}: {line[:80]!r}"
            )

    frame = pd.read_csv(
        io.StringIO("\n".join(lines)),
        header=None,
        names=columns,
        # A quote file may carry the extra eighth field; ignore anything past
        # the columns we name.
        usecols=range(expected),
        dtype={"conditions": "string"} if kind == "trade" else None,
    )

    stamps = pd.to_datetime(frame.pop("timestamp"), format="mixed")
    # ambiguous/nonexistent default to raising: a DST-invalid tick is a broken
    # file, not something to silently coerce.
    frame.index = (
        pd.DatetimeIndex(stamps)
        .tz_localize(EASTERN, ambiguous="raise", nonexistent="raise")
        .tz_convert("UTC")
    )

    if kind == "trade":
        frame["conditions"] = frame["conditions"].fillna("").astype(str)

    return kind, frame.sort_index()
