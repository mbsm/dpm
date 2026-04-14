"""Tests for CLI table formatting and value helpers."""

import time

from dpm.cli.formatting import (
    format_bool,
    format_cpu,
    format_host_status,
    format_mem_mb,
    format_pid,
    format_runtime,
    format_state,
    format_table,
)


# ---------------------------------------------------------------------------
# format_table
# ---------------------------------------------------------------------------

def test_format_table_basic():
    headers = ["Name", "Value"]
    rows = [["foo", "1"], ["barbaz", "22"]]
    result = format_table(headers, rows)
    lines = result.split("\n")
    assert len(lines) == 4  # header + underline + 2 rows
    assert "Name" in lines[0]
    assert "----" in lines[1]
    assert "foo" in lines[2]
    assert "barbaz" in lines[3]


def test_format_table_empty_rows():
    result = format_table(["A", "B"], [])
    lines = result.split("\n")
    assert len(lines) == 2  # header + underline only


def test_format_table_empty_headers():
    assert format_table([], []) == ""


def test_format_table_alignment():
    result = format_table(["X"], [["short"], ["much longer"]])
    lines = result.split("\n")
    # underline should be as wide as the longest cell
    assert len(lines[1].strip()) >= len("much longer")


# ---------------------------------------------------------------------------
# Value formatters
# ---------------------------------------------------------------------------

def test_format_state_codes():
    assert format_state("T") == "Ready"
    assert format_state("R") == "Running"
    assert format_state("F") == "Failed"
    assert format_state("K") == "Killed"
    assert format_state("") == "Ready"
    assert format_state("Z") == "Ready"
    assert format_state("S") == "Suspended"


def test_format_runtime():
    assert format_runtime(0) == "0s"
    assert format_runtime(59) == "59s"
    assert format_runtime(60) == "1:00"
    assert format_runtime(3661) == "1:01:01"
    assert format_runtime(-1) == "-"


def test_format_mem_mb():
    assert format_mem_mb(1024) == "1.0"
    assert format_mem_mb(0) == "-"
    assert format_mem_mb(-1) == "-"
    assert format_mem_mb(512) == "0.5"


def test_format_cpu():
    assert format_cpu(0.5) == "50.0%"
    assert format_cpu(0.0) == "-"
    assert format_cpu(1.0) == "100.0%"


def test_format_pid():
    assert format_pid(1234) == "1234"
    assert format_pid(-1) == "-"
    assert format_pid(0) == "-"


def test_format_host_status_online():
    now_usec = int(time.time() * 1e6)
    assert format_host_status(now_usec) == "Online"


def test_format_host_status_offline():
    old_usec = int((time.time() - 60) * 1e6)
    assert format_host_status(old_usec) == "Offline"


def test_format_host_status_zero():
    assert format_host_status(0) == "Offline"


def test_format_bool():
    assert format_bool(True) == "Yes"
    assert format_bool(False) == "No"
