#!/bin/sh
# Fetch (or renew) this node's Let's Encrypt certificate and hand it to MinIO.
#
# MinIO does NOT notice a rewritten certificate on disk, but it DOES reload on
# SIGHUP, with no restart and no dropped connections. So: write to a staging
# path, compare, and only promote + signal when the content actually changed.
#
# usage: cert-sync.sh <cert-dir> <domain> <pid-file>
set -eu

CERT_DIR="${1:?usage: cert-sync.sh <cert-dir> <domain> <pid-file>}"
DOMAIN="${2:?missing domain}"
PID_FILE="${3:?missing pid file}"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

# Never overwrite a working certificate with a failed renewal: write to the
# staging directory first and promote only on success.
if ! tailscale cert --cert-file "$STAGE/public.crt" --key-file "$STAGE/private.key" "$DOMAIN"; then
    echo "cert-sync: tailscale cert failed for $DOMAIN; keeping existing certificate" >&2
    exit 1
fi

if [ -f "$CERT_DIR/public.crt" ] && cmp -s "$STAGE/public.crt" "$CERT_DIR/public.crt"; then
    exit 0
fi

had_cert=0
[ -f "$CERT_DIR/public.crt" ] && had_cert=1

cp "$STAGE/public.crt" "$CERT_DIR/public.crt"
cp "$STAGE/private.key" "$CERT_DIR/private.key"
chmod 600 "$CERT_DIR/private.key"
chmod 644 "$CERT_DIR/public.crt"

# On the very first write MinIO has not started yet (or is starting with this
# cert), so there is nothing to reload.
if [ "$had_cert" -eq 1 ] && [ -f "$PID_FILE" ]; then
    pid="$(cat "$PID_FILE")"
    if kill -0 "$pid" 2>/dev/null; then
        echo "cert-sync: certificate changed, reloading MinIO (pid $pid)"
        kill -HUP "$pid"
    fi
fi
exit 0
