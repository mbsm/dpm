"""Tests for cgroups v2 module (mocked filesystem)."""

import os
from unittest.mock import patch

import pytest


def test_cgroups_available_true(tmp_path):
    """Returns True when cgroup v2 unified hierarchy is mounted and writable."""
    from dpm.agent.cgroups import cgroups_available
    dpm_dir = tmp_path / "dpm"
    dpm_dir.mkdir()
    with patch("dpm.agent.cgroups.CGROUP_BASE", str(tmp_path)):
        assert cgroups_available() is True


def test_cgroups_available_false_no_dir():
    """Returns False when cgroup dir doesn't exist."""
    from dpm.agent.cgroups import cgroups_available
    with patch("dpm.agent.cgroups.CGROUP_BASE", "/nonexistent/cgroup/path"):
        assert cgroups_available() is False


def test_setup_cgroup_creates_dir_and_writes(tmp_path):
    """setup_cgroup creates the cgroup dir and writes controller files."""
    from dpm.agent.cgroups import setup_cgroup
    with patch("dpm.agent.cgroups.CGROUP_BASE", str(tmp_path)):
        setup_cgroup("myproc", pid=1234, cpuset="0,1", cpu_limit=1.5, mem_limit=1073741824)

    cgroup_dir = tmp_path / "myproc"
    assert cgroup_dir.is_dir()
    assert (cgroup_dir / "cpuset.cpus").read_text() == "0,1"
    assert (cgroup_dir / "cpu.max").read_text() == "150000 100000"
    assert (cgroup_dir / "memory.max").read_text() == "1073741824"
    assert (cgroup_dir / "cgroup.procs").read_text() == "1234"


def test_setup_cgroup_skips_unset_limits(tmp_path):
    """Only writes controller files for non-zero limits."""
    from dpm.agent.cgroups import setup_cgroup
    with patch("dpm.agent.cgroups.CGROUP_BASE", str(tmp_path)):
        setup_cgroup("myproc", pid=1234, cpuset="", cpu_limit=0.0, mem_limit=0)

    cgroup_dir = tmp_path / "myproc"
    assert cgroup_dir.is_dir()
    assert not (cgroup_dir / "cpuset.cpus").exists()
    assert not (cgroup_dir / "cpu.max").exists()
    assert not (cgroup_dir / "memory.max").exists()
    assert (cgroup_dir / "cgroup.procs").read_text() == "1234"


def test_cleanup_cgroup_removes_dir(tmp_path):
    """cleanup_cgroup removes the cgroup directory.

    On a real cgroupfs, os.rmdir works because the kernel removes pseudo-files.
    In tests we use an empty directory to simulate this behavior.
    """
    from dpm.agent.cgroups import cleanup_cgroup
    cgroup_dir = tmp_path / "myproc"
    cgroup_dir.mkdir()

    with patch("dpm.agent.cgroups.CGROUP_BASE", str(tmp_path)):
        cleanup_cgroup("myproc")

    assert not cgroup_dir.exists()


def test_cleanup_cgroup_nonexistent_is_noop(tmp_path):
    """cleanup_cgroup on nonexistent dir doesn't raise."""
    from dpm.agent.cgroups import cleanup_cgroup
    with patch("dpm.agent.cgroups.CGROUP_BASE", str(tmp_path)):
        cleanup_cgroup("nonexistent")  # should not raise


def test_setup_cgroup_cpu_limit_conversion(tmp_path):
    """Verify cpu_limit to cpu.max conversion: 2.0 cores = 200000 100000."""
    from dpm.agent.cgroups import setup_cgroup
    with patch("dpm.agent.cgroups.CGROUP_BASE", str(tmp_path)):
        setup_cgroup("myproc", pid=1, cpuset="", cpu_limit=2.0, mem_limit=0)

    assert (tmp_path / "myproc" / "cpu.max").read_text() == "200000 100000"
