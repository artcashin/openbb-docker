"""cert-sync.sh: fetch a cert, and HUP the server only when it actually changed."""

import signal
import subprocess
import time
from pathlib import Path

HUP_CATCHER = (
    "import signal, sys, time\n"
    "p = sys.argv[1]\n"
    "signal.signal(signal.SIGHUP, lambda *_: open(p, 'a').write('HUP\\n'))\n"
    "time.sleep(30)\n"
)


def _start_catcher(tmp_path):
    marker = tmp_path / "hups.txt"
    script = tmp_path / "catcher.py"
    script.write_text(HUP_CATCHER)
    proc = subprocess.Popen(["python3", str(script), str(marker)])
    pid_file = tmp_path / "minio.pid"
    pid_file.write_text(str(proc.pid))
    time.sleep(1)  # let the handler install
    return proc, pid_file, marker


def test_writes_cert_and_key(tmp_path, run_sync):
    certs = tmp_path / "certs"
    certs.mkdir()
    pid_file = tmp_path / "none.pid"
    result = run_sync(certs, "minio.example.ts.net", pid_file)
    assert result.returncode == 0, result.stderr
    assert (certs / "public.crt").read_text() == "CERT-A"
    assert (certs / "private.key").exists()


def test_key_is_not_world_readable(tmp_path, run_sync):
    certs = tmp_path / "certs"
    certs.mkdir()
    run_sync(certs, "minio.example.ts.net", tmp_path / "none.pid")
    mode = (certs / "private.key").stat().st_mode
    assert mode & 0o077 == 0


def test_no_hup_on_first_write(tmp_path, run_sync):
    certs = tmp_path / "certs"
    certs.mkdir()
    proc, pid_file, marker = _start_catcher(tmp_path)
    try:
        run_sync(certs, "minio.example.ts.net", pid_file)
        time.sleep(1)
        assert not marker.exists(), "first write should not signal a reload"
    finally:
        proc.kill()


def test_no_hup_when_cert_is_unchanged(tmp_path, run_sync, fake_tailscale):
    certs = tmp_path / "certs"
    certs.mkdir()
    proc, pid_file, marker = _start_catcher(tmp_path)
    try:
        run_sync(certs, "minio.example.ts.net", pid_file)
        run_sync(certs, "minio.example.ts.net", pid_file)
        time.sleep(1)
        assert not marker.exists(), "unchanged cert must not signal a reload"
    finally:
        proc.kill()


def test_hups_when_cert_changes(tmp_path, run_sync, fake_tailscale):
    certs = tmp_path / "certs"
    certs.mkdir()
    proc, pid_file, marker = _start_catcher(tmp_path)
    try:
        run_sync(certs, "minio.example.ts.net", pid_file)
        (fake_tailscale / "cert-body").write_text("CERT-B")
        run_sync(certs, "minio.example.ts.net", pid_file)
        time.sleep(1)
        assert marker.exists() and "HUP" in marker.read_text()
        assert (certs / "public.crt").read_text() == "CERT-B"
    finally:
        proc.kill()


def test_reports_failure_when_tailscale_fails(tmp_path, run_sync, fake_tailscale):
    (fake_tailscale / "fail").write_text("")
    certs = tmp_path / "certs"
    certs.mkdir()
    result = run_sync(certs, "minio.example.ts.net", tmp_path / "none.pid")
    assert result.returncode == 1
    assert "boom" in result.stderr or "cert" in result.stderr.lower()


def test_existing_cert_is_kept_when_renewal_fails(tmp_path, run_sync, fake_tailscale):
    certs = tmp_path / "certs"
    certs.mkdir()
    run_sync(certs, "minio.example.ts.net", tmp_path / "none.pid")
    (fake_tailscale / "fail").write_text("")
    run_sync(certs, "minio.example.ts.net", tmp_path / "none.pid")
    assert (certs / "public.crt").read_text() == "CERT-A", "must not clobber a good cert"
