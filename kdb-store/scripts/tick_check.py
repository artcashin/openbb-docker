"""Real-q check for tick recording and aggregation. Needs a licence; not in CI."""

import sys
from datetime import datetime, timedelta

import pandas as pd

from kdb_store.aggregate import aggregate_ticks
from kdb_store.config import resolve_config
from kdb_store.session import KdbSession
from kdb_store.store import KdbStore

failures = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name} {detail}")
    if not ok:
        failures.append(name)


session = KdbSession(resolve_config())
store = KdbStore(session)
base = datetime(2025, 6, 10, 14, 0, 0)

# Deliberately out of order: true open is 100 (at :05), true close is 101 (at :55).
ticks = pd.DataFrame({
    "time": [base + timedelta(seconds=55), base + timedelta(seconds=5),
             base + timedelta(seconds=30), base + timedelta(seconds=70)],
    "sym": ["TICKCHK"] * 4,
    "price": [101.0, 100.0, 103.0, 99.0],
    "size": [5.0, 10.0, 1.0, 7.0],
})
check("write_ticks", store.write_ticks(ticks) == 4)

bars = aggregate_ticks(store, "TICKCHK", "1m", base, base + timedelta(minutes=5))
check("two buckets", len(bars) == 2, f"got {len(bars)}")
if bars:
    first = bars[0]
    check("open is time-ordered, not insertion-ordered", first["open"] == 100.0,
          f"open={first['open']} (100.0 expected; 101.0 means the sort was dropped)")
    check("close is time-ordered", first["close"] == 101.0, f"close={first['close']}")
    check("high", first["high"] == 103.0)
    check("low", first["low"] == 100.0)
    check("volume", first["volume"] == 16.0)

span = store.tick_span("TICKCHK")
check("tick_span", span is not None and span[0] <= span[1])

store.prune_ticks(datetime(2030, 1, 1))
check("prune clears the window", store.tick_span("TICKCHK") is None)

session.close()
print()
print("FAILURES:", failures if failures else "none")
sys.exit(1 if failures else 0)
