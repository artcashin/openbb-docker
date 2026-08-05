"""Config resolution: env vars and OpenBB credentials into one frozen object."""

import pytest

from openbb_kdb.config import KdbConfig, resolve_config


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
