# tick-lab

A local CLI that reads ticks out of the shared ArcticDB store, rolls them
into 1-minute OHLCV bars, and checks those bars against an independent
source. Companion code for *Adventures in OpenBB, Ep. 11*.

It runs on your own machine and never enters a container. By default it
never imports `openbb` either — the point of this chapter is that the store
is a shared network service usable from any Python process, and `tick-lab`
is that process. The one exception is `--reference eodhd-local` (added in
v11.1.1): opt into it and `openbb` runs in-process on your laptop, on
purpose, as a teaching contrast — see
["Three ways to ask the same question"](#three-ways-to-ask-the-same-question)
below.

```bash
tick-lab load ./FirstRate_sample.zip
tick-lab compare --symbol MSFT --date 2023-05-12
```

## Install

```bash
cd tick-lab
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

Requires Python 3.10+. `arcticdb` is a native-extension package; on Apple
Silicon this resolves a macOS `arm64` wheel natively (unlike the Docker image
this store also feeds — see [`docs/arcticdb-minio-design.md`](../docs/arcticdb-minio-design.md#the-amd64-pin-and-what-it-means-on-apple-silicon)
for why that one runs emulated).

## Configure

```bash
cp .env.example .env
```

The values are the **same names, same values** as `../minio.env` — copy
them across, don't invent your own:

```
ARCTICDB_S3_ENDPOINT=minio.<your-tailnet>.ts.net
ARCTICDB_S3_PORT=9000
ARCTICDB_S3_BUCKET=openbb
ARCTICDB_S3_SECURE=true
ARCTICDB_S3_ACCESS=      # = MINIO_ROOT_USER in minio.env
ARCTICDB_S3_SECRET=      # = MINIO_ROOT_PASSWORD in minio.env
```

One `minio.env` feeds both the container and this CLI, so they can never
drift onto different buckets or credentials. See
[`docs/arcticdb-minio-design.md`](../docs/arcticdb-minio-design.md#arcticdb_s3--one-file-two-consumers)
for why the two sides share a naming convention instead of each inventing
their own.

`.env` is git-ignored. `chmod 600` it, same as every other credential file
in this repo.

## Get the sample data

The comparison needs real tick data to be worth doing. Grab FirstRate Data's
free sample from <https://firstratedata.com/tick-data> — GOOG and MSFT,
trades and quotes, 2023-05-12, US Eastern timestamps.

**It is not committed to this repository.** It's third-party licensed data;
download it yourself and keep the zip wherever you like outside the repo (or
git-ignore it if you unzip it inside `tick-lab/`).

## Load it

```bash
tick-lab load ./FirstRate_sample.zip --dry-run   # reports what would be written, writes nothing
tick-lab load ./FirstRate_sample.zip              # writes for real
```

`load` also accepts a path to an already-extracted directory, not just a
zip. Trade files land in the `ticks` library, quote files in `quotes`, each
keyed by symbol (`MSFT`, `GOOG`). A write is an **overwrite**, so re-running
`load` on the same file is idempotent — there's no "already loaded" state to
manage.

Per-file failures (an unparseable file, a filename that doesn't say what
kind it is, a write ArcticDB rejects) are reported to stderr and the run
continues; the exit code is non-zero only if something failed.

## Compare

```bash
tick-lab compare --symbol MSFT --date 2023-05-12
```

This reads your stored MSFT ticks for that day, rolls them into 1-minute
bars, asks a reference source for its own 1-minute bars over the same
window, and reports where the two disagree. `--reference` defaults to
`eodhd-api`; `yfinance` and `eodhd-local` are also available — see
["Three ways to ask the same question"](#three-ways-to-ask-the-same-question)
for what each one costs.

### Expected output

`yfinance` cannot serve 1-minute bars for a date in 2023 — Yahoo's own
retention limit is roughly the last 30 days — so a `compare` against
`2023-05-12` **cannot** produce a clean per-minute report today. That's not
a bug in `tick-lab`; it's the correct, informative failure, and it's the
output worth showing a reader before the real thing:

Illustrative shape (tick and bar counts depend on your sample; the `1m`
retention line is quoted from a real yfinance response):

```
rolled <N> ticks into <M> 1-minute bars (session=regular)
asking yfinance for 1m bars...
  1m: retention — $MSFT: possibly delisted; no price data found (Yahoo error =
      "1m data not available for startTime=... and endTime=.... The requested
      range must be within the last 30 days.")
  falling back to 1d
```

`tick-lab` prints Yahoo's own explanation rather than swallowing it into a
generic "no data" message, then walks the interval ladder down
(`1m → 5m → 15m → 30m → 1h → 1d`) until it finds one `yfinance` can actually
serve for a window this old, and says which one it picked. The report that
follows still compares real numbers — just at daily resolution instead of
per-minute. `--reference` is already a flag for exactly this reason: swapping
in a source with real 2023 intraday history later is meant to be a small
change, not a rewrite.

### `--session`

```bash
tick-lab compare --symbol MSFT --date 2023-05-12 --session all
```

`--session regular` (the default) keeps only trades between **09:30
inclusive and 16:00 exclusive, US Eastern** — 390 one-minute bars in a full
session. This is the single biggest lever on how many discrepancies you'll
see, and it's a real gotcha, not a style choice: **FirstRate's tick data
includes extended-hours trades, and consolidated reference feeds usually
don't.** Compare with `--session all` and pre-market/after-hours prints that
`yfinance` never saw will show up as one-sided "coverage gaps" — bars on
your side with nothing to compare against — not price discrepancies. If your
gap count looks unexpectedly large, check which session you asked for before
suspecting the data.

### `--tol`

```bash
tick-lab compare --symbol MSFT --date 2023-05-12 --tol 0.05
```

`--tol` (default `0.01`) is in the **instrument's own price units** — for a
USD equity, that's one cent. A field only counts as a discrepancy once
`abs(ours - theirs)` exceeds this, per OHLCV field, per bar. The report
separately names the single largest **close** discrepancy, in both absolute
terms and basis points, with its timestamp — the one number worth looking at
first if something looks off.

### `--json`

```bash
tick-lab compare --symbol MSFT --date 2023-05-12 --json
```

Emits the same report as structured JSON instead of the text summary above —
`bars_compared`, `price_discrepancies` (one entry per field per bar over
tolerance), `non_finite_comparisons`, `coverage_gaps` (kept separate from
price discrepancies — a bar only one side has isn't a pricing disagreement),
`largest_close_discrepancy`, and `notes` (records things like "stepped down
to 1d because 1m wasn't servable").

### `--end`

`--end` extends the window past `--date` (`--date 2023-05-12 --end
2023-05-15`); it defaults to `--date` itself, i.e. a single day.

## Three ways to ask the same question

`--reference` picks how `compare` gets its independent 1-minute bars. All
three answer the same question — "what does an outside source say happened
in this window?" — but they pay for the answer differently:

| `--reference` | Runs where | Needs on your laptop | 2023 intraday? |
|---|---|---|---|
| `yfinance` (default before v11.1.0) | your laptop, hits Yahoo directly | nothing but `yfinance` (already a dependency) | no — Yahoo's own 1-minute retention is ~30 days, so a 2023 window steps down to `1d` |
| `eodhd-api` (**the default**) | your laptop, hits *this stack's* OpenBB REST API | just `OPENBB_URL` + the Basic-auth pair — no provider credential, no `openbb` install | yes — the EODHD key lives on the server |
| `eodhd-local` | your laptop, runs OpenBB **in-process** | `pip install openbb openbb-eodhd`, *and* an EODHD key present locally (e.g. `EODHD_API_KEY`) | yes, for symbols the key covers |

`eodhd-local` exists for the contrast, not because it's the recommended
path: it makes the same request as `eodhd-api`, but locally, so you can see
both side by side. It needs `openbb` installed here **and** the provider key
present here — on your own machine — which is exactly what routing through
this stack's own REST API (`eodhd-api`) spares you. That's the payoff of
running the stack at all: one Basic-auth pair replaces an installed SDK plus
a credential on every laptop that wants to ask.

```bash
pip install openbb openbb-eodhd   # not a tick-lab dependency -- opt in only if you use this adapter
EODHD_API_KEY=<your key> tick-lab compare --symbol MSFT --date 2023-05-12 --reference eodhd-local
```

### The GOOG entitlement example

The EODHD demo key committed in this repo's `credentials.env.example`
(shared with the Docker stack's `openbb-eodhd` extension) covers `MSFT` but
**not** `GOOG`. Verified directly against the live EODHD API: an MSFT
1-minute intraday request for 2023-05-12 returns real bars (HTTP 200); the
identical request for GOOG returns HTTP 403 Forbidden. Asking for GOOG
through `eodhd-local` is expected to fail with a *specific, actionable*
message — an `entitlement` error naming the 403 — rather than a silent
empty frame:

```bash
EODHD_API_KEY=<demo key> tick-lab compare --symbol GOOG --date 2023-05-12 --reference eodhd-local
```

```
asking eodhd-local for 1m bars...
  1m: entitlement — GOOG at 1m: 403 Forbidden — this API key's plan does not
      cover the request (...)

eodhd-local cannot serve this window: entitlement
  GOOG at 1m: 403 Forbidden — this API key's plan does not cover the request (...)
```

**This exact transcript was not captured from a live run.** The 403 itself
was verified directly against the EODHD API (real HTTP 200 for MSFT, real
HTTP 403 for GOOG); the message shape above is what `eodhd_local.py`'s
`classify_exception` produces from that failure, exercised in
`tests/test_eodhd_local.py`. Running it live also needs `openbb` +
`openbb-eodhd` installed — a heavier ask than the rest of this CLI's
dependencies — which could not be added to this development environment (the
latest `openbb` requires Python `<3.13`; this venv runs 3.13). If your own
`.venv` is on an older Python, `pip install openbb openbb-eodhd` and the two
commands above are the manual check to run and confirm.

## Running the tests

```bash
cd tick-lab
.venv/bin/pytest -q
```

Everything except the ArcticDB round-trip tests runs with **no network and
no live store** — `firstrate.py` parsing, the roll-up arithmetic (session
edges, DST, tolerance/bps math), the `yfinance` adapter's failure
classification, and the CLI all run against fixtures and monkeypatched
seams.

`tests/test_store.py`'s round-trip tests are the exception: they need a real
S3-compatible store and are skipped unless `TICK_LAB_TEST_S3=1`. Point them
at a disposable MinIO rather than your real `minio.env` store — the tests
write and delete throwaway libraries, and there's no reason to risk your
actual bucket:

```bash
docker run -d --name ticklab-minio -p 19000:9000 \
  -e MINIO_ROOT_USER=testuser -e MINIO_ROOT_PASSWORD=testpassword123 \
  quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z server /data
sleep 8

.venv/bin/python - <<'PY'
import boto3
from botocore.client import Config
s3 = boto3.client("s3", endpoint_url="http://127.0.0.1:19000",
                  aws_access_key_id="testuser", aws_secret_access_key="testpassword123",
                  config=Config(signature_version="s3v4"), region_name="us-east-1")
s3.create_bucket(Bucket="openbb")
PY

TICK_LAB_TEST_S3=1 ARCTICDB_S3_ENDPOINT=127.0.0.1 ARCTICDB_S3_PORT=19000 \
  ARCTICDB_S3_BUCKET=openbb ARCTICDB_S3_SECURE=false \
  ARCTICDB_S3_ACCESS=testuser ARCTICDB_S3_SECRET=testpassword123 \
  .venv/bin/pytest tests/test_store.py -q

docker rm -f ticklab-minio
```

`boto3` is a test-only convenience for creating the throwaway bucket (MinIO
doesn't let ArcticDB create one itself); install it into `.venv` if it's
missing.

There's also a provider-parity check one level up, in `tests/integration/`
at the repo root — it runs `provider="arcticdb"` **inside** the Platform
image and asserts it returns the exact same bars `tick-lab` computes by
hand, both pinned against one committed golden CSV
(`tests/fixtures/golden_1m_bars.csv`). See
[`tests/integration/README.md`](../tests/integration/README.md) for how to
run it against your real stack.
