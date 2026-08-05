"""Config resolution: env vars and OpenBB credentials into one frozen object."""

import pytest

from openbb_kdb.config import resolve_config


def test_defaults(monkeypatch):
    for var in ("KDB_HOST", "KDB_PORT", "KDB_EMBEDDED", "KDB_MEMORY_MB",
                "KDB_CACHE_WATERMARK", "KDB_UPSTREAM"):
        monkeypatch.delenv(var, raising=False)
    cfg = resolve_config()
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 5000
    assert cfg.embedded is True
    assert cfg.memory_mb == 8192
    assert cfg.watermark == 0.75
    assert cfg.upstream == "eodhd"


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("KDB_HOST", "kdb.internal")
    monkeypatch.setenv("KDB_PORT", "5010")
    monkeypatch.setenv("KDB_UPSTREAM", "yfinance")
    cfg = resolve_config()
    assert (cfg.host, cfg.port, cfg.upstream) == ("kdb.internal", 5010, "yfinance")


def test_credentials_beat_env(monkeypatch):
    monkeypatch.setenv("KDB_UPSTREAM", "yfinance")
    cfg = resolve_config({"kdb_upstream": "fmp"})
    assert cfg.upstream == "fmp"


def test_pointing_at_an_external_server_disables_spawning(monkeypatch):
    """A non-loopback host means the user brought their own kdb+."""
    monkeypatch.setenv("KDB_HOST", "kdb.internal")
    assert resolve_config().embedded is False


def test_explicit_embedded_false(monkeypatch):
    monkeypatch.setenv("KDB_EMBEDDED", "false")
    assert resolve_config().embedded is False


def test_workspace_is_25_percent_above_budget(monkeypatch):
    monkeypatch.setenv("KDB_MEMORY_MB", "8192")
    assert resolve_config().q_workspace_mb == 10240


def test_rejects_bad_port(monkeypatch):
    monkeypatch.setenv("KDB_PORT", "notanumber")
    with pytest.raises(ValueError):
        resolve_config()


def test_rejects_out_of_range_watermark(monkeypatch):
    monkeypatch.setenv("KDB_CACHE_WATERMARK", "1.5")
    with pytest.raises(ValueError):
        resolve_config()


def test_config_is_frozen():
    cfg = resolve_config()
    with pytest.raises(Exception):
        cfg.host = "elsewhere"  # type: ignore[misc]


def test_explicit_embedded_false_credential_is_honored(monkeypatch):
    """A credential of False must not be mistaken for 'not set'."""
    monkeypatch.delenv("KDB_EMBEDDED", raising=False)
    cfg = resolve_config({"kdb_embedded": False})
    assert cfg.embedded is False


def test_explicit_zero_memory_credential_raises(monkeypatch):
    """A credential of 0 must not be mistaken for 'not set' and silently defaulted."""
    monkeypatch.delenv("KDB_MEMORY_MB", raising=False)
    with pytest.raises(ValueError):
        resolve_config({"kdb_memory_mb": 0})


def test_explicit_zero_watermark_credential_raises(monkeypatch):
    """A credential of 0.0 must not be mistaken for 'not set' and silently defaulted."""
    monkeypatch.delenv("KDB_CACHE_WATERMARK", raising=False)
    with pytest.raises(ValueError):
        resolve_config({"kdb_cache_watermark": 0.0})


def test_empty_env_host_falls_back_to_default(monkeypatch):
    """An empty string is how a shell/compose env file expresses 'unset'."""
    monkeypatch.setenv("KDB_HOST", "")
    assert resolve_config().host == "127.0.0.1"


def test_ipv6_loopback_host_is_treated_as_embedded(monkeypatch):
    monkeypatch.setenv("KDB_HOST", "::1")
    assert resolve_config().embedded is True
