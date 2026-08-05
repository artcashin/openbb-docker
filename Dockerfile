# OpenBB Platform API + MCP server, containerized. Companion image for the
# Adventures in OpenBB series (v9.0.0).
#
# Scope (v9.0.0): the Platform REST API with OpenBB's standard providers,
# the analysis extensions, the official MCP server for AI agents, and the
# EODHD provider extension. No CLI/Terminal; other custom providers arrive
# with their episodes.

# --- kdb-x runtime (Ep. 10) -------------------------------------------------
# The q SERVER binary only. kc.lic is deliberately NOT shipped: this image is
# published, and a personal-edition license may not be redistributed. Readers
# mount their own at /opt/kx-license (see docker-compose.yml).
#
# The licence is deleted HERE, in the builder, before anything is copied out.
# Deleting it after the COPY in the final stage is not enough: layers are
# additive, so the COPY layer would still hold an intact kc.lic that anyone
# who pulls the image can extract with `docker save`, even though the
# flattened filesystem looks clean.
#
# Pinned to :1.0, not :latest. Two reasons, and the second one bites silently:
# :latest was a SINGLE-arch (arm64) manifest until 2026-08-05, so an amd64
# build resolved it anyway and copied AArch64 binaries into an x86_64 image --
# COPY never executes anything, so the build stayed green and only the q spawn
# failed at runtime. :1.0 is a multi-arch index (amd64 + arm64) and resolves
# per build platform. Ep. 11 pins platform: linux/amd64 (ArcticDB ships no
# aarch64 Linux wheels), which is exactly the case that broke.
FROM ghcr.io/artcashin/kdb-x:1.0 AS kdbx
# Every *.lic, not the one filename we happen to know: kdb+ also honours
# k4.lic and kx.lic, the COPY below is wholesale (`/root/.kx`, not a file
# list), and a base image is free to add another one on any rebuild. Then
# assert the delete actually emptied the tree -- an `rm` that matched nothing
# exits 0, so without this the whole guarantee rests on a glob nobody checks.
RUN find /root/.kx -type f -name '*.lic' -delete \
    && ! find /root/.kx -type f -name '*.lic' -print | grep -q . \
    && echo "kdbx builder stage: no *.lic remains"

FROM python:3.12-slim

# OpenBB version. Override with --build-arg to track a newer release.
ARG OPENBB_VERSION=4.7.2

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OPENBB_HOME=/root/.openbb_platform \
    # Applied to EVERY pip install below so nothing drags a shared lib past
    # what the stack tolerates. See extension-constraints.txt.
    PIP_CONSTRAINT=/tmp/extension-constraints.txt

COPY extension-constraints.txt /tmp/extension-constraints.txt

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --upgrade pip setuptools wheel

# OpenBB Platform + analysis extensions.
# NOTE: the `[all]` extra is intentionally NOT used; it pulls openbb-charting,
# whose GUI chart windows cannot render in a headless container. Chart-type
# widgets still work: endpoints return Plotly figure JSON and the *client*
# renders it (see Ep. 2).
RUN pip install \
        "openbb==${OPENBB_VERSION}" \
        "openbb-technical==1.6.2" \
        "openbb-quantitative==1.6.2" \
        "openbb-econometrics==1.7.2"

# Patch openbb_cftc.cftc_router.build_choices: upstream assumes name/code/
# subcategory are non-None and calls .strip() unconditionally. Some CFTC
# contracts have a NULL subcategory, which crashes the REST server at startup
# (the function is registered as a FastAPI startup event handler) and exits
# the whole API.
RUN python - <<'PY'
import re, pathlib
p = pathlib.Path("/usr/local/lib/python3.12/site-packages/openbb_cftc/cftc_router.py")
src = p.read_text()
for attr in ("name", "code", "subcategory"):
    src = re.sub(rf"d\.{attr}\.strip\(\)", f"(d.{attr} or '').strip()", src)
p.write_text(src)
PY
RUN python -c "import ast; ast.parse(open('/usr/local/lib/python3.12/site-packages/openbb_cftc/cftc_router.py').read()); print('cftc_router patch parses OK')"

# Patch openbb_core.api.rest_api CORS setup to answer Chrome's Private Network
# Access preflight. OpenBB Workspace runs at https://pro.openbb.co (a public
# origin); when the browser classifies your backend's address as private/local
# (see Ep. 2's gotchas), the fetch is gated behind an OPTIONS preflight carrying
# Access-Control-Request-Private-Network: true. Starlette rejects it unless
# allow_private_network=True, and OpenBB exposes no setting for this.
RUN python - <<'PY'
import pathlib
p = pathlib.Path("/usr/local/lib/python3.12/site-packages/openbb_core/api/rest_api.py")
src = p.read_text()
anchor = "    allow_headers=system.api_settings.cors.allow_headers,\n)"
assert anchor in src, "rest_api.py CORS block not found - upstream changed"
p.write_text(src.replace(anchor, anchor.replace("\n)", "\n    allow_private_network=True,\n)"), 1))
PY
RUN python -c "import ast; ast.parse(open('/usr/local/lib/python3.12/site-packages/openbb_core/api/rest_api.py').read()); print('rest_api CORS patch parses OK')"

# Custom EODHD provider extension (Ep. 9): equity/ETF/crypto/forex historical
# (EOD + intraday) and fundamentals via the official SDK, pinned to a GitHub
# commit (the PyPI release predates the SDK's typed errors and timeouts).
COPY openbb-eodhd/ /opt/openbb-eodhd/
RUN pip install /opt/openbb-eodhd

# q runtime, minus the license (already removed in the kdbx stage above, so
# nothing here can carry it).
COPY --from=kdbx /root/.kx /opt/kx

# Prove it, in the image that actually ships. The deletion above is in a stage
# whose layers are discarded, so this is the only place the published
# filesystem is checked -- and shipping a licence is a licensing violation,
# not a bug worth discovering after a `docker push`. Scanning the whole
# filesystem (not just /opt/kx) also catches a licence arriving from any other
# COPY. CI additionally checks that no *layer tar* carries one, which is the
# stronger property: a file deleted in a later layer is still extractable from
# an earlier one via `docker save`.
RUN found=$(find / -xdev -type f \( -name '*.lic' -o -name '*.license' \) -print) \
    && if [ -n "$found" ]; then \
         echo "REFUSING TO SHIP: licence file(s) in the image:" >&2; \
         echo "$found" >&2; exit 1; \
       fi \
    && echo "licence scan: no *.lic / *.license in the image filesystem"

# Shared kdb+ session/store plumbing (Ep. 10): openbb-kdb and live-grid both
# depend on the "kdb-store" distribution now, and it is not published to
# PyPI, so it must be installed from this checkout before openbb-kdb (whose
# own pyproject.toml lists it as a dependency) or pip has nothing to resolve
# "kdb-store" against.
COPY kdb-store /tmp/kdb-store
RUN pip install --no-cache-dir /tmp/kdb-store && rm -rf /tmp/kdb-store

# kdb read-through cache provider (Ep. 10).
COPY openbb-kdb /tmp/openbb-kdb
RUN pip install --no-cache-dir /tmp/openbb-kdb && rm -rf /tmp/openbb-kdb

# Official OpenBB MCP server (Ep. 6): wraps the Platform FastAPI app
# in-process and serves MCP over streamable-http. PIP_CONSTRAINT still
# applies, so it cannot drag shared libs anywhere the stack doesn't tolerate.
RUN pip install "openbb-mcp-server==1.4.1"
RUN python -c "import openbb_mcp_server; print('openbb-mcp-server import OK')"

# Pre-compile the static package so the first run is instant, and verify the
# platform registers at build time.
RUN python -c "import openbb; openbb.build(); from openbb import obb; \
assert 'eodhd' in obb.coverage.providers, 'eodhd provider not registered'; \
assert 'kdb' in obb.coverage.providers, 'kdb provider not registered'; \
print('OpenBB Platform OK:', len(obb.coverage.providers), 'providers (incl. eodhd, kdb)')"

WORKDIR /workspace

# Self-provision persistent mount points so the image is drop-in on any host
# (NAS container managers, plain Docker) with bind mounts to not-yet-created
# paths. OPENBB_HOME persists settings/credentials; /workspace holds user data.
ENV APP_DIRS="/root/.openbb_platform /workspace"
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

# Default: the REST API on loopback (the compose file's Tailscale sidecar is
# the only way in — see docker-compose.yml).
CMD ["openbb-api", "--host", "127.0.0.1", "--port", "6900"]
