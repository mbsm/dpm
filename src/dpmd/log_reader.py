"""Read on-disk process logs for ``read_log`` requests.

Walks the rotated set (``<name>.log``, ``<name>.log.1``, ``<name>.log.2``,
...) in order from oldest to newest, optionally filtering by line
timestamps and capping the tail.

Lines themselves are not timestamped (we keep the on-disk format
identical to whatever the process printed). The ``since_us`` filter is
therefore best-effort: rotated files older than ``since_us`` (judged by
their mtime) are skipped wholesale; the active file is read in full.
"""

from __future__ import annotations

import logging
import os
from collections import deque
from typing import Iterable, List

from dpmd.proc_logs import _safe_filename


def _ordered_log_files(log_dir: str, name: str, max_backups: int = 32) -> List[str]:
    """Return [oldest, ..., newest] paths for a process's rotated logs."""
    base = os.path.join(log_dir, f"{_safe_filename(name)}.log")
    out: List[str] = []
    # Older generations have larger numeric suffixes — walk down then add base.
    for i in range(max_backups, 0, -1):
        p = f"{base}.{i}"
        if os.path.exists(p):
            out.append(p)
    if os.path.exists(base):
        out.append(base)
    return out


def _file_intersects_since(path: str, since_us: int) -> bool:
    """True if the file's mtime suggests it could contain lines newer than since_us."""
    if since_us <= 0:
        return True
    try:
        mtime_us = int(os.stat(path).st_mtime * 1_000_000)
    except OSError:
        return True  # be conservative — don't accidentally hide content
    return mtime_us >= since_us


def read_log_lines(
    log_dir: str,
    name: str,
    since_us: int = 0,
    tail_lines: int = 0,
) -> str:
    """Return a string of joined log lines matching the request.

    - ``since_us``: 0 for no lower bound; otherwise drops files whose
      mtime predates the bound. Per-line timestamping would be more
      precise but the on-disk format intentionally preserves the
      process's own output verbatim.
    - ``tail_lines``: 0 for no cap; otherwise keeps the last N lines
      after the since_us file filter.
    """
    files = _ordered_log_files(log_dir, name)
    if not files:
        return ""

    if since_us > 0:
        files = [f for f in files if _file_intersects_since(f, since_us)]
        if not files:
            return ""

    if tail_lines > 0:
        # deque keeps memory bounded by the line cap, even for big files.
        buf: deque = deque(maxlen=tail_lines)
        for path in files:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        buf.append(line)
            except OSError as e:
                logging.warning("read_log: failed to read %s: %s", path, e)
        return "".join(buf)

    parts: List[str] = []
    for path in files:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                parts.append(f.read())
        except OSError as e:
            logging.warning("read_log: failed to read %s: %s", path, e)
    return "".join(parts)


def chunk(text: str, max_bytes: int) -> Iterable[str]:
    """Split ``text`` into UTF-8-safe pieces no larger than ``max_bytes``.

    The cap is on encoded byte length (LCM strings are byte-counted), so
    we encode → split → decode rather than slicing characters and hoping.
    """
    if not text:
        return
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= max_bytes:
        yield text
        return
    i = 0
    while i < len(raw):
        end = min(i + max_bytes, len(raw))
        # Backtrack to a UTF-8 boundary so we never split a multi-byte char.
        if end < len(raw):
            while end > i and (raw[end] & 0xC0) == 0x80:
                end -= 1
            if end == i:
                end = min(i + max_bytes, len(raw))  # fallback — shouldn't happen
        yield raw[i:end].decode("utf-8", errors="replace")
        i = end
