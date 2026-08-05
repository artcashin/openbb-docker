"""Session lifecycle: spawn, reuse, notice death, respawn, give up cleanly."""

import pytest

from openbb_kdb.config import KdbConfig
from openbb_kdb.session import KdbSession, KdbUnavailable


def cfg(**kw) -> KdbConfig:
    base = dict(host="127.0.0.1", port=5000, embedded=True, memory_mb=1024,
                watermark=0.75, upstream="eodhd", qhome="/opt/kx")
    base.update(kw)
    return KdbConfig(**base)


class FakeConn:
    """Stands in for pykx.SyncQConnection."""

    def __init__(self, alive=True):
        self.alive = alive
        self.calls = []

    def __call__(self, query, *args):
        if not self.alive:
            raise RuntimeError("Attempted to use a closed IPC connection")
        self.calls.append(query)
        return 2

    def close(self):
        self.alive = False


def test_connects_and_reuses_one_connection(monkeypatch):
    made = []
    monkeypatch.setattr(KdbSession, "_spawn", lambda self: None)
    monkeypatch.setattr(KdbSession, "_connect", lambda self: made.append(1) or FakeConn())
    s = KdbSession(cfg())
    first, second = s.connection(), s.connection()
    assert first is second
    assert len(made) == 1


def test_health_check_detects_a_dead_q(monkeypatch):
    monkeypatch.setattr(KdbSession, "_spawn", lambda self: None)
    monkeypatch.setattr(KdbSession, "_connect", lambda self: FakeConn())
    s = KdbSession(cfg())
    conn = s.connection()
    assert s.is_alive() is True
    conn.alive = False
    assert s.is_alive() is False


def test_respawns_after_death(monkeypatch):
    """q dying is a normal state, not an exception -- the next call gets a new one."""
    conns = [FakeConn(), FakeConn()]
    spawns = []
    monkeypatch.setattr(KdbSession, "_spawn", lambda self: spawns.append(1))
    monkeypatch.setattr(KdbSession, "_connect", lambda self: conns.pop(0))
    s = KdbSession(cfg())
    first = s.connection()
    first.alive = False
    second = s.connection()
    assert second is not first
    assert len(spawns) == 2


def test_external_server_is_never_spawned(monkeypatch):
    spawns = []
    monkeypatch.setattr(KdbSession, "_spawn", lambda self: spawns.append(1))
    monkeypatch.setattr(KdbSession, "_connect", lambda self: FakeConn())
    KdbSession(cfg(host="kdb.internal", embedded=False)).connection()
    assert spawns == []


def test_connect_failure_raises_kdb_unavailable(monkeypatch):
    def boom(self):
        raise OSError("connection refused")

    monkeypatch.setattr(KdbSession, "_spawn", lambda self: None)
    monkeypatch.setattr(KdbSession, "_connect", boom)
    with pytest.raises(KdbUnavailable):
        KdbSession(cfg()).connection()


def test_repeated_failure_is_not_retried_every_call(monkeypatch):
    """A missing license must not cost a spawn attempt on every single request."""
    attempts = []

    def boom(self):
        attempts.append(1)
        raise OSError("connection refused")

    monkeypatch.setattr(KdbSession, "_spawn", lambda self: None)
    monkeypatch.setattr(KdbSession, "_connect", boom)
    s = KdbSession(cfg())
    for _ in range(5):
        with pytest.raises(KdbUnavailable):
            s.connection()
    assert len(attempts) == 1


def test_q_command_binds_loopback_and_sets_workspace():
    """The bind is load-bearing: 0.0.0.0 would expose an unauthenticated q."""
    argv = KdbSession(cfg(memory_mb=1024))._q_argv()
    assert argv[1] == "-p"
    assert argv[2] == "127.0.0.1:5000"
    assert "0.0.0.0" not in " ".join(argv)
    assert argv[argv.index("-w") + 1] == "1280"  # 1024 * 1.25
