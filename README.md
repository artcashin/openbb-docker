# OpenBB in Docker

Containerized OpenBB Platform (all extensions) + interactive CLI / Terminal.
Pinned to the locally-built versions: **openbb 4.6.0**, **openbb-cli 1.3.0**.

## Build

```bash
cd ~/Developer/openbb-docker
docker compose build          # or: docker build -t openbb-local:4.6.0 .
```

## Quick start (`./obb-up`)

A small wrapper for the common operations:

```bash
./obb-up            # interactive OpenBB Terminal
./obb-up s3         # start the shared ArcticDB store (MinIO) + create bucket
./obb-up api        # REST API server -> http://localhost:6900/docs
./obb-up shell      # bash shell inside the container
./obb-up status     # show stack containers
./obb-up down       # stop everything (data volumes kept)
```

Inspect the ArcticDB store from the host (no Python needed):

```bash
./obb-arctic ls [library]                # list stored symbols
./obb-arctic read <symbol> [library]     # print a stored table  (--csv for CSV)
./obb-arctic meta <symbol> [library]     # show stored metadata
./obb-arctic rm   <symbol> [library]     # delete a symbol
```

The sections below show the underlying `docker compose` commands.

## Run the interactive Terminal

```bash
docker compose run --rm openbb           # drops into the OpenBB Terminal
# or, without compose:
docker run -it --rm -v openbb-data:/root/.openbb_platform openbb-local:4.6.0
```

The `-it` / `tty` is required — the Terminal is interactive.

## Python API instead of the Terminal

```bash
docker compose run --rm openbb python
>>> from openbb import obb
>>> obb.equity.price.historical("AAPL", provider="yfinance").to_df()
```

## REST API server

```bash
docker compose --profile api up          # serves on http://localhost:6900
```

## Split setup: client on host, data engine in the container

The OpenBB CLI Terminal is monolithic — it runs the platform in-process, so it
can't keep the REPL native while offloading only data calls. To put the *engine*
(all data fetching + compute) in the container and a thin *client* on the host,
use the REST API instead:

1. Start the engine in the container:
   ```bash
   docker compose --profile api up        # 279 endpoints on http://localhost:6900
   ```
2. Drive it from the host with the bundled dependency-free client (`./obb`):
   ```bash
   ./obb --list                                   # list endpoints
   ./obb --list equity                            # filter
   ./obb equity/price/historical symbol=AAPL provider=yfinance
   ./obb --open                                   # open Swagger docs in browser
   ```
   The client uses only the Python standard library — nothing to install on the
   host. Point it elsewhere with `OBB_API=http://host:port ./obb ...`.

Other host clients that work the same way: `curl http://localhost:6900/api/v1/...`,
the interactive docs at <http://localhost:6900/docs>, or OpenBB Workspace
(add a custom backend pointing at `http://localhost:6900`).

### Alternative: containerized Terminal, host TTY

If you want the actual OpenBB **Terminal UI** (not the API) with all compute in
the container, just run `docker compose run --rm openbb`. Your Mac only renders
the terminal; every data call already executes inside the container.

## Custom integrations baked into the image

- **Alpaca** (`openbb-alpaca/`) — `provider="alpaca"` for equity/ETF historical
  pricing (needs `ALPACA_API_KEY` / `ALPACA_API_SECRET`).
- **ArcticDB** (`openbb-arcticdb/`) — store any result and serve it back:
  ```python
  res = obb.equity.price.historical("AAPL", provider="yfinance")
  res.arcticdb.write("AAPL")                               # persist
  obb.equity.price.historical("AAPL", provider="arcticdb") # read back, offline
  ```
  Stores to a local LMDB DB on the `openbb-data` volume by default (persists
  across runs); override with `ARCTICDB_URI` / `ARCTICDB_LIBRARY`. See
  `openbb-arcticdb/README.md`.

## Shared ArcticDB store (MinIO) — single box or split across machines

ArcticDB is serverless (no DB server to connect to); to share a store across
machines you run an S3-compatible object store. A MinIO service is bundled under
the `s3` profile.

**Same machine (OpenBB + MinIO together):**

```bash
docker compose --profile s3 up -d           # starts minio + creates the bucket
# in credentials.env, uncomment the S3 block:
#   ARCTICDB_URI=s3://minio:openbb?port=9000&access=minioadmin&secret=minioadmin&use_virtual_addressing=false
#   ARCTICDB_LIBRARY=prices
docker compose run --rm openbb              # now reads/writes the shared store
```

MinIO console: http://localhost:9001 (minioadmin / minioadmin). Data persists in
the `minio-data` volume.

**Split: MinIO on another machine.** Run only the store on machine B and point
OpenBB (machine A) at it — no code or image changes, just the URI:

1. On **machine B**, run MinIO (this repo's `minio` + `minio-setup` services, or
   any S3/MinIO) and expose port 9000.
2. On **machine A**, set in `credentials.env` (note the URI grammar: the host has
   **no colon** — the port is a separate `port=` param):
   ```
   ARCTICDB_URI=s3://<MACHINE_B_IP>:openbb?port=9000&access=<KEY>&secret=<SECRET>&use_virtual_addressing=false
   ARCTICDB_LIBRARY=prices
   ```
   Use `s3s://` instead of `s3://` if MinIO serves HTTPS. The bucket must exist
   (the `minio-setup` service creates it).

The write accessor and the `provider="arcticdb"` read path both route to whatever
`ARCTICDB_URI` points at — verified end-to-end against MinIO.

## Provider API keys

Keys are injected into the container as environment variables via Compose.

```bash
cp credentials.env.example credentials.env   # then edit credentials.env
```

`credentials.env` is git-ignored and wired into both services with
`env_file: required: false`, so it's optional — keyless providers (yfinance,
sec, federal_reserve, oecd, imf, ecb, finviz, ...) work without it.

OpenBB reads **bare UPPERCASE** variable names directly into its credentials —
e.g. `FMP_API_KEY`, `FRED_API_KEY`, `TIINGO_TOKEN`, `INTRINIO_API_KEY`. No
`OPENBB_` prefix. Empty values are ignored, so only fill in what you use. See
`credentials.env.example` for the full list of supported keys + signup links.

Anything set this way appears as `obb.user.credentials.<name>` inside the
container and is used automatically by that provider.

## Persistence

User settings (and any credentials saved from inside the Terminal via
`/settings`) live in the `openbb-data` named volume (`/root/.openbb_platform`),
so they survive `--rm` runs. Keys from `credentials.env` are applied fresh on
each run and take precedence for the providers they cover.

## Notes

- Charting (`openbb-charting` / pywry) is **excluded on purpose**: pywry is a
  Rust GUI-window backend with no Linux container wheel, and it can't render
  headless anyway. Data retrieval, the analysis extensions (technical,
  quantitative, econometrics), the Terminal, and the REST API all work normally.
  For charts, use the locally-built `~/Developer/OpenBB` install instead.
- Image is built for the host architecture (arm64 on Apple Silicon).
