"""Config resolution: env vars and OpenBB credentials into one frozen object."""

import pytest

import kdb_store.config as config
from kdb_store.config import resolve_config


@pytest.fixture(autouse=True)
def _unmade_qhome_decision(monkeypatch):
    """QHOME is decided once per PROCESS; give each test that decision unmade."""
    monkeypatch.setattr(config, "_qhome", None)


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


def test_qlic_env_var_is_carried_onto_config(monkeypatch):
    """The mounted licence directory must survive into KdbConfig.qlic."""
    monkeypatch.setenv("QLIC", "/opt/kx-license")
    assert resolve_config().qlic == "/opt/kx-license"


def test_qhome_survives_pykx_rewriting_the_environment(monkeypatch):
    """`import pykx` REWRITES os.environ["QHOME"] to PyKX's own bundled q. A
    second resolve_config() must not therefore hand back a different qhome --
    spawning PyKX's q instead of the operator's kdb-x install."""
    monkeypatch.setenv("QHOME", "/opt/kx")
    first = resolve_config()

    monkeypatch.setenv("QHOME", "/usr/local/lib/python3.13/site-packages/pykx/lib")
    second = resolve_config()

    assert first.qhome == second.qhome == "/opt/kx"


def test_qlic_falls_back_to_qhome_when_unset(monkeypatch):
    """No QLIC set: preserve today's behaviour of looking in QHOME."""
    monkeypatch.delenv("QLIC", raising=False)
    monkeypatch.setenv("QHOME", "/opt/kx")
    cfg = resolve_config()
    assert cfg.qlic == cfg.qhome == "/opt/kx"
