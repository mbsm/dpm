"""Plain-text table rendering and value formatting for the DPM CLI."""

import time
from typing import List

from dpm.constants import HOST_OFFLINE_THRESHOLD_SEC, STATE_DISPLAY


def format_table(headers: List[str], rows: List[List[str]], min_pad: int = 2) -> str:
    """Render a fixed-width plain-text table with dashed header underlines."""
    if not headers:
        return ""

    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(col_widths):
                col_widths[i] = max(col_widths[i], len(cell))

    def _fmt_row(cells):
        parts = []
        for i, cell in enumerate(cells):
            if i < len(col_widths):
                parts.append(cell.ljust(col_widths[i] + min_pad))
        return "".join(parts).rstrip()

    lines = [_fmt_row(headers)]
    lines.append(_fmt_row(["-" * col_widths[i] for i in range(len(headers))]))
    for row in rows:
        lines.append(_fmt_row(row))
    return "\n".join(lines)


def format_state(state_code: str) -> str:
    """Map a single-letter state code to a human-readable label."""
    return STATE_DISPLAY.get((state_code or "").strip().upper(), "Ready")


def format_runtime(seconds: int) -> str:
    """Format seconds as H:MM:SS or Ns for short durations."""
    if seconds < 0:
        return "-"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    if m > 0:
        return f"{m}:{s:02d}"
    return f"{s}s"


def format_mem_mb(mem_kb: int) -> str:
    """Format memory in kB as MB with one decimal."""
    if mem_kb <= 0:
        return "-"
    return f"{mem_kb / 1024:.1f}"


def format_cpu(cpu_frac: float) -> str:
    """Format CPU fraction (0.0-1.0) as percentage."""
    if cpu_frac <= 0:
        return "-"
    return f"{cpu_frac * 100:.1f}%"


def format_pid(pid: int) -> str:
    """Format PID, showing '-' for invalid values."""
    if pid <= 0:
        return "-"
    return str(pid)


def format_host_status(timestamp_usec: int) -> str:
    """Return 'Online' or 'Offline' based on timestamp age."""
    try:
        age = time.time() - (float(timestamp_usec) * 1e-6)
        return "Online" if age <= HOST_OFFLINE_THRESHOLD_SEC else "Offline"
    except (TypeError, ValueError, OverflowError):
        return "Offline"


def format_bool(val) -> str:
    """Format a boolean as Yes/No."""
    return "Yes" if val else "No"
