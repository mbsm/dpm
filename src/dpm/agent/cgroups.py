"""Cgroups v2 management for DPM agent process isolation."""

import logging
import os
import shutil

# Base path for the DPM cgroup subtree. Requires Delegate=yes in the
# systemd unit so the agent's user can create child cgroups here.
CGROUP_BASE = "/sys/fs/cgroup/dpm"

# cpu.max period (microseconds) — standard 100ms scheduling period
_CPU_PERIOD = 100_000


def cgroups_available() -> bool:
    """Return True if cgroups v2 is available and the DPM subtree is writable."""
    try:
        if not os.path.isdir(CGROUP_BASE):
            os.makedirs(CGROUP_BASE, exist_ok=True)
        return os.access(CGROUP_BASE, os.W_OK)
    except OSError:
        return False


def setup_cgroup(
    name: str,
    pid: int,
    cpuset: str = "",
    cpu_limit: float = 0.0,
    mem_limit: int = 0,
) -> None:
    """Create a cgroup for a process and apply resource limits.

    Args:
        name: Process name (used as cgroup directory name).
        pid: PID to place in the cgroup.
        cpuset: Comma-separated core IDs (e.g. "0,1"). Empty = no restriction.
        cpu_limit: CPU bandwidth in cores (e.g. 1.5). 0.0 = unlimited.
        mem_limit: Memory limit in bytes. 0 = unlimited.

    Raises:
        OSError: If cgroup creation or writes fail.
    """
    cgroup_dir = os.path.join(CGROUP_BASE, name)
    os.makedirs(cgroup_dir, exist_ok=True)

    if cpuset:
        _write(cgroup_dir, "cpuset.cpus", cpuset)

    if cpu_limit > 0:
        quota = int(cpu_limit * _CPU_PERIOD)
        _write(cgroup_dir, "cpu.max", f"{quota} {_CPU_PERIOD}")

    if mem_limit > 0:
        _write(cgroup_dir, "memory.max", str(mem_limit))

    _write(cgroup_dir, "cgroup.procs", str(pid))

    logging.debug(
        "Cgroup setup: %s pid=%d cpuset=%r cpu_limit=%s mem_limit=%s",
        name, pid, cpuset, cpu_limit, mem_limit,
    )


def cleanup_cgroup(name: str) -> None:
    """Remove the cgroup directory for a process. Best-effort."""
    cgroup_dir = os.path.join(CGROUP_BASE, name)
    if not os.path.isdir(cgroup_dir):
        return
    try:
        # Move any remaining PIDs to parent before removing
        procs_file = os.path.join(cgroup_dir, "cgroup.procs")
        if os.path.exists(procs_file):
            parent_procs = os.path.join(CGROUP_BASE, "cgroup.procs")
            try:
                with open(procs_file, "r") as f:
                    pids = f.read().strip().split()
                if pids and os.path.exists(parent_procs):
                    for pid in pids:
                        if pid:
                            try:
                                _write(CGROUP_BASE, "cgroup.procs", pid)
                            except OSError:
                                pass
            except OSError:
                pass
        shutil.rmtree(cgroup_dir)
        logging.debug("Cgroup cleanup: removed %s", cgroup_dir)
    except OSError as e:
        logging.warning("Cgroup cleanup failed for %s: %s", name, e)


def _write(cgroup_dir: str, filename: str, value: str) -> None:
    """Write a value to a cgroup control file."""
    path = os.path.join(cgroup_dir, filename)
    with open(path, "w") as f:
        f.write(value)
