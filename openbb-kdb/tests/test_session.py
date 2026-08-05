"""Session lifecycle: spawn, reuse, notice death, respawn, give up cleanly."""

import subprocess

import pytest

import openbb_kdb.session as session
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


class FakeProc:
    """Stands in for subprocess.Popen -- records terminate/wait/kill calls."""

    def __init__(self, alive=True):
        self.alive = alive
        self.terminated = False
        self.killed = False
        self.wait_calls = 0

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True
        self.alive = False

    def wait(self, timeout=None):
        self.wait_calls += 1
        if self.alive:
            raise subprocess.TimeoutExpired(cmd="q", timeout=timeout)
        return 0


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


def test_orphaned_process_terminated_on_connect_failure(monkeypatch):
    """A q we spawned must never keep running unsupervised after connect fails."""
    fake_proc = FakeProc(alive=True)

    def fake_spawn(self):
        self._proc = fake_proc

    def fake_connect(self):
        raise OSError("connection refused")

    # Keep the retry loop's total budget tiny so the test doesn't burn real time.
    monkeypatch.setattr(session, "_CONNECT_BUDGET_S", 0.05)
    monkeypatch.setattr(session, "_CONNECT_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(KdbSession, "_spawn", fake_spawn)
    monkeypatch.setattr(KdbSession, "_connect", fake_connect)

    s = KdbSession(cfg())
    with pytest.raises(KdbUnavailable):
        s.connection()

    assert fake_proc.terminated is True
    assert s._proc is None


def test_close_terminates_and_reaps_process_falling_back_to_kill():
    """close() must wait() after terminate(), and escalate to kill() if it hangs."""
    fake_proc = FakeProc(alive=True)
    s = KdbSession(cfg())
    s._proc = fake_proc

    s.close()

    assert fake_proc.terminated is True
    assert fake_proc.wait_calls >= 1
    assert fake_proc.killed is True
    assert s._proc is None


def test_close_does_not_kill_a_process_that_exits_on_terminate():
    """A clean SIGTERM exit must not escalate to SIGKILL."""

    class ExitsOnTerminate(FakeProc):
        def terminate(self):
            super().terminate()
            self.alive = False

    fake_proc = ExitsOnTerminate(alive=True)
    s = KdbSession(cfg())
    s._proc = fake_proc

    s.close()

    assert fake_proc.terminated is True
    assert fake_proc.wait_calls == 1
    assert fake_proc.killed is False
