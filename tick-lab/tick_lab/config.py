"""S3 connection settings, read from the same ARCTICDB_S3_* names the container uses.

Keeping one convention on both sides means `minio.env` is the single source of
truth: the laptop and the Platform container cannot drift apart.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import quote

REQUIRED = (
    "ARCTICDB_S3_ENDPOINT",
    "ARCTICDB_S3_BUCKET",
    "ARCTICDB_S3_ACCESS",
    "ARCTICDB_S3_SECRET",
)


class ConfigError(RuntimeError):
    """Raised when the environment cannot produce a usable connection."""


@dataclass(frozen=True)
class S3Config:
    endpoint: str
    bucket: str
    access: str
    secret: str
    port: int = 9000
    secure: bool = True

    def __repr__(self) -> str:
        # A traceback from a CLI lands in terminal scrollback and pasted bug
        # reports; the secret has no business being in either.
        return (
            f"S3Config(endpoint={self.endpoint!r}, bucket={self.bucket!r}, "
            f"access={self.access!r}, secret='***', port={self.port}, "
            f"secure={self.secure})"
        )

    @property
    def uri(self) -> str:
        """The ArcticDB connection URI.

        Host and bucket are separated by ':' and the port is a QUERY parameter.
        'host:port:bucket' looks plausible and is not valid ArcticDB syntax.
        """
        scheme = "s3s" if self.secure else "s3"
        return (
            f"{scheme}://{self.endpoint}:{self.bucket}"
            f"?port={self.port}"
            f"&access={quote(self.access, safe='')}"
            f"&secret={quote(self.secret, safe='')}"
            f"&use_virtual_addressing=false"
        )


def from_env(env: Mapping[str, str] | None = None) -> S3Config:
    """Build an S3Config from ARCTICDB_S3_* variables."""
    e = os.environ if env is None else env

    missing = [k for k in REQUIRED if not str(e.get(k, "")).strip()]
    if missing:
        raise ConfigError(
            "missing or empty required environment variable(s): "
            + ", ".join(missing)
            + " — copy tick-lab/.env.example to .env and fill it in"
        )

    port_raw = str(e.get("ARCTICDB_S3_PORT") or "9000").strip()
    if not port_raw.isdigit():
        raise ConfigError(f"ARCTICDB_S3_PORT must be a number, got {port_raw!r}")

    secure_raw = str(e.get("ARCTICDB_S3_SECURE") or "true").strip().lower()
    if secure_raw not in ("true", "false"):
        raise ConfigError(f"ARCTICDB_S3_SECURE must be 'true' or 'false', got {secure_raw!r}")

    return S3Config(
        endpoint=str(e["ARCTICDB_S3_ENDPOINT"]).strip(),
        bucket=str(e["ARCTICDB_S3_BUCKET"]).strip(),
        access=str(e["ARCTICDB_S3_ACCESS"]).strip(),
        secret=str(e["ARCTICDB_S3_SECRET"]).strip(),
        port=int(port_raw),
        secure=secure_raw == "true",
    )
