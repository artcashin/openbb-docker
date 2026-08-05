"""Ownership of the q process and its IPC connection.

q is a child of THIS container, bound to loopback. Everything in this stack
shares the tailscale container's network namespace, so a loopback bind is
reachable by every sibling service and by no tailnet peer. Binding 0.0.0.0
would publish an unauthenticated q -- which executes arbitrary q -- to the
whole tailnet.

Crossing q's -w kills the process outright (verified against kdb-x 5.0), so a
dead q is treated as an ordinary state: detect, respawn, carry on.
"""

import logging
import subprocess
import time

from openbb_kdb.config import KdbConfig

logger = logging.getLogger(__name__)


class KdbUnavailable(Exception):
    """No usable kdb+ connection. Callers degrade to upstream pass-through."""


class KdbSession:
    """Owns at most one q process and one IPC connection."""

    def __init__(self, config: KdbConfig):
        self.config = config
        self._conn = None
        self._proc: subprocess.Popen | None = None
        self._given_up = False

    def _q_argv(self) -> list[str]:
        """Argument vector for the q server."""
        cfg = self.config
        return [
            f"{cfg.qhome}/bin/q",
            "-p", f"127.0.0.1:{cfg.port}",
            "-w", str(cfg.q_workspace_mb),
            "-q",
        ]

    def _spawn(self) -> None:
        """Start q as a child process.

        stdin is a pipe we never close: q reads its console from stdin and
        exits on EOF, which in a detached container happens immediately.
        """
        import os

        if self._proc is not None and self._proc.poll() is None:
            return
        env = dict(os.environ, QHOME=self.config.qhome, QLIC=self.config.qhome)
        self._proc = subprocess.Popen(  # noqa: S603
            self._q_argv(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=self.config.qhome,
        )
        time.sleep(1.0)
        if self._proc.poll() is not None:
            out = (self._proc.stdout.read() or b"").decode(errors="replace")[:500]
            raise OSError(f"q exited immediately: {out}")

    def _connect(self):
        """Open a PyKX IPC connection (unlicensed client mode -- no license needed)."""
        import pykx as kx

        return kx.SyncQConnection(self.config.host, self.config.port)

    def is_alive(self) -> bool:
        """True when the current connection still answers."""
        if self._conn is None:
            return False
        try:
            self._conn("1+1")
            return True
        except Exception:
            return False

    def connection(self):
        """Return a live connection, spawning or respawning as needed."""
        if self._given_up:
            raise KdbUnavailable("kdb+ previously unreachable; not retrying")
        if self._conn is not None and self.is_alive():
            return self._conn
        self._conn = None
        try:
            if self.config.embedded:
                self._spawn()
            self._conn = self._connect()
        except Exception as exc:
            self._given_up = True
            logger.warning("kdb+ unavailable, falling back to upstream: %s", exc)
            raise KdbUnavailable(str(exc)) from exc
        return self._conn

    def close(self) -> None:
        """Close the connection and stop a q we started. Never raises."""
        try:
            if self._conn is not None:
                self._conn.close()
        except Exception:
            pass
        self._conn = None
        try:
            if self._proc is not None and self._proc.poll() is None:
                self._proc.terminate()
        except Exception:
            pass
        self._proc = None
