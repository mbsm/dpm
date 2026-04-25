"""Per-process on-disk log files with size-based rotation.

Each managed process writes its merged stdout+stderr to
``<process_log_dir>/<name>.log``. When the active file exceeds
``max_bytes``, it is rotated to ``<name>.log.1`` (existing
``.1``/``.2``/... shifted up to ``backups``; the oldest is dropped).

Distinct from the daemon's own event log (``/var/log/dpm/dpmd.log``):
managed-process output goes here; daemon warnings/errors go there.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from typing import Optional

DEFAULT_DIR = "/var/log/dpm/processes"
DEFAULT_MAX_BYTES = 50 * 1024 * 1024  # 50 MB
DEFAULT_BACKUPS = 3


def _safe_filename(name: str) -> str:
    return name.replace("/", "_").replace("\\", "_").replace("..", "_") or "_"


class ProcessLogFile:
    """Append-only log file with size+count rotation. Thread-safe.

    Both stdout-reader and stderr-reader threads write through the same
    instance; the lock serializes writes and rotation so output from
    concurrent threads never interleaves mid-line.
    """

    def __init__(self, path: str, max_bytes: int, backups: int) -> None:
        self.path = path
        self.max_bytes = max(int(max_bytes), 1)
        self.backups = max(int(backups), 1)
        self._lock = threading.Lock()
        self._fp: Optional[object] = None
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._open()

    def _open(self) -> None:
        # buffering=1 → line-buffered, so a `tail -f` in another shell sees
        # output as soon as a newline lands.
        self._fp = open(self.path, "a", buffering=1, encoding="utf-8", errors="replace")

    def write(self, line: str) -> None:
        if not line:
            return
        with self._lock:
            if self._fp is None:
                return
            try:
                self._fp.write(line)
            except OSError as e:
                logging.error("ProcessLogFile: write failed for %s: %s", self.path, e)
                return
            try:
                if self._fp.tell() >= self.max_bytes:
                    self._rotate_locked()
            except OSError:
                pass

    def write_marker(self, text: str) -> None:
        """Write a single-line marker (e.g., '--- start pid=1234 ---')."""
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.write(f"--- {ts} {text} ---\n")

    def write_crash_sidecar(
        self, exit_code: int, restart_count: int, last_stderr: str
    ) -> None:
        """Append a forensic breadcrumb to ``<path>.crash`` (separate file)."""
        sidecar = f"{self.path}.crash"
        try:
            with open(sidecar, "a", encoding="utf-8", errors="replace") as f:
                ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
                f.write(
                    f"--- {ts} exit={exit_code} restart_count={restart_count} ---\n"
                )
                if last_stderr:
                    f.write(last_stderr)
                    if not last_stderr.endswith("\n"):
                        f.write("\n")
        except OSError as e:
            logging.error(
                "ProcessLogFile: crash sidecar write failed for %s: %s", sidecar, e
            )

    def _rotate_locked(self) -> None:
        try:
            if self._fp is not None:
                self._fp.close()
        except OSError:
            pass
        # Drop the oldest, then shift: name.log.(N-1) -> name.log.N, ..., name.log -> name.log.1
        oldest = f"{self.path}.{self.backups}"
        if os.path.exists(oldest):
            try:
                os.unlink(oldest)
            except OSError as e:
                logging.error(
                    "ProcessLogFile: failed to drop oldest backup %s: %s", oldest, e
                )
        for i in range(self.backups - 1, 0, -1):
            src = f"{self.path}.{i}"
            dst = f"{self.path}.{i + 1}"
            if os.path.exists(src):
                try:
                    os.rename(src, dst)
                except OSError as e:
                    logging.error(
                        "ProcessLogFile: rotate %s -> %s failed: %s", src, dst, e
                    )
        if os.path.exists(self.path):
            try:
                os.rename(self.path, f"{self.path}.1")
            except OSError as e:
                logging.error(
                    "ProcessLogFile: rotate %s -> %s.1 failed: %s",
                    self.path,
                    self.path,
                    e,
                )
        try:
            self._open()
        except OSError as e:
            logging.error(
                "ProcessLogFile: reopen after rotate failed for %s: %s", self.path, e
            )
            self._fp = None

    def close(self) -> None:
        with self._lock:
            if self._fp is not None:
                try:
                    self._fp.close()
                except OSError:
                    pass
                self._fp = None


def open_process_log(
    name: str,
    log_dir: str = DEFAULT_DIR,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backups: int = DEFAULT_BACKUPS,
) -> Optional[ProcessLogFile]:
    """Open ``<log_dir>/<name>.log`` for append. Returns None on permission errors.

    Returning None (rather than raising) lets the daemon keep a process
    running even when the log directory is not writable — the in-memory
    ring buffer + LCM stream still work.
    """
    path = os.path.join(log_dir, f"{_safe_filename(name)}.log")
    try:
        return ProcessLogFile(path, max_bytes, backups)
    except OSError as e:
        logging.warning(
            "ProcessLogFile: cannot open %s for process %s (%s); disk logging disabled.",
            path, name, e,
        )
        return None
