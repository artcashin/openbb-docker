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
    """Resolve one setting: credential > env var > absent (``None``).

    Presence must be judged by containment/identity, not truthiness --
    otherwise a legitimately falsy credential (``False``, ``0``, ``0.0``)
    reads as "not set" and silently falls through to the env var or
    default, which is exactly the bug this function exists to avoid.
    So a credential counts as present if the key exists and its value is
    not ``None``, regardless of whether that value is falsy.

    Environment variables are different: they're always strings, and a
    shell/compose env file has no way to represent "unset" other than an
    empty string. So ``KDB_HOST=""`` is treated as absent, not as an
    explicit empty value -- there's no falsy-but-meaningful env value to
    protect the way there is for credentials.
    """
    creds = credentials or {}
    cred_key = f"kdb_{key}"
    if cred_key in creds and creds[cred_key] is not None:
        return creds[cred_key]
    env_val = os.getenv(env)
    if env_val:
        return env_val
    return None


def resolve_config(credentials: dict | None = None) -> KdbConfig:
    """Resolve configuration from credentials, environment, then defaults."""
    host = _pick("host", "KDB_HOST", credentials)
    if host is None:
        host = _DEFAULTS["host"]

    raw_port = _pick("port", "KDB_PORT", credentials)
    if raw_port is None:
        raw_port = _DEFAULTS["port"]
    try:
        port = int(raw_port)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid kdb+ port: {raw_port!r}. Must be an integer.") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"KDB_PORT {port} out of range (1-65535).")

    raw_embedded = _pick("embedded", "KDB_EMBEDDED", credentials)
    if raw_embedded is None:
        # Spawning only makes sense for a q we own -- a remote host is the
        # user's own server.
        embedded = host in ("127.0.0.1", "localhost", "::1")
    else:
        embedded = str(raw_embedded).strip().lower() in ("1", "true", "yes", "on")

    raw_mem = _pick("memory_mb", "KDB_MEMORY_MB", credentials)
    if raw_mem is None:
        raw_mem = _DEFAULTS["memory_mb"]
    try:
        memory_mb = int(raw_mem)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid KDB_MEMORY_MB: {raw_mem!r}.") from exc
    if memory_mb < 64:
        raise ValueError(f"KDB_MEMORY_MB {memory_mb} is too small (minimum 64).")

    raw_wm = _pick("cache_watermark", "KDB_CACHE_WATERMARK", credentials)
    if raw_wm is None:
        raw_wm = _DEFAULTS["watermark"]
    try:
        watermark = float(raw_wm)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid KDB_CACHE_WATERMARK: {raw_wm!r}.") from exc
    if not 0.1 <= watermark <= 0.95:
        raise ValueError(f"KDB_CACHE_WATERMARK {watermark} out of range (0.1-0.95).")

    upstream = _pick("upstream", "KDB_UPSTREAM", credentials)
    if upstream is None:
        upstream = _DEFAULTS["upstream"]
    qhome = os.getenv("QHOME") or _DEFAULTS["qhome"]

    return KdbConfig(
        host=host, port=port, embedded=embedded, memory_mb=memory_mb,
        watermark=watermark, upstream=str(upstream), qhome=qhome,
    )
