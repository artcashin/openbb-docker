"""Live check: real q, real PyKX, real store. Requires a license; not in CI.

Run inside the container:
    docker compose run --rm openbb python /tmp/live_check.py
"""

import sys
from datetime import datetime, timedelta

import pandas as pd

from openbb_kdb.config import resolve_config
from openbb_kdb.session import KdbSession
from openbb_kdb.store import KdbStore

failures = []


def check(name, condition, detail=""):
    print(f"{'PASS' if condition else 'FAIL'}  {name} {detail}")
    if not condition:
        failures.append(name)


config = resolve_config()
session = KdbSession(config)
conn = session.connection()
store = KdbStore(session)

check("q answers", conn("1+1").py() == 2)

mem = store.memory()
check("memory keys are str", "heap" in mem and "wmax" in mem, str(mem)[:120])
check(
    "workspace is the configured headroom",
    mem["wmax"] == config.q_workspace_mb * 1024 * 1024,
    f"wmax={mem['wmax']}",
)

# Bars round-trip through the real PyKX conversions.
now = datetime(2025, 1, 10)
frame = pd.DataFrame({
    "t": [now - timedelta(days=i) for i in range(5)],
    "open": [1.0] * 5, "high": [2.0] * 5, "low": [0.5] * 5,
    "close": [1.5] * 5, "volume": [100] * 5,
})
store.write_bars("LIVECHK", "1d", frame)
back = store.read_bars("LIVECHK", "1d", now - timedelta(days=2), now)
check("bars round-trip", len(back) == 3, f"got {len(back)} rows")

# Writing twice must not duplicate rows.
store.write_bars("LIVECHK", "1d", frame)
back2 = store.read_bars("LIVECHK", "1d", now - timedelta(days=10), now)
check("upsert does not duplicate", len(back2) == 5, f"got {len(back2)} rows")

# Coverage round-trip (real timestamp conversion).
store.record_coverage("LIVECHK", "1d", (now - timedelta(days=5), now))
cov = store.read_coverage("LIVECHK", "1d")
check("coverage round-trip", len(cov) == 1, str(cov)[:120])

# Eviction genuinely returns heap -- delete alone does not.
before = store.memory()["heap"]
conn("ballast: 4000000 # 1.0")
grown = store.memory()["heap"]
conn("delete ballast from `.")
conn(".Q.gc[]")
after = store.memory()["heap"]
check("gc reclaims heap", after < grown, f"{grown} -> {after}")

store.drop("LIVECHK", "1d")
check("drop clears coverage", store.read_coverage("LIVECHK", "1d") == [])

# One shared connection used from several threads (spec risk 3).
import threading

results = {}


def worker(tag):
    try:
        results[tag] = conn(f"{tag}+1").py()
    except Exception as exc:  # noqa: BLE001
        results[tag] = f"ERR {type(exc).__name__}"


threads = [threading.Thread(target=worker, args=(i,)) for i in range(1, 4)]
[t.start() for t in threads]
[t.join() for t in threads]
ok = all(results.get(i) == i + 1 for i in range(1, 4))
check("shared connection across threads", ok, str(results))
if not ok:
    print("  -> serialize q access behind one lock, or open a connection per thread")

session.close()
print()
print("FAILURES:", failures if failures else "none")
sys.exit(1 if failures else 0)
