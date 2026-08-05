#!/bin/sh
# Join the tailnet, obtain a certificate, serve S3 over TLS, keep the
# certificate fresh -- all in one container.
#
# tailscaled listens on the CLI's DEFAULT socket path, which is what lets
# cert-sync.sh call a bare `tailscale cert` with no --socket flag.
set -eu

CERT_DIR="${MINIO_CERT_DIR:-/root/.minio/certs}"
RENEW_SECONDS="${MINIO_CERT_RENEW_SECONDS:-43200}"
STATE_DIR="${TS_STATE_DIR:-/var/lib/tailscale}"
SOCKET=/var/run/tailscale/tailscaled.sock
NODE_NAME="${TS_HOSTNAME:-minio}"
PID_FILE=/run/minio.pid

# The certificate must be issued for this node's MagicDNS name, which is the
# same host ArcticDB clients connect to. Default from ARCTICDB_S3_ENDPOINT so
# minio.env stays the single source of truth: compose CANNOT interpolate an
# env_file value into the compose file, so this defaulting happens here.
DOMAIN="${MINIO_CERT_DOMAIN:-${ARCTICDB_S3_ENDPOINT:-}}"
if [ -z "$DOMAIN" ]; then
    echo "entrypoint: set ARCTICDB_S3_ENDPOINT (or MINIO_CERT_DOMAIN) in minio.env," \
         "e.g. minio.<your-tailnet>.ts.net" >&2
    exit 1
fi
if [ -z "${TS_AUTHKEY:-}" ]; then
    echo "entrypoint: TS_AUTHKEY must be set (it comes from ts.env)" >&2
    exit 1
fi

mkdir -p "$STATE_DIR" /var/run/tailscale "$CERT_DIR" /run

echo "entrypoint: starting tailscaled"
tailscaled --state="$STATE_DIR/tailscaled.state" --socket="$SOCKET" --tun=tailscale0 &
TAILSCALED_PID=$!

echo "entrypoint: waiting for tailscaled..."
i=0
until tailscale status --json >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -ge 60 ]; then
        echo "entrypoint: tailscaled did not come up within 60s" >&2
        exit 1
    fi
    sleep 1
done

# Idempotent: on a restart with persisted state this is a no-op re-auth.
echo "entrypoint: bringing up node $NODE_NAME"
tailscale up --authkey="$TS_AUTHKEY" --hostname="$NODE_NAME"

echo "entrypoint: obtaining certificate for $DOMAIN"
/usr/local/bin/cert-sync.sh "$CERT_DIR" "$DOMAIN" "$PID_FILE" || {
    echo "entrypoint: could not obtain a certificate; refusing to start plaintext" >&2
    exit 1
}

MINIO_PID=""
RENEW_PID=""
term() {
    [ -n "${MINIO_PID:-}" ] && kill -TERM "$MINIO_PID" 2>/dev/null || true
    [ -n "${MINIO_PID:-}" ] && wait "$MINIO_PID" 2>/dev/null || true
    [ -n "${RENEW_PID:-}" ] && kill -TERM "$RENEW_PID" 2>/dev/null || true
    kill -TERM "$TAILSCALED_PID" 2>/dev/null || true
    exit 0
}
trap term TERM INT

minio server --certs-dir "$CERT_DIR" "$@" &
MINIO_PID=$!
echo "$MINIO_PID" > "$PID_FILE"

(
    while true; do
        sleep "$RENEW_SECONDS"
        /usr/local/bin/cert-sync.sh "$CERT_DIR" "$DOMAIN" "$PID_FILE" || true
    done
) &
RENEW_PID=$!

wait "$MINIO_PID"
