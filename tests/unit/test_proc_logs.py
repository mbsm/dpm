"""Tests for per-process on-disk log files with size+count rotation."""

from __future__ import annotations

import os
import threading

import pytest

from dpmd.proc_logs import ProcessLogFile, _safe_filename, open_process_log


def test_safe_filename_strips_path_separators():
    assert _safe_filename("a/b") == "a_b"
    assert _safe_filename("a\\b") == "a_b"
    assert _safe_filename("..") == "_"
    assert _safe_filename("..foo") == "_foo"
    assert _safe_filename("") == "_"


def test_open_process_log_creates_dir(tmp_path):
    log_dir = tmp_path / "p"
    f = open_process_log("foo", log_dir=str(log_dir))
    assert f is not None
    assert (log_dir / "foo.log").exists()
    f.close()


def test_open_process_log_returns_none_on_unwritable(tmp_path, monkeypatch):
    """Permission errors during open() should yield None, not raise."""
    def _boom(*a, **kw):
        raise OSError(13, "permission denied")
    monkeypatch.setattr("dpmd.proc_logs.os.makedirs", _boom)
    f = open_process_log("foo", log_dir="/forbidden")
    assert f is None


def test_write_appends_lines(tmp_path):
    f = ProcessLogFile(str(tmp_path / "x.log"), max_bytes=1_000_000, backups=2)
    f.write("alpha\n")
    f.write("beta\n")
    f.close()
    content = (tmp_path / "x.log").read_text()
    assert content == "alpha\nbeta\n"


def test_rotation_shifts_backups(tmp_path):
    """Crossing max_bytes rotates name.log -> name.log.1; older shift up."""
    path = tmp_path / "x.log"
    # 50-byte cap => first write of ≥50 bytes triggers rotation
    f = ProcessLogFile(str(path), max_bytes=50, backups=2)
    payload = "x" * 60 + "\n"
    f.write(payload)              # writes, then rotates: rename x.log -> x.log.1, reopens fresh
    f.write("after-rotate-1\n")
    f.write(payload)              # rotates again: x.log.1 -> x.log.2, x.log -> x.log.1
    f.write("after-rotate-2\n")
    f.close()
    assert path.exists()
    assert (tmp_path / "x.log.1").exists()
    assert (tmp_path / "x.log.2").exists()
    # x.log.3 must NOT exist (backups=2)
    assert not (tmp_path / "x.log.3").exists()
    # The oldest content should have rolled to .2
    assert "after-rotate-1" in (tmp_path / "x.log.1").read_text()


def test_rotation_drops_oldest_beyond_backups(tmp_path):
    """With backups=1, only one prior generation is retained."""
    path = tmp_path / "x.log"
    f = ProcessLogFile(str(path), max_bytes=50, backups=1)
    f.write("x" * 60 + "\n")     # rotate 1
    f.write("y" * 60 + "\n")     # rotate 2 — first .1 should be dropped
    f.close()
    assert path.exists()
    assert (tmp_path / "x.log.1").exists()
    assert not (tmp_path / "x.log.2").exists()


def test_concurrent_writes_serialize(tmp_path):
    """Concurrent writes from N threads must not interleave mid-line."""
    f = ProcessLogFile(str(tmp_path / "x.log"), max_bytes=10_000_000, backups=2)
    line_a = "AAAAAAAAAAAAAAAAAAAA\n"
    line_b = "BBBBBBBBBBBBBBBBBBBB\n"

    def writer(line, n):
        for _ in range(n):
            f.write(line)

    t1 = threading.Thread(target=writer, args=(line_a, 200))
    t2 = threading.Thread(target=writer, args=(line_b, 200))
    t1.start(); t2.start()
    t1.join(); t2.join()
    f.close()

    content = (tmp_path / "x.log").read_text()
    # Each line, separately, must always appear intact.
    for line in content.splitlines(keepends=True):
        assert line in (line_a, line_b), f"interleaved write detected: {line!r}"


def test_marker(tmp_path):
    f = ProcessLogFile(str(tmp_path / "x.log"), max_bytes=10_000, backups=1)
    f.write_marker("start pid=99")
    f.close()
    content = (tmp_path / "x.log").read_text()
    assert "start pid=99" in content
    assert content.startswith("--- ") and content.endswith("---\n")


def test_crash_sidecar_writes_separate_file(tmp_path):
    f = ProcessLogFile(str(tmp_path / "x.log"), max_bytes=10_000, backups=1)
    f.write_crash_sidecar(
        exit_code=139, restart_count=3, last_stderr="segfault tail\n"
    )
    f.close()
    sidecar = tmp_path / "x.log.crash"
    assert sidecar.exists()
    txt = sidecar.read_text()
    assert "exit=139" in txt
    assert "restart_count=3" in txt
    assert "segfault tail" in txt


def test_close_is_idempotent(tmp_path):
    f = ProcessLogFile(str(tmp_path / "x.log"), max_bytes=10_000, backups=1)
    f.close()
    f.close()  # must not raise
    # write after close is a no-op (logged, but doesn't crash)
    f.write("late\n")
