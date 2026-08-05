"""Connection and cache configuration.

Precedence: OpenBB credential > environment variable > default.
"""

import os
from dataclasses import dataclass

_DEFAULTS = {
    "host": "127.0.0.1",
    "port": 5000,
    "memory_mb": 8192,
    "watermark": 0.75,
    "upstream": "eodhd",
    "qhome": "/opt/kx",
}

# q is given headroom above the cache budget. Crossing -w kills the process
# outright (no catchable 'wsfull), so -w is containment that protects the rest
# of the container -- the real budget is enforced by eviction well below it.
_WORKSPACE_HEADROOM = 1.25


@dataclass(frozen=True)
class KdbConfig:
    """Resolved kdb+ settings."""

    host: str
    port: int
    embedded: bool
    memory_mb: int
    watermark: float
    upstream: str
    qhome: str

    @property
    def q_workspace_mb(self) -> int:
        """The `-w` value: the cache budget plus containment headroom."""
        return int(self.memory_mb * _WORKSPACE_HEADROOM)


def _pick(key: str, env: str, credentials: dict | None):
    creds = credentials or {}
    if creds.get(f"kdb_{key}"):
        return creds[f"kdb_{key}"]
    if os.getenv(env):
        return os.getenv(env)
    return None


def resolve_config(credentials: dict | None = None) -> KdbConfig:
    """Resolve configuration from credentials, environment, then defaults."""
    host = _pick("host", "KDB_HOST", credentials) or _DEFAULTS["host"]

    raw_port = _pick("port", "KDB_PORT", credentials) or _DEFAULTS["port"]
    try:
        port = int(raw_port)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid kdb+ port: {raw_port!r}. Must be an integer.") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"kdb+ port {port} out of range (1-65535).")

    raw_embedded = _pick("embedded", "KDB_EMBEDDED", credentials)
    if raw_embedded is None:
        # Spawning only makes sense for a q we own -- a remote host is the
        # user's own server.
        embedded = host in ("127.0.0.1", "localhost")
    else:
        embedded = str(raw_embedded).strip().lower() in ("1", "true", "yes", "on")

    raw_mem = _pick("memory_mb", "KDB_MEMORY_MB", credentials) or _DEFAULTS["memory_mb"]
    try:
        memory_mb = int(raw_mem)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid KDB_MEMORY_MB: {raw_mem!r}.") from exc
    if memory_mb < 64:
        raise ValueError(f"KDB_MEMORY_MB {memory_mb} is too small (minimum 64).")

    raw_wm = _pick("cache_watermark", "KDB_CACHE_WATERMARK", credentials) or _DEFAULTS["watermark"]
    try:
        watermark = float(raw_wm)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid KDB_CACHE_WATERMARK: {raw_wm!r}.") from exc
    if not 0.1 <= watermark <= 0.95:
        raise ValueError(f"KDB_CACHE_WATERMARK {watermark} out of range (0.1-0.95).")

    upstream = _pick("upstream", "KDB_UPSTREAM", credentials) or _DEFAULTS["upstream"]
    qhome = os.getenv("QHOME") or _DEFAULTS["qhome"]

    return KdbConfig(
        host=host, port=port, embedded=embedded, memory_mb=memory_mb,
        watermark=watermark, upstream=str(upstream), qhome=qhome,
    )
