"""Cgroups v2 management for DPM daemon process isolation.

Provides per-process cgroups with:
  - CPU affinity via cpuset.cpus
  - CPU bandwidth limits via cpu.max
  - Memory limits via memory.max
  - Exclusive core reservation (overlap validation) for isolated processes

Full kernel-level CPU partition isolation (cpuset.cpus.partition=isolated)
requires system-level configuration — either the `isolcpus` kernel parameter
or root-level cgroup partition setup.  When partition isolation is not
available, the daemon falls back to cpuset affinity, which combined with
realtime scheduling provides low-preemption execution in practice.
"""

import logging
import os

# cpu.max period (microseconds) — standard 100ms scheduling period
_CPU_PERIOD = 100_000

# Track which cores are currently reserved by isolated processes.
_isolated_cores: dict[str, set[int]] = {}  # proc_name -> set of core IDs

# Resolved at first use — the daemon's own cgroup directory (delegated by systemd).
CGROUP_BASE: str = ""


def _enable_subtree_controllers(base: str) -> bool:
    """Enable cpuset, cpu, and memory controllers for child cgroups.

    The kernel requires that no processes sit directly in a cgroup before
    controllers can be enabled on it.  We move the current process (the
    daemon) into a leaf child 'daemon' first, then write to subtree_control.

    Returns True if controllers were enabled successfully, False otherwise.
    """
    daemon_leaf = os.path.join(base, "daemon")
    try:
        os.makedirs(daemon_leaf, exist_ok=True)
        with open(os.path.join(daemon_leaf, "cgroup.procs"), "w") as f:
            f.write(str(os.getpid()))
    except OSError as e:
        logging.warning("Failed to move daemon PID to leaf cgroup: %s", e)
        return False

    ctrl_path = os.path.join(base, "cgroup.subtree_control")
    try:
        with open(ctrl_path, "w") as f:
            f.write("+cpuset +cpu +memory")
        logging.debug("Enabled subtree controllers at %s", base)
        return True
    except OSError as e:
        logging.warning("Failed to enable subtree controllers at %s: %s", base, e)
        return False


def _resolve_cgroup_base() -> str:
    """Detect the daemon's cgroup directory from /proc/self/cgroup.

    With systemd Delegate=yes, the daemon owns its cgroup subtree and can
    create child cgroups there.  Falls back to /sys/fs/cgroup/dpm for
    standalone (non-systemd) execution.
    """
    global CGROUP_BASE
    if CGROUP_BASE:
        return CGROUP_BASE

    try:
        with open("/proc/self/cgroup", "r") as f:
            for line in f:
                # cgroups v2 line: "0::<path>"
                parts = line.strip().split(":", 2)
                if len(parts) == 3 and parts[0] == "0":
                    rel = parts[2].lstrip("/")
                    candidate = os.path.join("/sys/fs/cgroup", rel)
                    if os.path.isdir(candidate) and os.access(candidate, os.W_OK):
                        if not _enable_subtree_controllers(candidate):
                            logging.warning(
                                "Cgroup base %s found but subtree controllers "
                                "could not be enabled; falling back.", candidate,
                            )
                            break
                        CGROUP_BASE = candidate
                        logging.info("Cgroup base (delegated): %s", CGROUP_BASE)
                        return CGROUP_BASE
    except OSError:
        pass

    # Fallback for standalone / dev execution
    fallback = "/sys/fs/cgroup/dpm"
    try:
        os.makedirs(fallback, exist_ok=True)
    except OSError:
        pass
    CGROUP_BASE = fallback
    logging.info("Cgroup base (fallback): %s", CGROUP_BASE)
    return CGROUP_BASE


def cgroups_available() -> bool:
    """Return True if cgroups v2 is available and the DPM subtree is writable."""
    try:
        base = _resolve_cgroup_base()
        return os.path.isdir(base) and os.access(base, os.W_OK)
    except OSError:
        return False


def _parse_cpuset(cpuset: str) -> set[int]:
    """Parse a cpuset string like '0,1,4-7' into a set of core IDs."""
    cores = set()
    for part in cpuset.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            cores.update(range(int(lo), int(hi) + 1))
        else:
            cores.add(int(part))
    return cores


def _check_overlap(name: str, cores: set[int]) -> None:
    """Raise ValueError if any cores overlap with existing isolated processes."""
    for other_name, other_cores in _isolated_cores.items():
        overlap = cores & other_cores
        if overlap:
            raise ValueError(
                f"Isolated cpuset for '{name}' overlaps with '{other_name}' "
                f"on cores {sorted(overlap)}"
            )


def setup_cgroup(
    name: str,
    pid: int,
    cpuset: str = "",
    cpu_limit: float = 0.0,
    mem_limit: int = 0,
    isolated: bool = False,
) -> None:
    """Create a cgroup for a process and apply resource limits.

    Args:
        name: Process name (used as cgroup directory name).
        pid: PID to place in the cgroup.
        cpuset: Comma-separated core IDs (e.g. "0,1"). Empty = no restriction.
        cpu_limit: CPU bandwidth in cores (e.g. 1.5). 0.0 = unlimited.
        mem_limit: Memory limit in bytes. 0 = unlimited.
        isolated: If True and cpuset is set, reserve cores exclusively for
                  this process (no other isolated process may use them).
                  Combined with realtime scheduling for low-preemption execution.

    Raises:
        OSError: If cgroup creation or writes fail.
        ValueError: If isolated cores overlap with another isolated process.
    """
    cgroup_dir = os.path.join(CGROUP_BASE, name)
    os.makedirs(cgroup_dir, exist_ok=True)

    if cpuset:
        if isolated:
            cores = _parse_cpuset(cpuset)
            _check_overlap(name, cores)
            _isolated_cores[name] = cores

        _write(cgroup_dir, "cpuset.cpus", cpuset)
        _write(cgroup_dir, "cpuset.mems", "0")

    if cpu_limit > 0:
        quota = int(cpu_limit * _CPU_PERIOD)
        _write(cgroup_dir, "cpu.max", f"{quota} {_CPU_PERIOD}")

    if mem_limit > 0:
        _write(cgroup_dir, "memory.max", str(mem_limit))

    _write(cgroup_dir, "cgroup.procs", str(pid))

    if cpuset and isolated:
        logging.info(
            "Cgroup: %s pinned to cores %s (isolated — exclusive reservation)",
            name, cpuset,
        )
    elif cpuset:
        logging.info("Cgroup: %s pinned to cores %s", name, cpuset)

    if cpu_limit > 0 or mem_limit > 0:
        logging.debug(
            "Cgroup: %s cpu_limit=%s mem_limit=%s",
            name, cpu_limit, mem_limit,
        )


def cleanup_cgroup(name: str) -> None:
    """Remove the cgroup directory for a process. Best-effort."""
    _isolated_cores.pop(name, None)

    cgroup_dir = os.path.join(CGROUP_BASE, name)
    if not os.path.isdir(cgroup_dir):
        return
    try:
        # Move any remaining PIDs to the daemon leaf before removing
        procs_file = os.path.join(cgroup_dir, "cgroup.procs")
        daemon_procs = os.path.join(CGROUP_BASE, "daemon", "cgroup.procs")
        if not os.path.exists(daemon_procs):
            daemon_procs = os.path.join(CGROUP_BASE, "cgroup.procs")
        try:
            with open(procs_file, "r") as f:
                pids = f.read().strip().split()
            for pid in pids:
                if pid:
                    try:
                        _write(os.path.dirname(daemon_procs), "cgroup.procs", pid)
                    except OSError:
                        pass
        except OSError:
            pass
        os.rmdir(cgroup_dir)
        logging.debug("Cgroup cleanup: removed %s", cgroup_dir)
    except OSError as e:
        logging.warning("Cgroup cleanup failed for %s: %s", name, e)


def _write(cgroup_dir: str, filename: str, value: str) -> None:
    """Write a value to a cgroup control file."""
    path = os.path.join(cgroup_dir, filename)
    with open(path, "w") as f:
        f.write(value)
