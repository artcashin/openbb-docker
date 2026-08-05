# MinIO with a Tailscale-issued certificate (Ep. 11).
#
# The tailscale CLI is copied in so the renewal loop can run in THIS container
# and signal MinIO directly -- see entrypoint.sh for why that matters.
FROM tailscale/tailscale:v1.98.9 AS ts

FROM quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z

COPY --from=ts /usr/local/bin/tailscale /usr/local/bin/tailscale
COPY cert-sync.sh /usr/local/bin/cert-sync.sh
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/cert-sync.sh /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["/data"]
