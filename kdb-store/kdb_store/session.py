"""Ownership of the q process, its IPC connection, and the thread that may touch it.

q is a child of THIS container, bound to loopback. Everything in this stack
shares the tailscale container's network namespace, so a loopback bind is
reachable by every sibling service and by no tailnet peer. Binding 0.0.0.0
would publish an unauthenticated q -- which executes arbitrary q -- to the
whole tailnet.

Crossing q's -w kills the process outright (verified against kdb-x 5.0), so a
dead q is treated as an ordinary state: detect, respawn, carry on.

PyKX is not merely un-serialised across threads, it is unusable from more than
one thread: four STRICTLY SEQUENTIAL write/read rounds (no concurrency at all)
survive on one thread and abort with `free(): invalid size` when each round
runs on a fresh thread. A mutex therefore cannot fix it -- serialising the
calls leaves the same heap corruption, it only hides the visible symptom of
replies crossing between threads. What the C layer wants is thread AFFINITY,
so this module owns a single-worker executor and every PyKX interaction --
spawning q, opening the connection, every query, every K-object conversion --
happens on that one thread for the process lifetime. Callers hand work in
through `run()`.
"""

import logging
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from kdb_store.config import KdbConfig

logger = logging.getLogger(__name__)

# poll() only proves a child hasn't exited -- not that it's listening yet --
# so a freshly spawned q gets a bounded window of connect attempts rather
# than one blind fixed sleep. Checking poll() between attempts lets a q that
# exits immediately (e.g. a missing license) fail fast instead of waiting
# out the whole budget.
_CONNECT_BUDGET_S = 5.0
_CONNECT_POLL_INTERVAL_S = 0.15

# Bounded shutdown: give q a chance to exit cleanly on SIGTERM, then force it.
_TERMINATE_WAIT_S = 3.0
_KILL_WAIT_S = 2.0


class KdbUnavailable(Exception):
    """No usable kdb+ connection. Callers degrade to upstream pass-through."""


class KdbSession:
    """Owns at most one q process, one IPC connection, and the thread that may use them."""

    def __init__(self, config: KdbConfig):
        self.config = config
        self._conn = None
        self._proc: subprocess.Popen | None = None
        self._proc_log_path: str | None = None
        self._given_up = False
        # One worker, forever: PyKX needs the SAME thread every time, not just
        # one thread at a time. `_owner_ident` is stamped by the worker itself
        # on its first task so `run()` can recognise a re-entrant call.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="openbb-kdb-q")
        self._owner_ident: int | None = None
        self._closed = False

    def run(self, fn):
        """Run `fn()` on the q owner thread and return its result.

        Everything that touches PyKX must go through here, including creating
        the connection and spawning q -- affinity that starts after the
        connection was opened elsewhere is not affinity.

        A call already ON the owner thread runs inline: `connection()` and
        `is_alive()` route through `run()` themselves, so a store method that
        asks for the connection from inside its own submitted task would
        otherwise queue work behind itself on a one-worker pool and deadlock.
        """
        if threading.get_ident() == self._owner_ident:
            return fn()
        if self._closed:
            raise KdbUnavailable("kdb+ session is closed")
        return self._executor.submit(self._own, fn).result()

    def _own(self, fn):
        """Body of every submitted task: claim ownership, then do the work."""
        self._owner_ident = threading.get_ident()
        return fn()

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

        stdout/stderr go to a temp file, not a pipe: nothing ever reads a
        PIPE for a long-lived child, so once q writes enough output to fill
        the OS pipe buffer its write() blocks forever -- a live, undetectable
        hang instead of the clean death this module is built to handle. The
        file is read back only if q exits immediately, to preserve the
        startup diagnostic (e.g. a missing license).
        """
        import os
        import tempfile

        if self._proc is not None and self._proc.poll() is None:
            return
        self._cleanup_log_file()
        env = dict(os.environ, QHOME=self.config.qhome, QLIC=self.config.qlic)
        log_file = tempfile.NamedTemporaryFile(
            prefix="openbb-kdb-q-", suffix=".log", delete=False
        )
        self._proc_log_path = log_file.name
        try:
            self._proc = subprocess.Popen(  # noqa: S603
                self._q_argv(),
                stdin=subprocess.PIPE,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=self.config.qhome,
            )
        finally:
            log_file.close()

    def _read_proc_log(self) -> str:
        """Best-effort read of the spawned q's captured stdout/stderr."""
        if not self._proc_log_path:
            return ""
        try:
            with open(self._proc_log_path, "rb") as fh:
                return fh.read().decode(errors="replace")
        except OSError:
            return ""

    def _cleanup_log_file(self) -> None:
        """Remove the previous spawn's log file, if any. Best-effort."""
        import os

        if self._proc_log_path is None:
            return
        try:
            os.remove(self._proc_log_path)
        except OSError:
            pass
        self._proc_log_path = None

    def _connect(self):
        """Open a PyKX IPC connection (unlicensed client mode -- no license needed)."""
        import pykx as kx

        return kx.SyncQConnection(self.config.host, self.config.port)

    def _connect_with_retry(self):
        """Connect to a just-spawned q, retrying until it's listening or we give up.

        A successful _spawn() only means q hasn't exited -- it may not be
        listening yet. Retry the connect itself within a bounded total
        budget, and bail out immediately (without waiting for the budget) if
        poll() shows the process has already died.
        """
        deadline = time.monotonic() + _CONNECT_BUDGET_S
        while True:
            if self._proc is not None and self._proc.poll() is not None:
                out = self._read_proc_log()[:500]
                raise OSError(f"q exited immediately: {out}")
            try:
                return self._connect()
            except Exception:
                if self._proc is None or time.monotonic() >= deadline:
                    raise
                time.sleep(_CONNECT_POLL_INTERVAL_S)

    def is_alive(self) -> bool:
        """True when the current connection still answers."""
        return self.run(self._is_alive)

    def _is_alive(self) -> bool:
        if self._conn is None:
            return False
        try:
            self._conn("1+1")
            return True
        except Exception:
            return False

    def connection(self):
        """Return a live connection, spawning or respawning as needed.

        The object handed back belongs to the owner thread: use it only from
        inside a `run()` callable, never directly from the caller's thread.
        """
        return self.run(self._connection)

    def _connection(self):
        if self._given_up:
            raise KdbUnavailable("kdb+ previously unreachable; not retrying")
        if self._conn is not None and self._is_alive():
            return self._conn
        self._conn = None
        spawned = False
        try:
            if self.config.embedded:
                self._spawn()
                spawned = True
                self._conn = self._connect_with_retry()
            else:
                self._conn = self._connect()
        except Exception as exc:
            if spawned:
                # We own this process; a q we started must never be left
                # running unsupervised just because we failed to connect.
                self._stop_proc()
            self._given_up = True
            logger.warning("kdb+ unavailable, falling back to upstream: %s", exc)
            raise KdbUnavailable(str(exc)) from exc
        return self._conn

    def _stop_proc(self) -> None:
        """Terminate and reap the child process, escalating to SIGKILL. Never raises."""
        proc = self._proc
        self._proc = None
        if proc is None or proc.poll() is not None:
            self._cleanup_log_file()
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=_TERMINATE_WAIT_S)
            except subprocess.TimeoutExpired:
                logger.warning("q did not exit within %.1fs of SIGTERM; sending SIGKILL",
                                _TERMINATE_WAIT_S)
                proc.kill()
                try:
                    proc.wait(timeout=_KILL_WAIT_S)
                except subprocess.TimeoutExpired:
                    logger.warning("q did not exit within %.1fs of SIGKILL", _KILL_WAIT_S)
        except Exception:
            logger.debug("error while stopping q process", exc_info=True)
        self._cleanup_log_file()

    def close(self) -> None:
        """Close the connection, stop a q we started, retire the owner thread.

        Never raises. The connection is closed ON the owner thread -- closing a
        PyKX handle is as much a PyKX call as querying it -- and the executor is
        shut down only afterwards, so no task can outlive the process it drives.
        """
        try:
            self.run(self._shutdown)
        except Exception:
            # The owner thread is already gone (or never started): the child
            # process still has to be reaped, and _shutdown is idempotent.
            logger.debug("kdb+ shutdown could not run on the owner thread", exc_info=True)
            try:
                self._shutdown()
            except Exception:
                logger.debug("error during kdb+ shutdown", exc_info=True)
        self._closed = True
        try:
            self._executor.shutdown(wait=True)
        except Exception:
            logger.debug("error shutting down the kdb+ owner thread", exc_info=True)
        # Thread identities are recycled once a thread is gone: a stale ident
        # would let some unrelated future thread take the re-entrant path.
        self._owner_ident = None

    def _shutdown(self) -> None:
        """Close the IPC connection and reap the child. Never raises."""
        try:
            if self._conn is not None:
                self._conn.close()
        except Exception as exc:
            logger.debug("error closing kdb+ IPC connection: %s", exc)
        self._conn = None
        self._stop_proc()
