#!/bin/sh
# Start MinIO with a Tailscale-issued certificate, and keep that certificate
# fresh for the life of the container.
#
# The renewal loop lives HERE, in the same container as MinIO, because a
# sibling container could only signal MinIO through the Docker socket -- and
# mounting the Docker socket into a network-facing service is exactly the kind
# of hole the rest of this stack goes out of its way to avoid.
set -eu

CERT_DIR="${MINIO_CERT_DIR:-/root/.minio/certs}"
RENEW_SECONDS="${MINIO_CERT_RENEW_SECONDS:-43200}"
PID_FILE=/run/minio.pid

# The certificate must be issued for this node's MagicDNS name, which is the
# same host ArcticDB clients connect to. Default to ARCTICDB_S3_ENDPOINT so
# minio.env stays the single source of truth: compose CANNOT interpolate an
# env_file value into the compose file, so this defaulting has to happen here.
DOMAIN="${MINIO_CERT_DOMAIN:-${ARCTICDB_S3_ENDPOINT:-}}"
if [ -z "$DOMAIN" ]; then
    echo "entrypoint: set ARCTICDB_S3_ENDPOINT (or MINIO_CERT_DOMAIN) in minio.env," \
         "e.g. minio.<your-tailnet>.ts.net" >&2
    exit 1
fi

mkdir -p "$CERT_DIR" /run

# tailscaled shares this container's network namespace via the sidecar, but the
# socket appears only once the sidecar is up and the node has a name.
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

echo "entrypoint: obtaining certificate for $DOMAIN"
/usr/local/bin/cert-sync.sh "$CERT_DIR" "$DOMAIN" "$PID_FILE" || {
    echo "entrypoint: could not obtain a certificate; refusing to start plaintext" >&2
    exit 1
}

minio server --certs-dir "$CERT_DIR" "$@" &
MINIO_PID=$!
echo "$MINIO_PID" > "$PID_FILE"

term() {
    kill -TERM "$MINIO_PID" 2>/dev/null || true
    wait "$MINIO_PID" 2>/dev/null || true
    exit 0
}
trap term TERM INT

(
    while true; do
        sleep "$RENEW_SECONDS"
        /usr/local/bin/cert-sync.sh "$CERT_DIR" "$DOMAIN" "$PID_FILE" || true
    done
) &

wait "$MINIO_PID"
