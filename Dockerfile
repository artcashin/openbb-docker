# OpenBB Platform API, containerized. Companion image for
# "Adventures in OpenBB, Ep. 1: Your Own Bloomberg in a Closet" (v1.0.0).
#
# Scope (v1.0.0): the Platform REST API with OpenBB's standard providers and
# the analysis extensions. No CLI/Terminal, no custom providers, no MCP —
# those arrive in later releases, with their episodes.
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

# Pre-compile the static package so the first run is instant, and verify the
# platform registers at build time.
RUN python -c "import openbb; openbb.build(); from openbb import obb; \
print('OpenBB Platform OK:', len(obb.coverage.providers), 'providers')"

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
