"""Joining tick-derived bars onto cached history.

The seam sits at the first bar boundary FULLY covered by ticks, not at the
first tick. A bar that ticks only partly cover would be missing its own opening
trades -- a wrong candle that looks entirely plausible on a chart -- so the
straddling bar comes from history and ticks own only what they cover whole.
"""

from datetime import datetime, timedelta

EPOCH = datetime(1970, 1, 1)


def seam_boundary(first_tick: datetime, interval: str) -> datetime:
    """The first bar boundary at or after `first_tick`."""
    from kdb_store.aggregate import bucket_ns

    width = timedelta(microseconds=bucket_ns(interval) / 1000)
    elapsed = first_tick - EPOCH
    buckets, remainder = divmod(elapsed, width)
    if remainder:
        buckets += 1
    return EPOCH + buckets * width


def tick_capable(interval: str, window: timedelta) -> bool:
    """True when a bar of this interval can fit inside the tick window."""
    from kdb_store.aggregate import bucket_ns

    try:
        width = timedelta(microseconds=bucket_ns(interval) / 1000)
    except ValueError:
        return False
    return width <= window


def stitch(history: list[dict], ticks: list[dict], boundary: datetime) -> list[dict]:
    """History strictly before `boundary`, then tick-derived bars, time-ordered."""
    if not ticks:
        return list(history)
    kept = [row for row in history if row["date"] < boundary]
    if not kept:
        return sorted(ticks, key=lambda r: r["date"])
    return sorted(kept + list(ticks), key=lambda r: r["date"])
